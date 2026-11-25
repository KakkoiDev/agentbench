"""Hidden tests for task-scheduler: ordering rules (one worker), skips, cancels, dynamic tasks, real parallelism, speed."""
import random
import threading
import time

# ---- grading helpers (same in every agentic-v6 hidden test file) ----
import os, re as _re_mod, signal, subprocess, sys, textwrap, unittest


class Limited(unittest.TestCase):
    """Each test gets LIMIT seconds; a solution that hangs fails that test only, not the whole suite."""
    LIMIT = 30

    def setUp(self):
        def boom(*_):
            raise TimeoutError(f'test took over {self.LIMIT} s')
        signal.signal(signal.SIGALRM, boom)
        signal.alarm(self.LIMIT)

    def tearDown(self):
        signal.alarm(0)

    def timed(self, setup, work, budget, check=''):
        """Run setup (untimed), then work (timed), then check in a fresh interpreter in this folder.
        Fails if work takes over budget seconds; the process is killed at budget + 20 s."""
        prog = ('import sys, time\nsys.path.insert(0, %r)\n' % os.getcwd() + textwrap.dedent(setup)
                + '\n_t0 = time.perf_counter()\n' + textwrap.dedent(work)
                + '\n_elapsed = time.perf_counter() - _t0\n' + textwrap.dedent(check) + '\nprint("ELAPSED", _elapsed)\n')
        signal.alarm(0)
        try:
            r = subprocess.run([sys.executable, '-I', '-c', prog], capture_output=True, text=True, timeout=budget + 20)
        except subprocess.TimeoutExpired:
            self.fail(f'over {budget} s (stopped after {budget + 20} s)')
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        took = float(_re_mod.search(r'ELAPSED (\S+)', r.stdout)[1])
        sys.stderr.write(f'TIMING {self.id().split(".")[-1]} {took:.3f} {budget}\n')
        self.assertLessEqual(took, budget, f'took {took:.2f} s, the limit is {budget} s')


