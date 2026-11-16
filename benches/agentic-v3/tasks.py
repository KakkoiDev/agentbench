"""agentic-v3: held-out agentic tasks, same format and grading as agentic-v1 and agentic-v2.

Written on 2026-11-16, after agentic-v1 and agentic-v2 had been used to tune the pi setup
for local models, to check whether those gains generalise. Keep it out of the tuning loop:
read only its totals when judging a change, never its transcripts.
Each task covers an area neither earlier set does: an event-driven state machine from a
written spec, a CLI with argparse and exit codes, a TTL cache with LRU eviction and
invalidation, dependency ordering with cycle detection, and subtle timezone and rounding
bugs that the visible test only partly reveals.

    .venv/bin/python benches/agentic-v3/tasks.py     # validate every task
"""
import importlib.util, sys
from pathlib import Path

_v1 = importlib.util.spec_from_file_location('agentic_v1_tasks', Path(__file__).resolve().parents[1] / 'agentic-v1' / 'tasks.py')
V1 = importlib.util.module_from_spec(_v1)
_v1.loader.exec_module(V1)
write_files, run_hidden, run_visible = V1.write_files, V1.run_hidden, V1.run_visible

TASKS = {}

# 1. An event-driven state machine from a written spec ------------------------------
TASKS['order-state-machine'] = dict(
    area='event-driven state machine from a written spec',
    instruction="""`Order` in order.py tracks an order's lifecycle but its transitions are incomplete and partly wrong. Make `order.handle(event, **data)` follow this spec:

- States: "new", "paid", "shipped", "delivered", "cancelled", "refunded". An order starts "new".
- `pay` (data `amount`, int cents): new -> paid. A missing amount or one different from `total_cents` raises `PaymentError`.
- `ship` (data `tracking`): paid -> shipped. `tracking` must be a string that is not empty or only whitespace, else `ValueError`; it is stored in `order.tracking`.
- `deliver`: shipped -> delivered, and sets `order.delivered_at = clock()`.
- `cancel`: new -> cancelled; paid -> refunded (and `refunded_cents` becomes `total_cents`). An order cannot be cancelled once shipped.
- `return`: delivered -> refunded (and `refunded_cents` becomes `total_cents`), allowed while `clock() - delivered_at` is at most 30 days (30 * 86400 seconds, inclusive); later it raises `ReturnWindowClosed`, a subclass of `InvalidTransition`.
- Any other event in a state, including unknown event names and any event in the final states "cancelled" and "refunded", raises `InvalidTransition(state, event)`. This state check comes before any check of the data.
- A failed event changes nothing: state, `history`, `tracking`, `refunded_cents` stay as they were and no listener is called.
- `history` is the list of `(from_state, event, to_state)` of the successful transitions.
- `order.on(listener)` registers `listener(order, from_state, event, to_state)`, called after every successful transition (with the state already updated), in registration order; `on` returns the listener so it can be used as a decorator. If a listener raises, the transition still stands, the remaining listeners are still called, and afterwards the first exception raised is re-raised.

Make sure `python3 -m unittest` passes.""",
    files={
        'order.py': r'''"""Order lifecycle."""
import time


class InvalidTransition(Exception):
    def __init__(self, state, event):
        super().__init__(f"cannot {event} when {state}")
        self.state = state
        self.event = event


class PaymentError(Exception):
    pass


class ReturnWindowClosed(InvalidTransition):
    pass


TRANSITIONS = {
    ("new", "pay"): "paid",
    ("new", "cancel"): "cancelled",
    ("paid", "ship"): "shipped",
    ("paid", "cancel"): "cancelled",
    ("shipped", "deliver"): "delivered",
    ("shipped", "cancel"): "cancelled",
}


class Order:
    def __init__(self, total_cents, clock=time.time):
        self.total_cents = total_cents
        self.clock = clock
        self.state = "new"
        self.history = []
        self.refunded_cents = 0
        self.tracking = None
        self.delivered_at = None

    def handle(self, event, **data):
        new = TRANSITIONS.get((self.state, event))
        if new is None:
            raise InvalidTransition(self.state, event)
        if event == "ship":
            self.tracking = data.get("tracking")
        self.history.append((self.state, event, new))
        self.state = new
''',
        'test_order.py': r'''import unittest
from order import Order, InvalidTransition, PaymentError


class OrderTest(unittest.TestCase):
    def test_happy_path(self):
        o = Order(1500, clock=lambda: 100.0)
        o.handle("pay", amount=1500)
        o.handle("ship", tracking="TRK1")
        o.handle("deliver")
        self.assertEqual(o.state, "delivered")
        self.assertEqual(o.history, [("new", "pay", "paid"), ("paid", "ship", "shipped"), ("shipped", "deliver", "delivered")])

    def test_wrong_amount(self):
        o = Order(1500)
        with self.assertRaises(PaymentError):
            o.handle("pay", amount=1499)
        self.assertEqual(o.state, "new")

    def test_no_cancel_after_shipping(self):
        o = Order(100)
        o.handle("pay", amount=100)
        o.handle("ship", tracking="T")
        with self.assertRaises(InvalidTransition):
            o.handle("cancel")


if __name__ == "__main__":
    unittest.main()
''',
    },
    hidden=r'''import unittest
from order import Order, InvalidTransition, PaymentError, ReturnWindowClosed

DAY = 86400


def clocked(total=1000):
    now = [1000.0]
    return Order(total, clock=lambda: now[0]), now


def delivered(total=1000):
    o, now = clocked(total)
    o.handle("pay", amount=total)
    o.handle("ship", tracking="T-1")
    o.handle("deliver")
    return o, now


class Hidden(unittest.TestCase):
    def test_full_path(self):
        o, now = delivered(2500)
        self.assertEqual((o.state, o.tracking, o.delivered_at, o.refunded_cents), ("delivered", "T-1", 1000.0, 0))
        self.assertEqual(o.history, [("new", "pay", "paid"), ("paid", "ship", "shipped"), ("shipped", "deliver", "delivered")])

    def test_payment_errors_change_nothing(self):
        for data in ({"amount": 999}, {"amount": 1001}, {}):
            o, _ = clocked()
            with self.assertRaises(PaymentError, msg=data):
                o.handle("pay", **data)
            self.assertEqual((o.state, o.history), ("new", []))

    def test_tracking_validation(self):
        for bad in ("", "   ", None, 5):
            o, _ = clocked()
            o.handle("pay", amount=1000)
            with self.assertRaises(ValueError, msg=repr(bad)):
                o.handle("ship", tracking=bad)
            self.assertEqual((o.state, o.tracking, len(o.history)), ("paid", None, 1))
        o, _ = clocked()
        o.handle("pay", amount=1000)
        with self.assertRaises(ValueError):
            o.handle("ship")

    def test_state_check_comes_first(self):
        o, _ = clocked()
        with self.assertRaises(InvalidTransition):
            o.handle("ship", tracking="")
        with self.assertRaises(InvalidTransition):
            o.handle("deliver")
        o.handle("pay", amount=1000)
        with self.assertRaises(InvalidTransition):
            o.handle("pay", amount=1)

    def test_cancel(self):
        o, _ = clocked()
        o.handle("cancel")
        self.assertEqual((o.state, o.refunded_cents), ("cancelled", 0))
        o, _ = clocked(700)
        o.handle("pay", amount=700)
        o.handle("cancel")
        self.assertEqual((o.state, o.refunded_cents), ("refunded", 700))
        self.assertEqual(o.history[-1], ("paid", "cancel", "refunded"))
        o, _ = clocked()
        o.handle("pay", amount=1000)
        o.handle("ship", tracking="X")
        for step in ("cancel", "deliver", "cancel"):
            if step == "deliver":
                o.handle("deliver")
                continue
            with self.assertRaises(InvalidTransition):
                o.handle(step)
        self.assertEqual((o.state, o.refunded_cents), ("delivered", 0))

    def test_return_window(self):
        o, now = delivered()
        now[0] += 30 * DAY
        o.handle("return")
        self.assertEqual((o.state, o.refunded_cents), ("refunded", 1000))
        self.assertEqual(o.history[-1], ("delivered", "return", "refunded"))
        o, now = delivered()
        now[0] += 30 * DAY + 1
        with self.assertRaises(ReturnWindowClosed) as e:
            o.handle("return")
        self.assertIsInstance(e.exception, InvalidTransition)
        self.assertEqual((o.state, o.refunded_cents, len(o.history)), ("delivered", 0, 3))
        o, _ = clocked()
        o.handle("pay", amount=1000)
        with self.assertRaises(InvalidTransition):
            o.handle("return")

    def test_final_states_and_unknown_events(self):
        o, _ = clocked()
        o.handle("cancel")
        r, _ = delivered()
        r.handle("return")
        for order in (o, r):
            for event in ("pay", "ship", "deliver", "cancel", "return"):
                with self.assertRaises(InvalidTransition) as e:
                    order.handle(event, amount=1000, tracking="T")
                self.assertEqual((e.exception.state, e.exception.event), (order.state, event))
        o, _ = clocked()
        with self.assertRaises(InvalidTransition) as e:
            o.handle("teleport")
        self.assertEqual((e.exception.state, e.exception.event, o.state), ("new", "teleport", "new"))

    def test_listeners(self):
        o, _ = clocked()
        seen = []

        @o.on
        def first(order, src, event, dst):
            seen.append(("first", src, event, dst, order.state))

        self.assertTrue(callable(first))
        o.on(lambda order, src, event, dst: seen.append(("second", src, event, dst)))
        o.handle("pay", amount=1000)
        with self.assertRaises(ValueError):
            o.handle("ship", tracking=" ")
        with self.assertRaises(InvalidTransition):
            o.handle("deliver")
        self.assertEqual(seen, [("first", "new", "pay", "paid", "paid"), ("second", "new", "pay", "paid")])

    def test_failing_listener(self):
        o, _ = clocked()
        calls = []

        def bad(order, *a):
            calls.append("bad")
            raise KeyError("boom")

        def worse(order, *a):
            calls.append("worse")
            raise RuntimeError("later")

        o.on(bad)
        o.on(worse)
        o.on(lambda order, *a: calls.append("good"))
        with self.assertRaises(KeyError):
            o.handle("pay", amount=1000)
        self.assertEqual(calls, ["bad", "worse", "good"])
        self.assertEqual((o.state, o.history), ("paid", [("new", "pay", "paid")]))
''',
    fix={'order.py': lambda s: s[:s.index('TRANSITIONS = {')] + r'''TRANSITIONS = {
    ("new", "pay"): "paid",
    ("new", "cancel"): "cancelled",
    ("paid", "ship"): "shipped",
    ("paid", "cancel"): "refunded",
    ("shipped", "deliver"): "delivered",
    ("delivered", "return"): "refunded",
}
RETURN_WINDOW = 30 * 86400


class Order:
    def __init__(self, total_cents, clock=time.time):
        self.total_cents = total_cents
        self.clock = clock
        self.state = "new"
        self.history = []
        self.refunded_cents = 0
        self.tracking = None
        self.delivered_at = None
        self._listeners = []

    def on(self, listener):
        self._listeners.append(listener)
        return listener

    def handle(self, event, **data):
        new = TRANSITIONS.get((self.state, event))
        if new is None:
            raise InvalidTransition(self.state, event)
        if event == "pay" and data.get("amount") != self.total_cents:
            raise PaymentError(f"expected {self.total_cents}, got {data.get('amount')}")
        if event == "ship":
            tracking = data.get("tracking")
            if not isinstance(tracking, str) or not tracking.strip():
                raise ValueError("tracking must be a non-empty string")
            self.tracking = tracking
        if event == "return" and self.clock() - self.delivered_at > RETURN_WINDOW:
            raise ReturnWindowClosed(self.state, event)
        if event == "deliver":
            self.delivered_at = self.clock()
        if new == "refunded":
            self.refunded_cents = self.total_cents
        old = self.state
        self.history.append((old, event, new))
        self.state = new
        first = None
        for listener in list(self._listeners):
            try:
                listener(self, old, event, new)
            except Exception as e:
                if first is None:
                    first = e
        if first is not None:
            raise first
'''},
)

