"""A multi-version transactional key-value store: snapshot isolation, optional serializable validation."""
import bisect
import threading

_TOMB = object()


class Conflict(Exception):
    def __init__(self, key):
        super().__init__(f'conflict on {key!r}')
        self.key = key


class TxnClosed(Exception):
    pass


class InvalidSavepoint(Exception):
    pass


class Store:
    def __init__(self):
        self._lock = threading.RLock()
        self._versions = {}      # key -> ([ts...], [value or _TOMB...]) ascending
        self._keys = []          # sorted keys that have at least one stored version
        self._last = 0
        self._active = {}        # id(txn) -> snapshot
        self._log = []           # (ts, keys written) in commit order
        self._log_ts = []        # the same timestamps, for bisect

    # ---- reads at a snapshot
    def _read(self, key, snap):
        vs = self._versions.get(key)
        if not vs:
            return _TOMB
        i = bisect.bisect_right(vs[0], snap) - 1
        return vs[1][i] if i >= 0 else _TOMB

    def get(self, key):
        with self._lock:
            v = self._read(key, self._last)
        return None if v is _TOMB else v

    def begin(self, serializable=False):
        with self._lock:
            t = Txn(self, self._last, serializable)
            self._active[id(t)] = t.snapshot
            return t

    def _scan_committed(self, lo, hi, snap):
        with self._lock:
            i = 0 if lo is None else bisect.bisect_left(self._keys, lo)
            j = len(self._keys) if hi is None else bisect.bisect_left(self._keys, hi)
            out = []
            for k in self._keys[i:j]:
                v = self._read(k, snap)
                if v is not _TOMB:
                    out.append((k, v))
            return out

    def _commit(self, txn):
        with self._lock:
            writes = txn._writes
            snap = txn.snapshot
            conflicts = []
            later = self._log[bisect.bisect_right(self._log_ts, snap):]
            if later:
                touched = set()
                for _, keys in later:
                    touched.update(keys)
                conflicts += [k for k in writes if k in touched]
                if txn.serializable:
                    conflicts += [k for k in txn._reads if k in touched]
                    for lo, hi in txn._ranges:
                        conflicts += [k for k in touched if (lo is None or k >= lo) and (hi is None or k < hi)]
            if conflicts:
                self._active.pop(id(txn), None)
                raise Conflict(min(conflicts))
            self._active.pop(id(txn), None)
            if not writes:
                return None
            self._last += 1
            ts = self._last
            new_keys = []
            for k, v in writes.items():
                vs = self._versions.get(k)
                if vs is None:
                    vs = self._versions[k] = ([], [])
                    new_keys.append(k)
                vs[0].append(ts)
                vs[1].append(v)
            if len(new_keys) > 64:
                self._keys = sorted(self._keys + new_keys)
            else:
                for k in new_keys:
                    bisect.insort(self._keys, k)
            self._log.append((ts, list(writes)))
            self._log_ts.append(ts)
            return ts

    def _end(self, txn):
        with self._lock:
            self._active.pop(id(txn), None)

    def gc(self):
        with self._lock:
            m = min(self._active.values()) if self._active else self._last
            removed = 0
            dead_keys = []
            for k, (tss, vals) in self._versions.items():
                i = bisect.bisect_right(tss, m) - 1   # newest version visible at m
                if i < 0:
                    continue
                cut = i + 1 if vals[i] is _TOMB else i
                if cut:
                    del tss[:cut]
                    del vals[:cut]
                    removed += cut
                if not tss:
                    dead_keys.append(k)
            for k in dead_keys:
                del self._versions[k]
                del self._keys[bisect.bisect_left(self._keys, k)]
            # the commit log is only needed for commits newer than the oldest active snapshot
            j = bisect.bisect_right(self._log_ts, m)
            del self._log[:j]
            del self._log_ts[:j]
            return removed

    def version_count(self):
        with self._lock:
            return sum(len(tss) for tss, _ in self._versions.values())


class Txn:
    def __init__(self, store, snapshot, serializable):
        self._store = store
        self.snapshot = snapshot
        self.serializable = serializable
        self._writes = {}       # key -> value or _TOMB
        self._undo = []         # (key, previous entry or missing) in order
        self._savepoints = []   # (id, undo length)
        self._next_sp = 1
        self._reads = set()
        self._ranges = []
        self._open = True

    def _check(self):
        if not self._open:
            raise TxnClosed()

    def get(self, key):
        self._check()
        self._reads.add(key)
        if key in self._writes:
            v = self._writes[key]
        else:
            with self._store._lock:
                v = self._store._read(key, self.snapshot)
        return None if v is _TOMB else v

    def _write(self, key, value):
        self._undo.append((key, self._writes.get(key, _MISSING)))
        self._writes[key] = value

    def put(self, key, value):
        self._check()
        if value is None:
            raise ValueError('values cannot be None')
        self._write(key, value)

    def delete(self, key):
        self._check()
        self._write(key, _TOMB)

    def scan(self, lo=None, hi=None):
        self._check()
        self._ranges.append((lo, hi))
        if lo is not None and hi is not None and lo >= hi:
            return []
        merged = dict(self._store._scan_committed(lo, hi, self.snapshot))
        for k, v in self._writes.items():
            if (lo is None or k >= lo) and (hi is None or k < hi):
                if v is _TOMB:
                    merged.pop(k, None)
                else:
                    merged[k] = v
        return sorted(merged.items(), key=lambda kv: kv[0])

    def savepoint(self):
        self._check()
        sp = self._next_sp
        self._next_sp += 1
        self._savepoints.append((sp, len(self._undo)))
        return sp

    def _find(self, sp):
        for i, (s, _) in enumerate(self._savepoints):
            if s == sp:
                return i
        raise InvalidSavepoint(sp)

    def rollback_to(self, sp):
        self._check()
        i = self._find(sp)
        n = self._savepoints[i][1]
        while len(self._undo) > n:
            key, prev = self._undo.pop()
            if prev is _MISSING:
                del self._writes[key]
            else:
                self._writes[key] = prev
        del self._savepoints[i + 1:]

    def release(self, sp):
        self._check()
        i = self._find(sp)
        del self._savepoints[i:]

    def commit(self):
        self._check()
        self._open = False
        return self._store._commit(self)

    def abort(self):
        self._check()
        self._open = False
        self._store._end(self)


_MISSING = object()
