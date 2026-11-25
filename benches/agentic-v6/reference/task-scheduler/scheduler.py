"""A dependency-aware thread pool with priorities, per-group limits, retries and cancellation."""
import heapq
import threading


class CycleError(ValueError):
    def __init__(self, cycle):
        super().__init__('dependency cycle: ' + ' -> '.join(cycle))
        self.cycle = cycle


class _Task:
    __slots__ = ('name', 'fn', 'deps', 'priority', 'group', 'retries', 'order', 'state', 'result', 'waiting',
                 'dependents', 'attempt')

    def __init__(self, name, fn, deps, priority, group, retries, order):
        self.name, self.fn, self.deps, self.priority, self.group, self.retries = name, fn, tuple(deps), priority, group, retries
        self.order = order
        self.state = 'pending'     # pending, ready, running, done
        self.result = None
        self.waiting = 0
        self.dependents = []
        self.attempt = 0


class Context:
    def __init__(self, sched, task):
        self._s, self._t = sched, task
        self.name = task.name
        self.attempt = task.attempt

    def add(self, name, fn, deps=(), priority=0, group=None, retries=0):
        self._s._add_running(name, fn, deps, priority, group, retries)

    def cancel(self, name):
        return self._s.cancel(name)


class Scheduler:
    def __init__(self, workers=1):
        if workers < 1:
            raise ValueError('workers must be >= 1')
        self.workers = workers
        self._tasks = {}
        self._limits = {}
        self._cv = threading.Condition()
        self._ready = []            # (-priority, seq, name)
        self._seq = 0
        self._running = 0
        self._group_running = {}
        self._unfinished = 0
        self._started = False

    def limit(self, group, n):
        if n < 1:
            raise ValueError('limit must be >= 1')
        with self._cv:
            self._limits[group] = n
            self._cv.notify_all()

    def add(self, name, fn, deps=(), priority=0, group=None, retries=0):
        with self._cv:
            if self._started:
                raise RuntimeError('use the context to add tasks while running')
            if name in self._tasks:
                raise ValueError(f'duplicate task {name!r}')
            self._tasks[name] = _Task(name, fn, deps, priority, group, retries, len(self._tasks))

    # ---- state changes (called with the lock held)
    def _make_ready(self, t):
        t.state = 'ready'
        self._seq += 1
        heapq.heappush(self._ready, (-t.priority, self._seq, t.name))

    def _finish(self, t, result):
        """t ends with result; unlock or skip its dependents (in the order they were added)."""
        stack = [(t, result)]
        while stack:
            t, result = stack.pop(0)
            if t.state == 'done':
                continue
            t.state, t.result = 'done', result
            self._unfinished -= 1
            ok = result[0] == 'ok'
            for d in sorted(t.dependents, key=lambda x: x.order):
                if d.state != 'pending':
                    continue
                if ok:
                    d.waiting -= 1
                    if d.waiting == 0:
                        self._make_ready(d)
                else:
                    stack.append((d, ('skipped', t.name)))
        self._cv.notify_all()

    def _add_running(self, name, fn, deps, priority, group, retries):
        with self._cv:
            if name in self._tasks:
                raise ValueError(f'duplicate task {name!r}')
            missing = [d for d in deps if d not in self._tasks]
            if missing:
                raise ValueError(f'unknown dependency {missing[0]!r}')
            t = _Task(name, fn, deps, priority, group, retries, len(self._tasks))
            self._tasks[name] = t
            self._unfinished += 1
            failed = None
            for d in t.deps:
                dt = self._tasks[d]
                if dt.state == 'done':
                    if dt.result[0] != 'ok' and failed is None:
                        failed = d
                else:
                    t.waiting += 1
                    dt.dependents.append(t)
            if failed is not None:
                self._finish(t, ('skipped', failed))
            elif t.waiting == 0:
                self._make_ready(t)
                self._cv.notify_all()

    def cancel(self, name):
        with self._cv:
            t = self._tasks.get(name)
            if t is None or t.state not in ('pending', 'ready'):
                return False
            if not self._started:
                t.state = 'cancelled-before-run'
                return True
            self._finish(t, ('cancelled', None))
            return True

    # ---- running
    def _check_graph(self):
        for t in self._tasks.values():
            for d in t.deps:
                if d not in self._tasks:
                    raise ValueError(f'unknown dependency {d!r} of {t.name!r}')
        color = {}
        for root in self._tasks:
            if root in color:
                continue
            stack = [(root, iter(self._tasks[root].deps))]
            path = [root]
            color[root] = 1
            while stack:
                node, it = stack[-1]
                nxt = next(it, None)
                if nxt is None:
                    color[node] = 2
                    stack.pop()
                    path.pop()
                    continue
                c = color.get(nxt)
                if c == 1:
                    cyc = path[path.index(nxt):] + [nxt]
                    raise CycleError(cyc)
                if c is None:
                    color[nxt] = 1
                    path.append(nxt)
                    stack.append((nxt, iter(self._tasks[nxt].deps)))

    def _next(self):
        """The best ready task whose group has room, or None."""
        skipped = []
        found = None
        while self._ready:
            item = heapq.heappop(self._ready)
            t = self._tasks[item[2]]
            if t.state != 'ready':
                continue
            lim = self._limits.get(t.group)
            if t.group is not None and lim is not None and self._group_running.get(t.group, 0) >= lim:
                skipped.append(item)
                continue
            found = t
            break
        for item in skipped:
            heapq.heappush(self._ready, item)
        return found

    def _worker(self):
        while True:
            with self._cv:
                while True:
                    if self._unfinished == 0:
                        self._cv.notify_all()
                        return
                    t = self._next()
                    if t is not None:
                        break
                    self._cv.wait()
                t.state = 'running'
                t.attempt += 1
                self._running += 1
                if t.group is not None:
                    self._group_running[t.group] = self._group_running.get(t.group, 0) + 1
                ctx = Context(self, t)
            try:
                value = t.fn(ctx)
                outcome = ('ok', value)
            except Exception as e:      # noqa: BLE001 - a task's failure is its result
                outcome = ('error', repr(e))
            with self._cv:
                self._running -= 1
                if t.group is not None:
                    self._group_running[t.group] -= 1
                if outcome[0] == 'error' and t.attempt <= t.retries:
                    self._make_ready(t)
                    self._cv.notify_all()
                else:
                    self._finish(t, outcome)

    def run(self):
        with self._cv:
            if self._started:
                raise RuntimeError('run() can be called once')
            self._check_graph()
            self._started = True
            pre_cancelled = [t for t in self._tasks.values() if t.state == 'cancelled-before-run']
            self._unfinished = len(self._tasks)
            for t in self._tasks.values():
                if t.state == 'cancelled-before-run':
                    t.state = 'pending'
                for d in t.deps:
                    self._tasks[d].dependents.append(t)
                t.waiting = len(t.deps)
            for t in pre_cancelled:
                self._finish(t, ('cancelled', None))
            for t in sorted(self._tasks.values(), key=lambda x: x.order):
                if t.state == 'pending' and t.waiting == 0:
                    self._make_ready(t)
        threads = [threading.Thread(target=self._worker, daemon=True) for _ in range(self.workers)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        return {name: t.result for name, t in self._tasks.items()}