# 2. A CLI with argparse and exit codes ---------------------------------------------
TASKS['kv-cli'] = dict(
    area='CLI with argparse and exit codes',
    instruction="""kv.py is a small command-line key-value store kept in a JSON file. Finish it to this spec:

- `main(argv)` parses `argv` (a list of strings, like `sys.argv[1:]`), prints to stdout/stderr and returns the exit code; it never raises `SystemExit` itself. Running `python3 kv.py ...` exits with that code.
- `--file PATH` (before the command, default `kv.json`) is the store: a JSON object mapping string keys to string values. A missing file is an empty store; `get` and `list` never create it.
- `set KEY VALUE [--if-absent]` stores the value, prints nothing, exit 0. With `--if-absent`, if the key already exists: print `exists: KEY` to stderr, exit 3, store unchanged.
- `get KEY` prints the value on stdout; a missing key prints `not found: KEY` to stderr and exits 1.
- `delete KEY [KEY ...]` removes the keys that exist (and saves). For each missing key, in argument order, it prints `not found: KEY` to stderr; exit 1 if any key was missing, else 0.
- `list [--prefix P]` prints `key=value` lines sorted by key, only for keys starting with P; exit 0 (an empty store prints nothing).
- A key must be non-empty and contain no `=` and no whitespace; otherwise print `invalid key: KEY` to stderr and exit 2.
- If the file exists but is not valid JSON, or is not an object mapping strings to strings, print `corrupt store: PATH` to stderr and exit 4 without changing the file.
- Usage errors (no command, unknown command, missing arguments) print argparse's message to stderr and exit 2; `--help` prints the help on stdout and exits 0.
- On any error stdout stays empty. Every message is one line ending in a newline.

Make sure `python3 -m unittest` passes.""",
    files={
        'kv.py': r'''"""A tiny key-value store kept in a JSON file."""
import argparse
import json
import os
import sys


def load(path):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def save(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="kv")
    parser.add_argument("--file", default="kv.json")
    sub = parser.add_subparsers(dest="cmd")
    s = sub.add_parser("set")
    s.add_argument("key")
    s.add_argument("value")
    g = sub.add_parser("get")
    g.add_argument("key")
    sub.add_parser("list")
    args = parser.parse_args(argv)
    data = load(args.file)
    if args.cmd == "set":
        data[args.key] = args.value
        save(args.file, data)
    elif args.cmd == "get":
        print(data[args.key])
    elif args.cmd == "list":
        for k, v in data.items():
            print(f"{k}={v}")
    return 0


if __name__ == "__main__":
    main()
''',
        'test_kv.py': r'''import contextlib, io, os, tempfile, unittest
import kv


class KvTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "store.json")

    def tearDown(self):
        self.dir.cleanup()

    def kv(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = kv.main(["--file", self.path, *argv])
        return code, out.getvalue(), err.getvalue()

    def test_set_and_get(self):
        self.assertEqual(self.kv("set", "name", "Ann"), (0, "", ""))
        self.assertEqual(self.kv("get", "name"), (0, "Ann\n", ""))

    def test_get_missing(self):
        self.assertEqual(self.kv("get", "nope"), (1, "", "not found: nope\n"))

    def test_list_sorted(self):
        self.kv("set", "b", "2")
        self.kv("set", "a", "1")
        self.assertEqual(self.kv("list"), (0, "a=1\nb=2\n", ""))


if __name__ == "__main__":
    unittest.main()
''',
    },
    hidden=r'''import contextlib, io, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
import kv


class Hidden(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "s.json")

    def tearDown(self):
        self.dir.cleanup()

    def kv(self, *argv, file=True):
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = kv.main((["--file", self.path] if file else []) + list(argv))
        except SystemExit as e:
            self.fail(f"main raised SystemExit({e.code!r}) for {argv}")
        return code, out.getvalue(), err.getvalue()

    def stored(self):
        with open(self.path) as f:
            return json.load(f)

    def test_set_get_list_prefix(self):
        for k, v in (("b", "3"), ("a.y", "2"), ("a.x", "1"), ("eq", "x = y")):
            self.assertEqual(self.kv("set", k, v), (0, "", ""))
        self.assertEqual(self.kv("get", "eq"), (0, "x = y\n", ""))
        self.assertEqual(self.kv("list"), (0, "a.x=1\na.y=2\nb=3\neq=x = y\n", ""))
        self.assertEqual(self.kv("list", "--prefix", "a."), (0, "a.x=1\na.y=2\n", ""))
        self.assertEqual(self.kv("list", "--prefix", "zz"), (0, "", ""))
        self.kv("set", "b", "4")
        self.assertEqual(self.stored(), {"a.x": "1", "a.y": "2", "b": "4", "eq": "x = y"})

    def test_if_absent(self):
        self.kv("set", "k", "old")
        self.assertEqual(self.kv("set", "k", "new", "--if-absent"), (3, "", "exists: k\n"))
        self.assertEqual(self.kv("get", "k"), (0, "old\n", ""))
        self.assertEqual(self.kv("set", "--if-absent", "j", "v"), (0, "", ""))
        self.assertEqual(self.stored(), {"k": "old", "j": "v"})

    def test_get_missing(self):
        self.kv("set", "a", "1")
        self.assertEqual(self.kv("get", "nope"), (1, "", "not found: nope\n"))

    def test_delete(self):
        for k in "abc":
            self.kv("set", k, k.upper())
        self.assertEqual(self.kv("delete", "a", "zz", "b", "yy"), (1, "", "not found: zz\nnot found: yy\n"))
        self.assertEqual(self.stored(), {"c": "C"})
        self.assertEqual(self.kv("delete", "c"), (0, "", ""))
        self.assertEqual(self.stored(), {})

    def test_invalid_keys(self):
        for argv in (("set", "", "v"), ("set", "a=b", "v"), ("set", "a b", "v"), ("get", "x\ty"), ("delete", "ok", "x=y")):
            code, out, err = self.kv(*argv)
            bad = next(a for a in argv[1:] if a == "" or "=" in a or any(c.isspace() for c in a))
            self.assertEqual((code, out, err), (2, "", f"invalid key: {bad}\n"), argv)
        self.assertFalse(os.path.exists(self.path))

    def test_missing_file_not_created(self):
        self.assertEqual(self.kv("list"), (0, "", ""))
        self.assertEqual(self.kv("get", "a")[0], 1)
        self.assertFalse(os.path.exists(self.path))

    def test_corrupt_store(self):
        for content in ("not json", "[1, 2]", '{"a": 1}', '"text"', ""):
            with open(self.path, "w") as f:
                f.write(content)
            for argv in (("get", "a"), ("list",), ("set", "a", "1"), ("delete", "a")):
                self.assertEqual(self.kv(*argv), (4, "", f"corrupt store: {self.path}\n"), (content, argv))
            with open(self.path) as f:
                self.assertEqual(f.read(), content)

    def test_usage_errors(self):
        for argv in ((), ("frobnicate",), ("get",), ("set", "k"), ("delete",)):
            code, out, err = self.kv(*argv)
            self.assertEqual(code, 2, argv)
            self.assertEqual(out, "", argv)
            self.assertTrue(err.strip(), argv)
        code, out, err = self.kv("--help")
        self.assertEqual(code, 0)
        self.assertIn("usage", out.lower())

    def test_as_a_script(self):
        def run(*argv):
            return subprocess.run([sys.executable, "kv.py", "--file", self.path, *argv], cwd=Path(__file__).parent,
                                  capture_output=True, text=True, timeout=30)
        self.assertEqual(run("set", "a", "1").returncode, 0)
        r = run("get", "a")
        self.assertEqual((r.returncode, r.stdout), (0, "1\n"))
        r = run("get", "b")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (1, "", "not found: b\n"))
        self.assertEqual(run("bogus").returncode, 2)
        self.assertEqual(run("set", "a", "2", "--if-absent").returncode, 3)
''',
    fix={'kv.py': lambda s: r'''"""A tiny key-value store kept in a JSON file."""
import argparse
import json
import os
import sys


class Corrupt(Exception):
    pass


def load(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
    except (ValueError, UnicodeDecodeError):
        raise Corrupt(path)
    if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
        raise Corrupt(path)
    return data


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def valid_key(key):
    return bool(key) and "=" not in key and not any(c.isspace() for c in key)


def make_parser():
    parser = argparse.ArgumentParser(prog="kv")
    parser.add_argument("--file", default="kv.json")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set")
    s.add_argument("key")
    s.add_argument("value")
    s.add_argument("--if-absent", action="store_true")
    g = sub.add_parser("get")
    g.add_argument("key")
    d = sub.add_parser("delete")
    d.add_argument("keys", nargs="+")
    ls = sub.add_parser("list")
    ls.add_argument("--prefix", default="")
    return parser


def err(message):
    print(message, file=sys.stderr)


def main(argv=None):
    try:
        args = make_parser().parse_args(argv)
    except SystemExit as e:
        return 0 if e.code is None else e.code
    keys = [args.key] if args.cmd in ("set", "get") else args.keys if args.cmd == "delete" else []
    for key in keys:
        if not valid_key(key):
            err(f"invalid key: {key}")
            return 2
    try:
        data = load(args.file)
    except Corrupt:
        err(f"corrupt store: {args.file}")
        return 4
    if args.cmd == "set":
        if args.if_absent and args.key in data:
            err(f"exists: {args.key}")
            return 3
        data[args.key] = args.value
        save(args.file, data)
        return 0
    if args.cmd == "get":
        if args.key not in data:
            err(f"not found: {args.key}")
            return 1
        print(data[args.key])
        return 0
    if args.cmd == "delete":
        missing = [k for k in args.keys if k not in data]
        present = [k for k in args.keys if k in data]
        for k in present:
            del data[k]
        if present:
            save(args.file, data)
        for k in missing:
            err(f"not found: {k}")
        return 1 if missing else 0
    for k in sorted(data):
        if k.startswith(args.prefix):
            print(f"{k}={data[k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''},
)

# 3. A caching layer with TTL, LRU eviction and invalidation ------------------------
TASKS['ttl-cache'] = dict(
    area='caching layer with TTL, eviction and invalidation',
    instruction="""`TTLCache(loader, ttl, max_size=None, clock=time.monotonic)` in cache.py is a read-through cache with bugs and missing pieces. Make it follow this spec:

- The constructor raises ValueError unless `ttl > 0` and `max_size` is None or an int >= 1.
- An entry is fresh while `clock() - stored_at < ttl` (so it has expired at exactly `ttl`). An expired entry counts as absent everywhere: it is never returned, not counted by `len`, not reported by `in`.
- `get(key)`: a fresh entry is a hit and is returned without calling the loader. Otherwise it is a miss: call `loader(key)` and store the result with `stored_at = clock()` read after the loader returns. Every value is cached, including None, 0, "" and other falsy values.
- If the loader raises, the exception propagates, it still counts as a miss, nothing is stored, and any old entry for that key is gone.
- `max_size`: before storing a new entry in a full cache, first drop all expired entries; if it is still full, evict the least recently used entry (use = being stored or returned by a hit) until there is room. Only these LRU evictions count in `evictions`.
- `invalidate(key)` removes the key (no error if absent). `invalidate_where(predicate)` removes every entry whose key satisfies `predicate(key)` and returns how many fresh entries it removed. `clear()` removes everything.
- `len(cache)` is the number of fresh entries; `key in cache` tells whether a fresh entry exists. Neither loads anything, changes the stats, or changes which entry is least recently used.
- `stats()` returns `{"hits": ..., "misses": ..., "evictions": ...}`.

Make sure `python3 -m unittest` passes.""",
    files={
        'cache.py': r'''"""A read-through cache with a time-to-live."""
import time


class TTLCache:
    def __init__(self, loader, ttl, max_size=None, clock=time.monotonic):
        self.loader = loader
        self.ttl = ttl
        self.max_size = max_size
        self.clock = clock
        self.data = {}  # key -> (value, stored_at)
        self.hits = 0
        self.misses = 0

    def get(self, key):
        entry = self.data.get(key)
        if entry and entry[0] and self.clock() - entry[1] <= self.ttl:
            self.hits += 1
            return entry[0]
        self.misses += 1
        value = self.loader(key)
        self.data[key] = (value, self.clock())
        return value

    def invalidate(self, key):
        del self.data[key]

    def stats(self):
        return {"hits": self.hits, "misses": self.misses}
''',
        'test_cache.py': r'''import unittest
from cache import TTLCache


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class CacheTest(unittest.TestCase):
    def test_hit_and_expiry(self):
        clock, calls = Clock(), []
        c = TTLCache(lambda k: calls.append(k) or k.upper(), ttl=10, clock=clock)
        self.assertEqual(c.get("a"), "A")
        clock.now = 5
        self.assertEqual(c.get("a"), "A")
        clock.now = 20
        self.assertEqual(c.get("a"), "A")
        self.assertEqual(calls, ["a", "a"])
        self.assertEqual(c.stats(), {"hits": 1, "misses": 2, "evictions": 0})

    def test_invalidate(self):
        calls = []
        c = TTLCache(lambda k: calls.append(k) or 1, ttl=60, clock=Clock())
        c.get("x")
        c.invalidate("x")
        c.get("x")
        self.assertEqual(calls, ["x", "x"])

    def test_max_size(self):
        c = TTLCache(str, ttl=60, max_size=2, clock=Clock())
        for k in (1, 2, 3):
            c.get(k)
        self.assertEqual(len(c), 2)


if __name__ == "__main__":
    unittest.main()
''',
    },
    hidden=r'''import unittest
from cache import TTLCache


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make(ttl=10, max_size=None, values=None):
    clock, calls = Clock(), []

    def loader(key):
        calls.append(key)
        return (values or {}).get(key, f"v{key}")
    return TTLCache(loader, ttl=ttl, max_size=max_size, clock=clock), clock, calls


class Hidden(unittest.TestCase):
    def test_expiry_boundary(self):
        c, clock, calls = make(ttl=10)
        c.get("a")
        clock.now = 9.999
        c.get("a")
        self.assertIn("a", c)
        clock.now = 10
        self.assertNotIn("a", c)
        self.assertEqual(len(c), 0)
        c.get("a")
        self.assertEqual(calls, ["a", "a"])
        clock.now = 19.5
        c.get("a")
        self.assertEqual(calls, ["a", "a"])
        self.assertEqual(c.stats(), {"hits": 2, "misses": 2, "evictions": 0})

    def test_falsy_values_cached(self):
        c, clock, calls = make(values={"n": None, "z": 0, "e": "", "l": []})
        for _ in range(3):
            self.assertEqual([c.get(k) for k in "nzel"], [None, 0, "", []])
        self.assertEqual(calls, list("nzel"))
        self.assertEqual(len(c), 4)

    def test_loader_errors(self):
        clock, fail = Clock(), [False]

        def loader(key):
            if fail[0]:
                raise LookupError(key)
            return key * 2
        c = TTLCache(loader, ttl=10, clock=clock)
        self.assertEqual(c.get("a"), "aa")
        clock.now = 15
        fail[0] = True
        with self.assertRaises(LookupError):
            c.get("a")
        self.assertNotIn("a", c)
        self.assertEqual(len(c), 0)
        fail[0] = False
        self.assertEqual(c.get("a"), "aa")
        self.assertEqual(c.stats(), {"hits": 0, "misses": 3, "evictions": 0})

    def test_stored_at_is_after_loading(self):
        clock = Clock()

        def slow_loader(key):
            clock.now += 8
            return key
        c = TTLCache(slow_loader, ttl=10, clock=clock)
        c.get("k")
        clock.now = 17
        self.assertIn("k", c)

    def test_invalidation(self):
        c, clock, calls = make(ttl=10)
        c.invalidate("absent")
        c.get("user:1")
        clock.now = 8
        c.get("user:2"); c.get("post:1"); c.get("user:3")
        clock.now = 12
        self.assertEqual(c.invalidate_where(lambda k: k.startswith("user:")), 2)
        self.assertEqual(len(c), 1)
        self.assertIn("post:1", c)
        c.invalidate("post:1")
        self.assertNotIn("post:1", c)
        c.get("a"); c.get("b")
        c.clear()
        self.assertEqual(len(c), 0)
        c.get("a")
        self.assertEqual(calls.count("a"), 2)

    def test_contains_and_len_are_passive(self):
        c, clock, calls = make(max_size=2)
        c.get(1); c.get(2)
        for _ in range(3):
            self.assertIn(1, c)
            self.assertNotIn(9, c)
        self.assertEqual(len(c), 2)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(c.stats(), {"hits": 0, "misses": 2, "evictions": 0})
        c.get(3)  # 1 is still the least recently used, `in` did not touch it
        self.assertNotIn(1, c)
        self.assertIn(2, c)

    def test_lru_eviction(self):
        c, clock, calls = make(max_size=2)
        c.get("a"); c.get("b"); c.get("a"); c.get("c")
        self.assertIn("a", c)
        self.assertNotIn("b", c)
        self.assertIn("c", c)
        self.assertEqual(c.stats(), {"hits": 1, "misses": 3, "evictions": 1})
        c.get("b")
        self.assertNotIn("a", c)
        self.assertEqual(c.stats()["evictions"], 2)

    def test_expired_dropped_before_evicting(self):
        c, clock, calls = make(ttl=10, max_size=2)
        c.get("a")
        clock.now = 5
        c.get("b")
        clock.now = 11
        c.get("c")
        self.assertEqual((len(c), "b" in c, "c" in c), (2, True, True))
        self.assertEqual(c.stats()["evictions"], 0)
        c1, clock1, _ = make(max_size=1)
        c1.get("x"); c1.get("y"); c1.get("x")
        self.assertEqual((len(c1), c1.stats()["evictions"]), (1, 2))

    def test_constructor_validation(self):
        for kwargs in ({"ttl": 0}, {"ttl": -1}, {"ttl": 5, "max_size": 0}, {"ttl": 5, "max_size": 1.5}):
            with self.assertRaises(ValueError, msg=kwargs):
                TTLCache(str, **kwargs)
        TTLCache(str, ttl=0.5, max_size=1)
''',
    fix={'cache.py': lambda s: r'''"""A read-through cache with a time-to-live."""
import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, loader, ttl, max_size=None, clock=time.monotonic):
        if not ttl > 0:
            raise ValueError("ttl must be > 0")
        if max_size is not None and (type(max_size) is not int or max_size < 1):
            raise ValueError("max_size must be None or an int >= 1")
        self.loader = loader
        self.ttl = ttl
        self.max_size = max_size
        self.clock = clock
        self.data = OrderedDict()  # key -> (value, stored_at), least recently used first
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def _fresh(self, entry, now):
        return now - entry[1] < self.ttl

    def _drop_expired(self):
        now = self.clock()
        for key in [k for k, e in self.data.items() if not self._fresh(e, now)]:
            del self.data[key]

    def get(self, key):
        entry = self.data.get(key)
        if entry is not None and self._fresh(entry, self.clock()):
            self.hits += 1
            self.data.move_to_end(key)
            return entry[0]
        self.misses += 1
        self.data.pop(key, None)
        value = self.loader(key)
        if self.max_size is not None and len(self.data) >= self.max_size:
            self._drop_expired()
            while len(self.data) >= self.max_size:
                self.data.popitem(last=False)
                self.evictions += 1
        self.data[key] = (value, self.clock())
        return value

    def invalidate(self, key):
        self.data.pop(key, None)

    def invalidate_where(self, predicate):
        now, removed = self.clock(), 0
        for key in [k for k in self.data if predicate(k)]:
            removed += self._fresh(self.data.pop(key), now)
        return removed

    def clear(self):
        self.data.clear()

    def __contains__(self, key):
        entry = self.data.get(key)
        return entry is not None and self._fresh(entry, self.clock())

    def __len__(self):
        now = self.clock()
        return sum(self._fresh(e, now) for e in self.data.values())

    def stats(self):
        return {"hits": self.hits, "misses": self.misses, "evictions": self.evictions}
'''},
)

# 4. Dependency ordering with cycle detection ---------------------------------------
TASKS['build-order'] = dict(
    area='dependency ordering with cycle detection',
    instruction="""`build_order(targets, only=None)` in deps.py orders build targets but crashes or gives wrong answers on many inputs. `targets` maps a name to the list of names it depends on. Make it follow this spec:

- Return a list in which each name appears once and comes after every name it depends on.
- A dependency that is not a key of `targets` is a target with no dependencies (and is in the result).
- Whenever several names could come next, the alphabetically smallest comes first, so the result is unique. Duplicate dependencies count once.
- `only` (an iterable of names): return just those names and everything they depend on, directly or not, ordered by the same rules. A name in `only` that is neither a key nor a dependency anywhere raises KeyError. Cycles that `only` does not need are ignored.
- If the names to order contain a cycle (a name depending on itself counts), raise `CycleError`. Its `cycle` attribute lists distinct names on one cycle, each depending on the next and the last depending on the first, starting with the alphabetically smallest of them; `str(error)` is like `a -> b -> a`.
- `targets` is never modified.

Make sure `python3 -m unittest` passes.""",
    files={
        'deps.py': r'''"""Work out the order to build targets in."""


class CycleError(Exception):
    def __init__(self, cycle):
        super().__init__(" -> ".join(cycle + cycle[:1]))
        self.cycle = cycle


def build_order(targets, only=None):
    """targets maps a name to the names it depends on. Returns names, dependencies first."""
    indegree = {name: len(deps) for name, deps in targets.items()}
    users = {name: [] for name in targets}
    for name, deps in targets.items():
        for d in deps:
            users[d].append(name)
    ready = [n for n, k in indegree.items() if k == 0]
    order = []
    while ready:
        n = ready.pop(0)
        order.append(n)
        for u in users[n]:
            indegree[u] -= 1
            if indegree[u] == 0:
                ready.append(u)
    return order
''',
        'test_deps.py': r'''import unittest
from deps import build_order, CycleError


class DepsTest(unittest.TestCase):
    def test_chain(self):
        self.assertEqual(build_order({"app": ["lib", "util"], "lib": ["util"], "util": []}), ["util", "lib", "app"])

    def test_ties_alphabetical(self):
        self.assertEqual(build_order({"b": [], "a": [], "c": ["b", "a"]}), ["a", "b", "c"])

    def test_cycle(self):
        with self.assertRaises(CycleError):
            build_order({"a": ["b"], "b": ["a"]})


if __name__ == "__main__":
    unittest.main()
''',
    },
    hidden=r'''import copy, heapq, random, unittest
from deps import build_order, CycleError


def reference(targets):
    graph = {}
    for n, ds in targets.items():
        graph.setdefault(n, set()).update(ds)
        for d in ds:
            graph.setdefault(d, set())
    indeg = {n: len(ds) for n, ds in graph.items()}
    ready = [n for n, k in indeg.items() if k == 0]
    heapq.heapify(ready)
    out = []
    while ready:
        n = heapq.heappop(ready)
        out.append(n)
        for m, ds in graph.items():
            if n in ds:
                indeg[m] -= 1
                if indeg[m] == 0:
                    heapq.heappush(ready, m)
    return out


class Hidden(unittest.TestCase):
    def assertCycle(self, targets, members=None, exact=None, **kw):
        with self.assertRaises(CycleError) as e:
            build_order(targets, **kw)
        cyc = e.exception.cycle
        self.assertEqual(len(cyc), len(set(cyc)), cyc)
        self.assertEqual(cyc[0], min(cyc), cyc)
        for a, b in zip(cyc, cyc[1:] + cyc[:1]):
            self.assertIn(b, targets.get(a, []), (cyc, a, b))
        if members is not None:
            self.assertTrue(set(cyc) <= set(members), cyc)
        if exact is not None:
            self.assertEqual(cyc, exact)
            self.assertEqual(str(e.exception), " -> ".join(exact + exact[:1]))

    def test_orders(self):
        self.assertEqual(build_order({}), [])
        self.assertEqual(build_order({"x": []}), ["x"])
        diamond = {"d": ["b", "c"], "b": ["a"], "c": ["a"], "a": []}
        self.assertEqual(build_order(diamond), ["a", "b", "c", "d"])
        self.assertEqual(build_order({"z": [], "m": ["z"], "a": ["m"], "b": []}), ["b", "z", "m", "a"])

    def test_implicit_and_duplicate_deps(self):
        self.assertEqual(build_order({"app": ["json", "http", "json"]}), ["http", "json", "app"])
        self.assertEqual(build_order({"a": ["b", "b", "b"], "c": ["b"]}), ["b", "a", "c"])

    def test_random_graphs_match_reference(self):
        random.seed(3)
        for _ in range(200):
            names = random.sample("abcdefghijklmnop", random.randint(1, 12))
            targets = {}
            for i, n in enumerate(names):
                if random.random() < 0.8:
                    targets[n] = random.sample(names[i + 1:], min(len(names) - i - 1, random.randint(0, 3)))
            got = build_order(targets)
            self.assertEqual(got, reference(targets), targets)

    def test_only(self):
        t = {"app": ["lib"], "lib": ["core"], "tool": ["core", "cli"], "docs": [], "x": ["y"], "y": ["x"]}
        self.assertEqual(build_order(t, only=["app"]), ["core", "lib", "app"])
        self.assertEqual(build_order(t, only=("tool", "docs")), ["cli", "core", "docs", "tool"])
        self.assertEqual(build_order(t, only=["core"]), ["core"])
        self.assertEqual(build_order(t, only=[]), [])
        self.assertEqual(build_order(t, only=iter(["lib"])), ["core", "lib"])
        with self.assertRaises(KeyError):
            build_order(t, only=["app", "nope"])
        self.assertCycle(t, exact=["x", "y"], only=["x"])
        self.assertCycle(t, exact=["x", "y"])

    def test_cycles(self):
        self.assertCycle({"a": ["a"]}, exact=["a"])
        self.assertCycle({"a": ["b"], "b": ["a"]}, exact=["a", "b"])
        self.assertCycle({"c": ["a"], "a": ["b"], "b": ["c"]}, exact=["a", "b", "c"])
        self.assertCycle({"top": ["q"], "q": ["r", "base"], "r": ["s"], "s": ["q"], "base": []}, exact=["q", "r", "s"])
        self.assertCycle({"m": ["n", "z"], "n": ["m"], "z": ["m"], "free": []}, members="mnz")
        self.assertCycle({"a": ["b"], "b": ["c", "d"], "c": ["a"], "d": ["b"], "e": ["e"]}, members="abcde")

    def test_input_unchanged(self):
        t = {"a": ["b", "c"], "b": ["c"], "c": [], "d": ["d"]}
        before = copy.deepcopy(t)
        build_order(t, only=["a"])
        with self.assertRaises(CycleError):
            build_order(t)
        self.assertEqual(t, before)
''',
    fix={'deps.py': lambda s: s[:s.index('def build_order(')] + r'''def build_order(targets, only=None):
    """targets maps a name to the names it depends on. Returns names, dependencies first."""
    import heapq
    graph = {}
    for name, deps in targets.items():
        graph.setdefault(name, set()).update(deps)
        for d in deps:
            graph.setdefault(d, set())
    if only is not None:
        stack = list(only)
        for name in stack:
            if name not in graph:
                raise KeyError(name)
        keep = set()
        while stack:
            name = stack.pop()
            if name not in keep:
                keep.add(name)
                stack.extend(graph[name])
        graph = {n: graph[n] for n in keep}
    indegree = {name: len(deps) for name, deps in graph.items()}
    users = {name: [] for name in graph}
    for name, deps in graph.items():
        for d in deps:
            users[d].append(name)
    ready = [n for n, k in indegree.items() if k == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        n = heapq.heappop(ready)
        order.append(n)
        for u in users[n]:
            indegree[u] -= 1
            if indegree[u] == 0:
                heapq.heappush(ready, u)
    if len(order) < len(graph):
        rest = {n: graph[n] for n in graph if indegree[n] > 0}
        node, path = min(rest), []
        while node not in path:
            path.append(node)
            node = min(d for d in rest[node] if d in rest)
        cycle = path[path.index(node):]
        i = cycle.index(min(cycle))
        raise CycleError(cycle[i:] + cycle[:i])
    return order
'''},
)

# 5. Subtle timezone and rounding bugs, partly shown by the visible test ------------
TASKS['daily-totals'] = dict(
    area='subtle timezone and rounding bugs (visible test shows only one)',
    instruction="""`daily_totals` in report.py gives wrong totals for some days; a customer in Tokyo noticed first. Fix it so it meets this contract:

- `daily_totals(rows, tz="+00:00", start=None, end=None)`; `rows` is an iterable of `(timestamp, amount)`.
- `timestamp` is ISO 8601 like `2026-03-01T23:30:00`, optionally with an offset `+HH:MM`, `-HH:MM` or `Z`; without an offset it is UTC.
- Each row counts on its local calendar date in `tz`, a fixed offset `+HH:MM`, `-HH:MM` or `Z`; any other `tz` raises ValueError.
- `amount` is a decimal string such as `"12.5"` or `"-3.005"`. A day's amounts are summed exactly (no float error); the day's total is then rounded to cents, halves away from zero (`0.125` -> `0.13`, `-0.125` -> `-0.13`), and formatted with exactly two decimals. A zero total is `"0.00"`, never `"-0.00"`.
- The result maps `"YYYY-MM-DD"` to the total string, with keys in ascending date order.
- `start` and `end` are optional ISO dates, both inclusive: rows whose local date is outside them are left out. When both are given, every date from start to end is in the result (`"0.00"` for days without rows). `start` after `end` raises ValueError.

Make sure `python3 -m unittest` passes.""",
    files={
        'report.py': r'''"""Daily sales totals for the finance report."""
from datetime import date, timedelta


def daily_totals(rows, tz="+00:00", start=None, end=None):
    """Sum (timestamp, amount) rows per local date. See the contract in the docs."""
    totals = {}
    for ts, amount in rows:
        day = ts[:10]
        totals[day] = totals.get(day, 0.0) + float(amount)
    if start and end:
        d = date.fromisoformat(start)
        while d < date.fromisoformat(end):
            totals.setdefault(d.isoformat(), 0.0)
            d += timedelta(days=1)
    return {d: f"{round(v, 2):.2f}" for d, v in sorted(totals.items())}
''',
        'test_report.py': r'''import unittest
from report import daily_totals


class ReportTest(unittest.TestCase):
    def test_one_utc_day(self):
        rows = [("2026-03-01T10:00:00Z", "1.50"), ("2026-03-01T23:00:00+00:00", "2.25")]
        self.assertEqual(daily_totals(rows), {"2026-03-01": "3.75"})

    def test_tokyo(self):
        rows = [("2026-03-01T20:00:00Z", "5.00"), ("2026-03-01T10:00:00Z", "1.00")]
        self.assertEqual(daily_totals(rows, tz="+09:00"), {"2026-03-01": "1.00", "2026-03-02": "5.00"})


if __name__ == "__main__":
    unittest.main()
''',
    },
    hidden=r'''import unittest
from report import daily_totals


class Hidden(unittest.TestCase):
    def test_row_offsets_and_naive_times(self):
        rows = [("2026-03-01T23:30:00-05:00", "1.00"), ("2026-03-02T01:00:00+02:00", "2.00"), ("2026-03-01T23:30:00", "4.00")]
        self.assertEqual(daily_totals(rows), {"2026-03-01": "6.00", "2026-03-02": "1.00"})
        self.assertEqual(daily_totals(rows, tz="Z"), {"2026-03-01": "6.00", "2026-03-02": "1.00"})
        self.assertEqual(daily_totals(rows, tz="+01:00"), {"2026-03-02": "7.00"})
        self.assertEqual(daily_totals([("2026-03-01T19:00:00Z", "1")], tz="+05:30"), {"2026-03-02": "1.00"})
        self.assertEqual(daily_totals([("2026-03-01T03:00:00Z", "1")], tz="-03:30"), {"2026-02-28": "1.00"})
        self.assertEqual(daily_totals([("2024-02-29T23:59:59-00:01", "1")]), {"2024-03-01": "1.00"})

    def test_exact_sums_and_half_up(self):
        cases = [(["0.125"], "0.13"), (["0.1", "0.2"], "0.30"), (["2.675"], "2.68"), (["1.005"], "1.01"),
                 (["-0.125"], "-0.13"), (["0.004", "0.004"], "0.01"), (["0.004", "-0.008"], "0.00"),
                 (["-0.001"], "0.00"), (["1234567.895"], "1234567.90"), (["0.1"] * 10, "1.00"), (["10", "-2.5"], "7.50"),
                 (["0.135", "0.2"], "0.34")]
        for amounts, want in cases:
            rows = [("2026-01-05T12:00:00Z", a) for a in amounts]
            self.assertEqual(daily_totals(rows), {"2026-01-05": want}, amounts)

    def test_range(self):
        rows = [("2026-03-01T12:00:00Z", "1"), ("2026-03-03T12:00:00Z", "2"), ("2026-03-05T12:00:00Z", "4"),
                ("2026-02-27T12:00:00Z", "8")]
        self.assertEqual(daily_totals(rows, start="2026-03-01", end="2026-03-04"),
                         {"2026-03-01": "1.00", "2026-03-02": "0.00", "2026-03-03": "2.00", "2026-03-04": "0.00"})
        self.assertEqual(daily_totals(rows, start="2026-03-03"), {"2026-03-03": "2.00", "2026-03-05": "4.00"})
        self.assertEqual(daily_totals(rows, end="2026-03-01"), {"2026-02-27": "8.00", "2026-03-01": "1.00"})
        self.assertEqual(daily_totals([], start="2026-03-01", end="2026-03-01"), {"2026-03-01": "0.00"})
        self.assertEqual(daily_totals([("2026-03-01T23:00:00Z", "3")], tz="+02:00", start="2026-03-01", end="2026-03-01"),
                         {"2026-03-01": "0.00"})
        with self.assertRaises(ValueError):
            daily_totals(rows, start="2026-03-05", end="2026-03-01")

    def test_order_of_keys(self):
        rows = [("2026-03-0%dT12:00:00Z" % d, "1") for d in (5, 1, 9, 3)] + [("2025-12-31T12:00:00Z", "1")]
        self.assertEqual(list(daily_totals(rows)), ["2025-12-31", "2026-03-01", "2026-03-03", "2026-03-05", "2026-03-09"])

    def test_bad_tz(self):
        for tz in ("09:00", "+9", "EST", "+0900", ""):
            with self.assertRaises(ValueError, msg=tz):
                daily_totals([("2026-03-01T12:00:00Z", "1")], tz=tz)
''',
    fix={'report.py': lambda s: r'''"""Daily sales totals for the finance report."""
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP


def _zone(text):
    if text == "Z":
        return timezone.utc
    m = re.fullmatch(r"([+-])(\d\d):(\d\d)", text or "")
    if not m:
        raise ValueError(f"bad offset: {text!r}")
    delta = timedelta(hours=int(m[2]), minutes=int(m[3]))
    return timezone(-delta if m[1] == "-" else delta)


def _local_date(ts, zone):
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(zone).date()


def _cents(total):
    q = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return "0.00" if q == 0 else f"{q:.2f}"


def daily_totals(rows, tz="+00:00", start=None, end=None):
    """Sum (timestamp, amount) rows per local date. See the contract in the docs."""
    zone = _zone(tz)
    lo = date.fromisoformat(start) if start else None
    hi = date.fromisoformat(end) if end else None
    if lo and hi and lo > hi:
        raise ValueError("start is after end")
    totals = {}
    for ts, amount in rows:
        day = _local_date(ts, zone)
        if (lo and day < lo) or (hi and day > hi):
            continue
        totals[day] = totals.get(day, Decimal(0)) + Decimal(amount)
    if lo and hi:
        d = lo
        while d <= hi:
            totals.setdefault(d, Decimal(0))
            d += timedelta(days=1)
    return {d.isoformat(): _cents(v) for d, v in sorted(totals.items())}
'''},
)


def validate():
    """As agentic-v2's validate(): a reference fix may also create new files; also checks the starter fails its visible tests."""
    import tempfile
    bad = 0
    for name, task in TASKS.items():
        with tempfile.TemporaryDirectory() as start, tempfile.TemporaryDirectory() as fixed:
            write_files(task, start)
            passed, total, _ = run_hidden(task, start)
            start_vis_ok, _ = run_visible(start)
            write_files(task, fixed)
            for rel, change in task['fix'].items():
                p = Path(fixed, rel)
                p.write_text(change(p.read_text() if p.exists() else ''))
            fpassed, ftotal, out = run_hidden(task, fixed)
            vis_ok, vis_out = run_visible(fixed)
            ok = ftotal and fpassed == ftotal and passed < (total or 1) and vis_ok and not start_vis_ok
            print(f"{'ok  ' if ok else 'FAIL'} {name}: start {passed}/{total} (visible {'pass' if start_vis_ok else 'fail'}), "
                  f"reference fix {fpassed}/{ftotal}, visible tests with fix {'pass' if vis_ok else 'FAIL'}")
            if not ok:
                bad += 1
                print(out[-1500:], vis_out[-800:])
    return bad


if __name__ == '__main__':
    sys.exit(1 if validate() else 0)
