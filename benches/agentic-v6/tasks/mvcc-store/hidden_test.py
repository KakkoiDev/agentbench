"""Hidden tests for mvcc-store: the rules one by one, random interleavings against a model, threads, speed."""
import random
import threading

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

# ---- a slow, simple model of the specification, for random operation sequences ----
_GONE = object()


class ModelConflict(Exception):
    def __init__(self, key):
        self.key = key


class ModelStore:
    def __init__(self):
        self.versions = {}   # key -> list of (ts, value or _GONE), ascending
        self.last = 0
        self.active = {}     # txn number -> snapshot
        self.commits = []    # (ts, set of keys)

    def visible(self, key, snap):
        best = _GONE
        for ts, v in self.versions.get(key, []):
            if ts <= snap:
                best = v
        return best

    def gc(self):
        m = min(self.active.values()) if self.active else self.last
        removed = 0
        for k in list(self.versions):
            vs = self.versions[k]
            older = [i for i, (ts, _) in enumerate(vs) if ts <= m]
            if not older:
                continue
            i = older[-1]
            cut = i + 1 if vs[i][1] is _GONE else i
            removed += cut
            self.versions[k] = vs[cut:]
            if not self.versions[k]:
                del self.versions[k]
        return removed

    def count(self):
        return sum(len(v) for v in self.versions.values())


class ModelTxn:
    def __init__(self, store, n, serializable):
        self.s, self.n, self.ser = store, n, serializable
        self.snap = store.last
        store.active[n] = self.snap
        self.writes, self.reads, self.ranges, self.sps, self.next_sp, self.open = {}, set(), [], [], 1, True

    def get(self, k):
        self.reads.add(k)
        v = self.writes[k] if k in self.writes else self.s.visible(k, self.snap)
        return None if v is _GONE else v

    def scan(self, lo, hi):
        self.ranges.append((lo, hi))
        keys = set(self.s.versions) | set(self.writes)
        out = []
        for k in sorted(keys):
            if (lo is None or k >= lo) and (hi is None or k < hi):
                v = self.writes[k] if k in self.writes else self.s.visible(k, self.snap)
                if v is not _GONE:
                    out.append((k, v))
        return out

    def savepoint(self):
        sp = self.next_sp
        self.next_sp += 1
        self.sps.append((sp, dict(self.writes)))
        return sp

    def rollback_to(self, sp):
        i = [s for s, _ in self.sps].index(sp)
        self.writes = dict(self.sps[i][1])
        del self.sps[i + 1:]

    def release(self, sp):
        i = [s for s, _ in self.sps].index(sp)
        del self.sps[i:]

    def commit(self):
        self.open = False
        del self.s.active[self.n]
        touched = set()
        for ts, keys in self.s.commits:
            if ts > self.snap:
                touched |= keys
        bad = [k for k in self.writes if k in touched]
        if self.ser:
            bad += [k for k in self.reads if k in touched]
            bad += [k for k in touched for lo, hi in self.ranges if (lo is None or k >= lo) and (hi is None or k < hi)]
        if bad:
            raise ModelConflict(min(bad))
        if not self.writes:
            return None
        self.s.last += 1
        for k, v in self.writes.items():
            self.s.versions.setdefault(k, []).append((self.s.last, v))
        self.s.commits.append((self.s.last, set(self.writes)))
        return self.s.last

    def abort(self):
        self.open = False
        del self.s.active[self.n]
# ---- end of model ----

from mvcc import Conflict, InvalidSavepoint, Store, TxnClosed


def committed(store, **kv):
    t = store.begin()
    for k, v in kv.items():
        if v is None:
            t.delete(k)
        else:
            t.put(k, v)
    return t.commit()


def conflict_key(test, txn):
    with test.assertRaises(Conflict) as cm:
        txn.commit()
    return cm.exception.key


