Write `mvcc.py`: an in-memory multi-version transactional key-value store (Python 3 standard library only).

## API

```python
store = Store()
t = store.begin(serializable=False)   # a Txn; t.snapshot is an int
t.get(key)            # value or None
t.put(key, value)     # value must not be None (ValueError)
t.delete(key)
t.scan(lo=None, hi=None)   # sorted list of (key, value) with lo <= key < hi (None = unbounded)
sp = t.savepoint(); t.rollback_to(sp); t.release(sp)
t.commit()            # the new commit timestamp (int), or None for a read-only transaction
t.abort()
store.get(key)        # the latest committed value, or None
store.gc()            # removes old versions, returns how many it removed
store.version_count() # how many versions are stored (tombstones included)
```

Exceptions (defined in mvcc.py, subclasses of `Exception`): `Conflict` (with attribute `key`), `TxnClosed`, `InvalidSavepoint`. Keys are strings (compared as Python strings); values are any non-None objects (stored as given).

## Rules

**Timestamps and snapshots.** The store's last commit timestamp starts at 0. `begin()` takes `snapshot` = the last commit timestamp at that moment. A transaction sees the newest committed version of each key with timestamp <= its snapshot, overlaid with its own writes (the last write it made to a key wins; a delete makes the key absent). A committed delete is stored as a tombstone version. `store.get` reads the newest committed version.

**Writes.** `put` and `delete` record writes in the transaction only. Deleting a key that does not exist is still a write. The write set at commit is the set of keys with a pending write (after any savepoint rollbacks), even if the value equals what was there before.

**Commit.** Atomically: (1) validation (below); on failure the transaction is aborted and `Conflict(key)` is raised, where `key` is the smallest conflicting key (over all reasons). (2) If the write set is empty, `commit()` returns `None` and no timestamp is used. (3) Otherwise the last commit timestamp is incremented, every write is stored as a new version (tombstone for deletes) with that timestamp, and the timestamp is returned. Timestamps are therefore 1, 2, 3... with no gaps.

**Validation.** A key *was changed after the snapshot* if a transaction that committed after this transaction's snapshot (commit timestamp > snapshot) wrote it. Every transaction fails if a key in its write set was changed after its snapshot (snapshot isolation, first committer wins). A transaction begun with `serializable=True` additionally fails if any key it passed to `get` (found or not, even one it had written itself) was changed after its snapshot, or if any key inside any range it scanned (`lo <= k < hi` with the scan's bounds) was changed after its snapshot - including keys that did not exist when it scanned (phantoms). Serializable transactions are validated even when read-only (they can raise `Conflict` instead of returning `None`).

**End of a transaction.** After `commit()` (successful or not) or `abort()`, every method of the transaction raises `TxnClosed`, including `commit` and `abort`. A transaction is *active* from `begin()` until then.

**Savepoints.** `savepoint()` returns 1, 2, 3... (numbered per transaction, never reused). `rollback_to(sp)` undoes every write made after `sp` was taken (restoring earlier pending values or no pending write), destroys the savepoints taken after `sp`, and keeps `sp` itself usable. `release(sp)` destroys `sp` and the savepoints taken after it, keeping the writes. Reads are never undone (the serializable read set keeps them). A savepoint that does not exist or was destroyed raises `InvalidSavepoint`.

**Garbage collection.** Let `m` be the smallest snapshot of the active transactions, or the last commit timestamp if none is active. For each key, `gc()` removes every version older than the key's newest version with timestamp <= m, and removes that newest version too if it is a tombstone. Versions with timestamps > m are all kept. It returns the number of versions removed; `version_count()` counts what is left. Reads and validation must stay correct after `gc()`.

**Threads.** A `Store` is used by many threads at once (each transaction by one thread): `begin`, `commit`, `abort`, `gc`, `store.get` and transaction reads must be safe to call concurrently, and commits must behave as if they ran one at a time.

## Speed

With 100,000 committed keys, 20,000 `scan`s that each return about 50 keys plus 20,000 `get`s must take a few seconds at most (keep the keys ordered; do not sort everything per scan). 20,000 small transactions committed one after another while an old transaction stays open, and loading 100,000 keys in one transaction, must also take a few seconds.

Make sure `python3 -m unittest` passes. The grading tests also run thousands of random interleavings of several transactions against a model of these rules.