def banned_source(path, names):
    """Names from `names` that the solution file imports or mentions."""
    import ast
    src = open(path).read()
    found = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            found |= {a.name.split('.')[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split('.')[0])
    hits = {n for n in names if n in found}
    hits |= {s for s in ('__import__', 'importlib', 'sys.modules') if s in src}
    return sorted(hits)
# ---- end of helpers ----

from scheduler import CycleError, Scheduler


def go(test, s, timeout=30):
    """s.run() in a daemon thread (threads it starts are daemons too unless it says otherwise): a deadlock fails the test."""
    box = {}

    def target():
        try:
            box['r'] = s.run()
        except BaseException as e:     # noqa
            box['e'] = e
    th = threading.Thread(target=target, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        test.fail(f'run() did not finish within {timeout} s (deadlock?)')
    if 'e' in box:
        raise box['e']
    return box['r']


def rec(log, name, value=None, fail=0):
    def fn(ctx):
        log.append((name, ctx.attempt))
        if ctx.attempt <= fail:
            raise RuntimeError(f'{name} failed {ctx.attempt}')
        return value
    return fn


class Ordering(Limited):
    def test_results_and_return_values(self):
        s = Scheduler(workers=2)
        s.add('none', lambda ctx: None)
        s.add('num', lambda ctx: 42, deps=['none'])
        s.add('name', lambda ctx: ctx.name)
        self.assertEqual(go(self, s), {'none': ('ok', None), 'num': ('ok', 42), 'name': ('ok', 'name')})

    def test_priority_then_readiness(self):
        log = []
        s = Scheduler(workers=1)
        s.add('a', rec(log, 'a'))
        s.add('b', rec(log, 'b'), priority=5)
        s.add('c', rec(log, 'c'), deps=['a'], priority=9)
        s.add('d', rec(log, 'd'), deps=['a'])
        s.add('e', rec(log, 'e'))
        s.add('f', rec(log, 'f', fail=1), retries=2)
        r = go(self, s)
        self.assertEqual(log, [('b', 1), ('a', 1), ('c', 1), ('e', 1), ('f', 1), ('d', 1), ('f', 2)])
        self.assertEqual(r['f'], ('ok', None))

    def test_negative_priorities_and_add_order(self):
        log = []
        s = Scheduler(workers=1)
        s.add('low', rec(log, 'low'), priority=-1)
        s.add('x', rec(log, 'x'))
        s.add('y', rec(log, 'y'))
        s.add('high', rec(log, 'high'), priority=1)
        go(self, s)
        self.assertEqual([n for n, _ in log], ['high', 'x', 'y', 'low'])

    def test_unlocked_dependents_ready_in_add_order(self):
        log = []
        s = Scheduler(workers=1)
        s.add('z3', rec(log, 'z3'), deps=['root'])
        s.add('root', rec(log, 'root'))
        s.add('z1', rec(log, 'z1'), deps=['root'])
        s.add('other', rec(log, 'other'), priority=-1)
        s.add('z2', rec(log, 'z2'), deps=['root', 'other'])
        go(self, s)
        self.assertEqual([n for n, _ in log], ['root', 'z3', 'z1', 'other', 'z2'])

    def test_retry_goes_behind_ready_tasks(self):
        log = []
        s = Scheduler(workers=1)
        s.add('flaky', rec(log, 'flaky', value='done', fail=2), retries=2, priority=1)
        s.add('p1', rec(log, 'p1'), priority=1)
        s.add('p0', rec(log, 'p0'))
        r = go(self, s)
        self.assertEqual(log, [('flaky', 1), ('p1', 1), ('flaky', 2), ('flaky', 3), ('p0', 1)])
        self.assertEqual(r['flaky'], ('ok', 'done'))

    def test_error_after_all_retries(self):
        log = []
        s = Scheduler(workers=1)
        s.add('bad', rec(log, 'bad', fail=9), retries=2)
        s.add('dep', rec(log, 'dep'), deps=['bad'])
        r = go(self, s)
        self.assertEqual(r, {'bad': ('error', "RuntimeError('bad failed 3')"), 'dep': ('skipped', 'bad')})
        self.assertEqual(log, [('bad', 1), ('bad', 2), ('bad', 3)])

    def test_exceptions_are_results(self):
        def boom(ctx):
            raise ValueError('nope', 3)
        s = Scheduler(workers=2)
        s.add('v', boom)
        s.add('z', lambda ctx: 1 / 0)
        r = go(self, s)
        self.assertEqual(r, {'v': ('error', "ValueError('nope', 3)"), 'z': ('error', "ZeroDivisionError('division by zero')")})


class Skipping(Limited):
    def test_skips_propagate_with_reasons(self):
        log = []
        s = Scheduler(workers=1)
        s.add('ok1', rec(log, 'ok1'))
        s.add('bad', rec(log, 'bad', fail=1))
        s.add('mid', rec(log, 'mid'), deps=['ok1', 'bad'])
        s.add('leaf', rec(log, 'leaf'), deps=['mid'])
        s.add('leaf2', rec(log, 'leaf2'), deps=['ok1', 'leaf'])
        s.add('fine', rec(log, 'fine'), deps=['ok1'])
        r = go(self, s)
        self.assertEqual(r['mid'], ('skipped', 'bad'))
        self.assertEqual(r['leaf'], ('skipped', 'mid'))
        self.assertEqual(r['leaf2'], ('skipped', 'leaf'))
        self.assertEqual(r['fine'], ('ok', None))
        self.assertNotIn('mid', [n for n, _ in log])

    def test_reason_is_the_first_to_fail(self):
        log = []
        s = Scheduler(workers=1)
        s.add('late', rec(log, 'late', fail=1))
        s.add('early', rec(log, 'early', fail=1), priority=3)
        s.add('both', rec(log, 'both'), deps=['late', 'early'])
        r = go(self, s)
        self.assertEqual(r['both'], ('skipped', 'early'))


class Cancelling(Limited):
    def test_cancel_before_run(self):
        log = []
        s = Scheduler(workers=1)
        s.add('a', rec(log, 'a'))
        s.add('b', rec(log, 'b'), deps=['a'])
        s.add('c', rec(log, 'c'))
        self.assertTrue(s.cancel('a'))
        self.assertFalse(s.cancel('a'))
        self.assertFalse(s.cancel('nope'))
        r = go(self, s)
        self.assertEqual(r, {'a': ('cancelled', None), 'b': ('skipped', 'a'), 'c': ('ok', None)})
        self.assertEqual(log, [('c', 1)])

    def test_cancel_from_a_task(self):
        seen = {}
        s = Scheduler(workers=1)

        def first(ctx):
            seen['pending'] = ctx.cancel('later')
            seen['self'] = ctx.cancel('first')
            seen['unknown'] = ctx.cancel('ghost')
            return 1

        def second(ctx):
            seen['done'] = ctx.cancel('first')
            return 2
        s.add('first', first, priority=2)
        s.add('second', second, priority=1)
        s.add('later', lambda ctx: 3)
        s.add('after_later', lambda ctx: 4, deps=['later'])
        r = go(self, s)
        self.assertEqual(seen, {'pending': True, 'self': False, 'unknown': False, 'done': False})
        self.assertEqual(r, {'first': ('ok', 1), 'second': ('ok', 2), 'later': ('cancelled', None), 'after_later': ('skipped', 'later')})


class Dynamic(Limited):
    def test_tasks_added_while_running(self):
        log = []
        errors = []
        s = Scheduler(workers=1)

        def parent(ctx):
            log.append('parent')
            ctx.add('child', rec(log, 'child', value='c'), deps=['parent_dep'], priority=5)
            ctx.add('after_bad', rec(log, 'after_bad'), deps=['bad', 'parent_dep'])
            ctx.add('waits', rec(log, 'waits'), deps=['parent'])
            for bad_args in (('x', ['ghost']), ('child', [])):
                try:
                    ctx.add(bad_args[0], rec(log, 'x'), deps=bad_args[1])
                except ValueError:
                    errors.append(bad_args[0])
            return 'p'
        s.add('parent_dep', rec(log, 'parent_dep'), priority=9)
        s.add('bad', rec(log, 'bad', fail=1), priority=8)
        s.add('parent', parent, deps=['parent_dep'])
        s.add('other', rec(log, 'other'))
        r = go(self, s)
        self.assertEqual(errors, ['x', 'child'])
        self.assertEqual(r['child'], ('ok', 'c'))
        self.assertEqual(r['after_bad'], ('skipped', 'bad'))
        self.assertEqual(r['waits'], ('ok', None))
        self.assertEqual(r['parent'], ('ok', 'p'))
        self.assertNotIn('x', r)
        # parent became ready after 'other' (when parent_dep ended); child (priority 5) was ready at once
        self.assertEqual([e if isinstance(e, str) else e[0] for e in log], ['parent_dep', 'bad', 'other', 'parent', 'child', 'waits'])

    def test_add_after_run_started_and_bad_arguments(self):
        s = Scheduler(workers=1)
        s.add('a', lambda ctx: 1)
        with self.assertRaises(ValueError):
            s.add('a', lambda ctx: 2)
        for bad in (lambda: Scheduler(workers=0), lambda: s.limit('g', 0)):
            with self.assertRaises(ValueError):
                bad()
        go(self, s)
        with self.assertRaises(RuntimeError):
            s.add('b', lambda ctx: 1)


class Graph(Limited):
    def test_unknown_dependency(self):
        log = []
        s = Scheduler(workers=2)
        s.add('a', rec(log, 'a'))
        s.add('b', rec(log, 'b'), deps=['nope'])
        with self.assertRaises(ValueError):
            go(self, s)
        self.assertEqual(log, [])

    def test_cycles(self):
        for edges in ({'a': ['b'], 'b': ['c'], 'c': ['a'], 'd': []}, {'x': ['x']}, {'p': [], 'q': ['p', 'r'], 'r': ['s'], 's': ['q']}):
            log = []
            s = Scheduler(workers=2)
            for n, deps in edges.items():
                s.add(n, rec(log, n), deps=deps)
            with self.assertRaises(CycleError) as cm:
                go(self, s)
            self.assertIsInstance(cm.exception, ValueError)
            cyc = cm.exception.cycle
            self.assertGreaterEqual(len(cyc), 2)
            self.assertEqual(cyc[0], cyc[-1])
            for a, b in zip(cyc, cyc[1:]):
                self.assertIn(b, edges[a], cyc)
            self.assertEqual(log, [])


class Parallel(Limited):
    LIMIT = 60

    def test_tasks_really_run_in_parallel(self):
        barrier = threading.Barrier(3, timeout=10)
        s = Scheduler(workers=3)
        for i in range(3):
            s.add(f't{i}', lambda ctx: barrier.wait())
        r = go(self, s)
        self.assertTrue(all(v[0] == 'ok' for v in r.values()), r)

    def test_never_more_than_workers(self):
        lock = threading.Lock()
        state = {'now': 0, 'max': 0}

        def work(ctx):
            with lock:
                state['now'] += 1
                state['max'] = max(state['max'], state['now'])
            time.sleep(0.01)
            with lock:
                state['now'] -= 1
        s = Scheduler(workers=4)
        for i in range(40):
            s.add(f'w{i}', work)
        go(self, s)
        self.assertLessEqual(state['max'], 4)
        self.assertGreaterEqual(state['max'], 2)

    def test_group_limits(self):
        lock = threading.Lock()
        state = {}

        def work(group):
            def fn(ctx):
                with lock:
                    now = state.get(group, 0) + 1
                    state[group] = now
                    state['max_' + str(group)] = max(state.get('max_' + str(group), 0), now)
                time.sleep(0.01)
                with lock:
                    state[group] -= 1
            return fn
        s = Scheduler(workers=6)
        s.limit('db', 1)
        s.limit('net', 2)
        for i in range(8):
            s.add(f'db{i}', work('db'), group='db', priority=1)
            s.add(f'net{i}', work('net'), group='net')
            s.add(f'cpu{i}', work(None))
        r = go(self, s)
        self.assertEqual(len(r), 24)
        self.assertEqual((state['max_db'], state['max_net']), (1, 2))
        self.assertGreaterEqual(state['max_None'], 2)

    def test_full_group_does_not_block_others(self):
        release = threading.Event()
        s = Scheduler(workers=2)
        s.limit('g', 1)
        s.add('g1', lambda ctx: release.wait(10) or 'timeout', group='g', priority=5)
        s.add('g2', lambda ctx: 'g2', group='g', priority=5)
        s.add('x', lambda ctx: release.set())
        r = go(self, s)
        self.assertEqual(r, {'g1': ('ok', True), 'g2': ('ok', 'g2'), 'x': ('ok', None)})

    def test_dependencies_respected_under_concurrency(self):
        rnd = random.Random(5)
        lock = threading.Lock()
        ended, problems = set(), []
        deps = {}
        s = Scheduler(workers=8)
        for i in range(300):
            ds = [f'n{j}' for j in rnd.sample(range(i), min(i, rnd.randint(0, 3)))]
            deps[f'n{i}'] = ds

            def fn(ctx, ds=ds):
                with lock:
                    missing = [d for d in ds if d not in ended]
                    if missing:
                        problems.append((ctx.name, missing))
                time.sleep(rnd.random() * 0.002)
                with lock:
                    ended.add(ctx.name)
                return len(ds)
            s.add(f'n{i}', fn, deps=ds, priority=rnd.randint(0, 3))
        r = go(self, s)
        self.assertEqual(problems, [])
        self.assertEqual(r, {n: ('ok', len(d)) for n, d in deps.items()})


class Speed(Limited):
    LIMIT = 120

    def test_20000_small_tasks(self):
        setup = '''
import random, threading
from scheduler import Scheduler
rnd = random.Random(3)
s = Scheduler(workers=4)
s.limit('g', 2)
for i in range(20000):
    deps = [f't{j}' for j in {rnd.randrange(i) for _ in range(2)}] if i > 10 else []
    s.add(f't{i}', lambda ctx: 1, deps=deps, priority=rnd.randint(0, 5), group='g' if i % 7 == 0 else None)
box = {}
th = threading.Thread(target=lambda: box.setdefault('r', s.run()), daemon=True)
'''
        work = '''
th.start()
th.join(60)
'''
        check = '''
assert not th.is_alive(), 'did not finish'
assert len(box['r']) == 20000 and all(v == ('ok', 1) for v in box['r'].values())
'''
        self.timed(setup, work, 8, check)


if __name__ == '__main__':
    unittest.main()