class Visibility(Limited):
    def test_read_your_own_writes_only(self):
        s = Store()
        a, b = s.begin(), s.begin()
        a.put('x', 1)
        self.assertEqual(a.get('x'), 1)
        self.assertIsNone(b.get('x'))
        self.assertIsNone(s.get('x'))
        a.delete('x')
        self.assertIsNone(a.get('x'))
        a.put('x', 3)
        a.commit()
        self.assertEqual(s.get('x'), 3)
        self.assertIsNone(b.get('x'))

    def test_snapshot_taken_at_begin(self):
        s = Store()
        committed(s, a=1, b=1)
        r = s.begin()
        self.assertEqual(r.snapshot, 1)
        committed(s, a=2)
        committed(s, b=None)
        self.assertEqual((r.get('a'), r.get('b')), (1, 1))
        self.assertEqual(r.scan(), [('a', 1), ('b', 1)])
        r2 = s.begin()
        self.assertEqual((r2.snapshot, r2.get('a'), r2.get('b')), (3, 2, None))

    def test_timestamps_and_read_only_commits(self):
        s = Store()
        self.assertEqual(committed(s, a=1), 1)
        r = s.begin()
        r.get('a')
        self.assertIsNone(r.commit())
        self.assertEqual(committed(s, b=1), 2)
        t = s.begin()
        t.put('c', 1)
        t.delete('c')
        self.assertEqual(t.commit(), 3)   # a delete of a missing key is a write
        self.assertEqual(s.version_count(), 3)

    def test_values_stored_as_given_and_none_rejected(self):
        s = Store()
        t = s.begin()
        obj = {'deep': [1, 2]}
        t.put('o', obj)
        t.put('f', 0)
        t.put('e', '')
        t.commit()
        self.assertIs(s.get('o'), obj)
        self.assertEqual((s.get('f'), s.get('e')), (0, ''))
        with self.assertRaises(ValueError):
            s.begin().put('x', None)


class Scans(Limited):
    def test_bounds_and_order(self):
        s = Store()
        committed(s, b=2, d=4, a=1, c=3, e=5)
        t = s.begin()
        self.assertEqual(t.scan(), [('a', 1), ('b', 2), ('c', 3), ('d', 4), ('e', 5)])
        self.assertEqual(t.scan('b', 'd'), [('b', 2), ('c', 3)])
        self.assertEqual(t.scan('bb'), [('c', 3), ('d', 4), ('e', 5)])
        self.assertEqual(t.scan(None, 'c'), [('a', 1), ('b', 2)])
        self.assertEqual(t.scan('d', 'b'), [])
        self.assertEqual(t.scan('c', 'c'), [])

    def test_own_writes_merged(self):
        s = Store()
        committed(s, a=1, c=3, e=5)
        t = s.begin()
        t.put('b', 20)
        t.delete('c')
        t.put('e', 50)
        t.put('z', 0)
        self.assertEqual(t.scan('a', 'f'), [('a', 1), ('b', 20), ('e', 50)])
        committed(s, d=4)
        self.assertEqual(t.scan(), [('a', 1), ('b', 20), ('e', 50), ('z', 0)])

    def test_committed_deletes_hidden(self):
        s = Store()
        committed(s, a=1, b=2)
        committed(s, a=None)
        self.assertEqual(s.begin().scan(), [('b', 2)])


