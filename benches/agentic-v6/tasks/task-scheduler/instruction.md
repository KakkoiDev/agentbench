Write `scheduler.py`: a dependency-aware task runner on a pool of threads (Python 3 standard library only).

## API

```python
s = Scheduler(workers=3)            # at most 3 tasks run at the same time (workers >= 1, else ValueError)
s.add(name, fn, deps=(), priority=0, group=None, retries=0)
s.limit(group, n)                   # at most n tasks of that group run at the same time (n >= 1, else ValueError)
s.cancel(name) -> bool
results = s.run()                   # blocks until every task has ended; returns {name: (status, value)}
```

Each task function is called as `fn(ctx)` in a worker thread. `ctx.name` is the task's name, `ctx.attempt` the attempt number (1, 2, ...), `ctx.add(...)` adds a task while running (same arguments as `Scheduler.add`), and `ctx.cancel(name)` cancels a task (same as `Scheduler.cancel`).

## Results

`run()` returns a dict with every task (also those added while running), mapping its name to:
- `('ok', return value)`;
- `('error', repr(exception))` when every attempt raised an `Exception` (a task gets `1 + retries` attempts);
- `('skipped', dep_name)` when a dependency did not end `'ok'` (the task never runs): `dep_name` is the dependency whose unsuccessful end caused the skip, which is the first of its dependencies to end unsuccessfully (for a task added while running whose dependencies have already ended unsuccessfully: the first such in its `deps` order). Skipping propagates: dependents of a skipped task are skipped with that task's name;
- `('cancelled', None)` when cancelled before it started (its dependents are skipped with its name).

## Rules

- Names are unique: adding a name that exists raises `ValueError` (from `add` and `ctx.add`). Before `run()`, `deps` may name tasks added later; `run()` raises `ValueError` if a dependency does not exist and `CycleError` (a `ValueError` subclass defined in scheduler.py, with attribute `cycle`: a list of names `[n1, n2, ..., n1]` where each depends on the next) if dependencies form a cycle; in both cases no task runs. `ctx.add` requires its dependencies to exist already (else `ValueError`, raised in the calling task). `Scheduler.add` after `run()` has started raises `RuntimeError`; `run()` can be called once.
- A task becomes **ready** when all its dependencies have ended `'ok'` (immediately if it has none). At the start of `run()`, the tasks without dependencies become ready in the order they were added; when a task ends `'ok'`, the dependents it unlocks become ready in the order they were added; a task added while running becomes ready at once if its dependencies already ended `'ok'` (or is skipped at once if one ended otherwise).
- A free worker always starts the best ready task whose group has room: the highest `priority`, then the one that became ready first. A ready task whose group is at its limit is passed over (it does not block the others). Tasks with `group=None` are never limited. Workers never sit idle while some ready task could start.
- A failed attempt with retries left makes the task ready again at that moment (behind tasks that were already ready with the same priority).
- `cancel(name)` cancels a task that has not started yet (before `run()` or while running; pending or ready) and returns `True`; for a running, ended or unknown task it returns `False` and changes nothing.
- Tasks really run in parallel up to `workers` (tasks may wait for each other, e.g. with a `threading.Barrier`, so running them one at a time can deadlock). Never more than `workers` at once, never more than a group's limit at once.
- Scheduling overhead must stay small: 20,000 tiny tasks with dependencies between them must finish in a few seconds (do not scan all tasks for every decision).

Make sure `python3 -m unittest` passes.