class WriteConflicts(Limited):
    def test_first_committer_wins(self):
        s = Store()
        a, b = s.begin(), s.begin()
        a.put('k', 'a')
        b.put('k', 'b')
        self.assertEqual(a.commit(), 1)
        self.assertEqual(conflict_key(self, b), 'k')
        self.assertEqual(s.get('k'), 'a')
        self.assertEqual(committed(s, z=1), 2)    # the failed commit used no timestamp

    def test_smallest_conflicting_key(self):
        s = Store()
        a, b = s.begin(), s.begin()
        for k in ('q', 'm', 'x'):
            a.put(k, 1)
            b.put(k, 2)
        b.put('a', 2)
        a.commit()
        self.assertEqual(conflict_key(self, b), 'm')
        self.assertIsNone(s.get('a'))

    def test_deletes_conflict_too(self):
        s = Store()
        committed(s, k=1)
        a, b = s.begin(), s.begin()
        a.delete('k')
        b.delete('k')
        a.commit()
        self.assertEqual(conflict_key(self, b), 'k')
        c, d = s.begin(), s.begin()
        c.delete('ghost')
        d.put('ghost', 1)
        c.commit()
        self.assertEqual(conflict_key(self, d), 'ghost')

    def test_disjoint_writes_and_earlier_commits_are_fine(self):
        s = Store()
        committed(s, k=1)
        a = s.begin()
        a.put('k', 2)
        b = s.begin()
        b.put('j', 1)
        self.assertEqual(b.commit(), 2)
        self.assertEqual(a.commit(), 3)

    def test_rolled_back_write_does_not_conflict(self):
        s = Store()
        a, b = s.begin(), s.begin()
        sp = b.savepoint()
        b.put('k', 1)
        b.rollback_to(sp)
        b.put('j', 1)
        a.put('k', 2)
        a.commit()
        self.assertEqual(b.commit(), 2)

    def test_snapshot_isolation_allows_write_skew(self):
        s = Store()
        committed(s, alice='on', bob='on')
        a, b = s.begin(), s.begin()
        if a.get('bob') == 'on':
            a.put('alice', 'off')
        if b.get('alice') == 'on':
            b.put('bob', 'off')
        self.assertEqual((a.commit(), b.commit()), (2, 3))


class Serializable(Limited):
    def test_write_skew_prevented(self):
        s = Store()
        committed(s, alice='on', bob='on')
        a, b = s.begin(serializable=True), s.begin(serializable=True)
        a.get('bob')
        a.put('alice', 'off')
        b.get('alice')
        b.put('bob', 'off')
        a.commit()
        self.assertEqual(conflict_key(self, b), 'alice')

    def test_missing_key_reads_count(self):
        s = Store()
        t = s.begin(serializable=True)
        self.assertIsNone(t.get('new'))
        t.put('other', 1)
        committed(s, new=1)
        self.assertEqual(conflict_key(self, t), 'new')

    def test_own_written_key_read_counts(self):
        s = Store()
        t = s.begin(serializable=True)
        t.put('k', 1)
        t.get('k')
        sp = t.savepoint()
        t.put('j', 1)
        t.rollback_to(sp)
        t.delete('k')
        committed(s, k=5)
        self.assertEqual(conflict_key(self, t), 'k')

    def test_phantoms_in_scanned_ranges(self):
        s = Store()
        committed(s, b=1)
        t = s.begin(serializable=True)
        t.scan('a', 'c')
        t.put('x', 1)
        committed(s, d=1, c=1)       # outside the range ('c' too: hi is exclusive): fine
        self.assertEqual(t.commit(), 3)
        u = s.begin(serializable=True)
        u.scan('a', 'c')
        u.put('y', 1)
        committed(s, bb=1)           # did not exist when u scanned
        self.assertEqual(conflict_key(self, u), 'bb')

    def test_unbounded_scan(self):
        s = Store()
        t = s.begin(serializable=True)
        t.scan()
        t.put('m', 1)
        committed(s, zzz=1)
        self.assertEqual(conflict_key(self, t), 'zzz')

    def test_read_only_serializable_is_validated(self):
        s = Store()
        committed(s, a=1)
        t = s.begin(serializable=True)
        t.get('a')
        committed(s, a=2)
        self.assertEqual(conflict_key(self, t), 'a')
        u = s.begin(serializable=True)
        u.get('a')
        self.assertIsNone(u.commit())

    def test_reads_do_not_matter_without_serializable(self):
        s = Store()
        t = s.begin()
        t.get('a')
        t.scan()
        t.put('b', 1)
        committed(s, a=1)
        self.assertEqual(t.commit(), 2)

    def test_smallest_key_over_all_reasons(self):
        s = Store()
        t = s.begin(serializable=True)
        t.put('m', 1)
        t.scan('p', 'r')
        t.get('z')
        committed(s, z=1, q=1, m=1)
        self.assertEqual(conflict_key(self, t), 'm')


class Savepoints(Limited):
    def test_numbering_and_rollback(self):
        s = Store()
        committed(s, a=1)
        t = s.begin()
        t.put('a', 2)
        sp1 = t.savepoint()
        t.put('a', 3)
        t.put('b', 3)
        sp2 = t.savepoint()
        t.delete('a')
        self.assertEqual((sp1, sp2), (1, 2))
        t.rollback_to(sp2)
        self.assertEqual((t.get('a'), t.get('b')), (3, 3))
        t.rollback_to(sp1)
        self.assertEqual((t.get('a'), t.get('b')), (2, None))
        with self.assertRaises(InvalidSavepoint):
            t.rollback_to(sp2)
        t.put('c', 1)
        t.rollback_to(sp1)          # still usable
        self.assertIsNone(t.get('c'))
        self.assertEqual(t.savepoint(), 3)
        t.commit()
        self.assertEqual((s.get('a'), s.get('b'), s.get('c')), (2, None, None))

    def test_release(self):
        s = Store()
        t = s.begin()
        sp1 = t.savepoint()
        t.put('a', 1)
        sp2 = t.savepoint()
        t.put('b', 1)
        sp3 = t.savepoint()
        t.release(sp2)
        self.assertEqual((t.get('a'), t.get('b')), (1, 1))
        for sp in (sp2, sp3, 99):
            with self.assertRaises(InvalidSavepoint):
                t.rollback_to(sp)
            with self.assertRaises(InvalidSavepoint):
                t.release(sp)
        t.rollback_to(sp1)
        self.assertIsNone(t.commit())

    def test_rollback_everything_makes_a_read_only_commit(self):
        s = Store()
        t = s.begin()
        sp = t.savepoint()
        t.put('a', 1)
        t.delete('b')
        t.rollback_to(sp)
        self.assertIsNone(t.commit())
        self.assertEqual(committed(s, a=1), 1)


class Closing(Limited):
    def test_closed_transactions(self):
        s = Store()
        a = s.begin()
        a.put('x', 1)
        a.commit()
        b = s.begin()
        b.abort()
        c, d = s.begin(), s.begin()
        c.put('k', 1)
        d.put('k', 2)
        c.commit()
        with self.assertRaises(Conflict):
            d.commit()
        for t in (a, b, d):
            for call in (lambda: t.get('x'), lambda: t.put('x', 1), lambda: t.delete('x'), lambda: t.scan(),
                         t.savepoint, t.commit, t.abort):
                with self.assertRaises(TxnClosed):
                    call()


class GarbageCollection(Limited):
    def test_collapses_without_active_transactions(self):
        s = Store()
        for i in range(5):
            committed(s, a=i, b=i)
        committed(s, b=None)
        committed(s, c=None)
        self.assertEqual(s.version_count(), 12)
        self.assertEqual(s.gc(), 11)
        self.assertEqual(s.version_count(), 1)
        self.assertEqual((s.get('a'), s.get('b'), s.get('c')), (4, None, None))
        self.assertEqual(s.gc(), 0)

    def test_keeps_what_the_oldest_snapshot_needs(self):
        s = Store()
        committed(s, a=1)               # ts 1
        committed(s, a=2, b=2)          # ts 2
        old = s.begin()                 # snapshot 2
        committed(s, a=3)               # ts 3
        committed(s, a=4, b=None)       # ts 4
        newer = s.begin()               # snapshot 4
        committed(s, a=5)               # ts 5
        # a: 1 2 3 4 5 -> keep 2 (newest <= 2) and 3 4 5; b: 2 4(tomb) -> keep both
        self.assertEqual(s.gc(), 1)
        self.assertEqual(s.version_count(), 6)
        self.assertEqual((old.get('a'), old.get('b'), newer.get('a'), newer.get('b')), (2, 2, 4, None))
        old.abort()
        # now m = 4: a keeps 4 and 5; b's newest <= 4 is a tombstone: b is gone
        self.assertEqual(s.gc(), 4)
        self.assertEqual(s.version_count(), 2)
        newer.commit()
        self.assertEqual(s.gc(), 1)
        self.assertEqual(s.version_count(), 1)

    def test_validation_still_works_after_gc(self):
        s = Store()
        committed(s, k=1)
        t = s.begin()
        committed(s, n=1)       # created after t's snapshot...
        committed(s, n=None)    # ...and deleted again
        committed(s, k=2)
        s.gc()
        t.put('n', 1)
        t.put('o', 1)
        self.assertEqual(conflict_key(self, t), 'n')
        self.assertEqual(s.gc(), 3)   # nobody is active now: both n versions and k's older version go
        self.assertEqual(s.version_count(), 1)


KEYS = 'abcdefgh'


def model_run(test, seed, steps=250):
    rnd = random.Random(seed)
    st, ms = Store(), ModelStore()
    txns, n = {}, 0
    for step in range(steps):
        where = (seed, step)
        r = rnd.random()
        if r < 0.12 or not txns:
            n += 1
            ser = rnd.random() < 0.4
            txns[n] = (st.begin(serializable=ser), ModelTxn(ms, n, ser))
            test.assertEqual(txns[n][0].snapshot, txns[n][1].snap, where)
            continue
        if r < 0.15:
            test.assertEqual(st.gc(), ms.gc(), where)
            test.assertEqual(st.version_count(), ms.count(), where)
            continue
        if r < 0.17:
            k = rnd.choice(KEYS)
            exp = ms.visible(k, ms.last)
            test.assertEqual(st.get(k), None if exp is _GONE else exp, where)
            continue
        i = rnd.choice(sorted(txns))
        t, m = txns[i]
        r = rnd.random()
        if r < 0.25:
            k = rnd.choice(KEYS)
            test.assertEqual(t.get(k), m.get(k), where)
        elif r < 0.45:
            k, v = rnd.choice(KEYS), rnd.randint(0, 99)
            t.put(k, v)
            m.writes[k] = v
        elif r < 0.55:
            k = rnd.choice(KEYS)
            t.delete(k)
            m.writes[k] = _GONE
        elif r < 0.63:
            lo, hi = rnd.choice([None] + list(KEYS)), rnd.choice([None] + list(KEYS) + ['z'])
            test.assertEqual(t.scan(lo, hi), m.scan(lo, hi), where)
        elif r < 0.7:
            test.assertEqual(t.savepoint(), m.savepoint(), where)
        elif r < 0.76 and m.sps:
            sp = rnd.choice(m.sps)[0]
            t.rollback_to(sp)
            m.rollback_to(sp)
        elif r < 0.79 and m.sps:
            sp = rnd.choice(m.sps)[0]
            t.release(sp)
            m.release(sp)
        elif r < 0.95:
            try:
                exp = ('ok', m.commit())
            except ModelConflict as c:
                exp = ('conflict', c.key)
            try:
                got = ('ok', t.commit())
            except Conflict as c:
                got = ('conflict', c.key)
            test.assertEqual(got, exp, where)
            del txns[i]
        else:
            t.abort()
            m.abort()
            del txns[i]


class AgainstModel(Limited):
    LIMIT = 90

    def test_random_interleavings_1(self):
        for seed in range(0, 120):
            model_run(self, seed)

    def test_random_interleavings_2(self):
        for seed in range(1000, 1120):
            model_run(self, seed)

    def test_random_interleavings_3(self):
        for seed in range(2000, 2120):
            model_run(self, seed)


class Threads(Limited):
    LIMIT = 120

    def test_concurrent_transfers(self):
        old = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:
            s = Store()
            t = s.begin()
            for i in range(20):
                t.put(f'acct{i:02d}', 100)
            t.commit()
            stamps, errors = [], []
            lock = threading.Lock()

            def worker(seed):
                rnd = random.Random(seed)
                try:
                    for _ in range(150):
                        while True:
                            x = s.begin(serializable=rnd.random() < 0.5)
                            a, b = rnd.sample(range(20), 2)
                            ka, kb = f'acct{a:02d}', f'acct{b:02d}'
                            va, vb = x.get(ka), x.get(kb)
                            x.put(ka, va - 1)
                            x.put(kb, vb + 1)
                            try:
                                ts = x.commit()
                            except Conflict:
                                continue
                            with lock:
                                stamps.append(ts)
                            break
                        if rnd.random() < 0.05:
                            s.gc()
                except Exception as e:      # noqa
                    errors.append(repr(e))
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
            for th in threads:
                th.start()
            for th in threads:
                th.join(60)
            self.assertFalse(any(th.is_alive() for th in threads), 'deadlock or far too slow')
            self.assertEqual(errors, [])
            self.assertEqual(sorted(stamps), list(range(2, 2 + 8 * 150)))
            total = sum(v for _, v in s.begin().scan())
            self.assertEqual(total, 2000)
        finally:
            sys.setswitchinterval(old)

    def test_concurrent_snapshots_are_consistent(self):
        s = Store()
        t = s.begin()
        for i in range(10):
            t.put(f'k{i}', 0)
        t.commit()
        stop = threading.Event()
        bad = []

        def writer():
            n = 0
            while not stop.is_set() and n < 3000:
                n += 1
                w = s.begin()
                for i in range(10):
                    w.put(f'k{i}', n)
                w.commit()

        def reader():
            while not stop.is_set():
                r = s.begin()
                vals = {v for _, v in r.scan()}
                if len(vals) != 1:
                    bad.append(vals)
                r.abort()
        ths = [threading.Thread(target=writer)] + [threading.Thread(target=reader) for _ in range(3)]
        for th in ths:
            th.start()
        ths[0].join(60)
        stop.set()
        for th in ths:
            th.join(10)
        self.assertEqual(bad[:3], [])


class Speed(Limited):
    LIMIT = 120
    SETUP = '''
import random
from mvcc import Store
rnd = random.Random(1)
keys = [f'k{rnd.randrange(10**9):09d}' for _ in range(100000)]
ordered = sorted(set(keys))
'''

    def test_load_100000_keys(self):
        self.timed(self.SETUP, '''
s = Store()
t = s.begin()
for i, k in enumerate(keys):
    t.put(k, i)
t.commit()
''', 6, 'assert s.version_count() == len(ordered)')

    def test_scans_and_gets(self):
        setup = self.SETUP + '''
s = Store()
t = s.begin()
for i, k in enumerate(keys):
    t.put(k, i)
t.commit()
r = s.begin()
'''
        self.timed(setup, '''
total = 0
for i in range(20000):
    total += len(r.scan(ordered[i], ordered[i + 50]))
    r.get(keys[-i])
''', 6, 'assert total == 20000 * 50, total')

    def test_many_commits_with_an_old_reader(self):
        setup = self.SETUP + '''
s = Store()
t = s.begin()
for i, k in enumerate(keys):
    t.put(k, i)
t.commit()
old = s.begin()
'''
        self.timed(setup, '''
for i in range(20000):
    w = s.begin()
    for j in range(3):
        w.put(keys[(i * 3 + j) % len(keys)], -i)
    w.commit()
old.abort()
removed = s.gc()
''', 6, 'assert removed == 60000 and s.version_count() == len(ordered), (removed, s.version_count())')


if __name__ == '__main__':
    unittest.main()
