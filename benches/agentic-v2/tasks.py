"""agentic-v2: held-out agentic tasks, same format and grading as agentic-v1.

Written before any tuning of the pi extensions for gpt-oss (2026-11-15) and kept out of the
tuning loop: only their totals are read when judging a change, never their transcripts.
Each task covers an area agentic-v1 does not: parsing, thread safety, a refactor across
files, an API client with error handling, and a performance fix with a hard time limit.

    .venv/bin/python benches/agentic-v2/tasks.py     # validate every task
"""
import importlib.util, sys
from pathlib import Path

_v1 = importlib.util.spec_from_file_location('agentic_v1_tasks', Path(__file__).resolve().parents[1] / 'agentic-v1' / 'tasks.py')
V1 = importlib.util.module_from_spec(_v1)
_v1.loader.exec_module(V1)
write_files, run_hidden, run_visible = V1.write_files, V1.run_hidden, V1.run_visible

TASKS = {}

# 1. Parsing from a written spec --------------------------------------------------
TASKS['parse-config'] = dict(
    area='parser from a written spec',
    instruction="""`parse(text)` in config.py reads an INI-style config but only handles the simplest files. Make it follow this spec:

- The result maps section name -> {key: value} (all strings). Keys before any `[section]` header go in the section "" (empty string), which is always present, even if empty.
- `[name]` starts a section (surrounding whitespace allowed). A section that appears again is merged into the earlier one.
- `key = value`: whitespace around key and value is stripped; keys are lowercased; the value may contain `=`. A repeated key keeps the last value.
- Blank lines are ignored. Lines whose first non-blank character is `#` or `;` are comments.
- In an unquoted value, a `#` or `;` that follows whitespace starts a comment, which is removed (so `x#y` stays `x#y`).
- A value in double quotes has the quotes removed and the escapes \\\\ \\" and \\n processed; `#` and `;` inside the quotes are kept, and a comment may follow the closing quote.
- Anything else (a line with no `=`, an empty key, an unclosed `[`, an unterminated quote, text after a closing quote other than a comment) raises `ConfigError` whose `line` attribute is the 1-based line number.

Make sure `python3 -m unittest` passes.""",
    files={
        'config.py': '''"""A small INI-style config reader."""


class ConfigError(Exception):
    def __init__(self, line, message):
        super().__init__(f"line {line}: {message}")
        self.line = line


def parse(text):
    data = {"": {}}
    section = ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            section = line[1:-1]
            data[section] = {}
            continue
        key, value = line.split("=")
        data[section][key.strip()] = value.strip()
    return data
''',
        'test_config.py': '''import unittest
from config import parse, ConfigError


class ConfigTest(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(parse("a = 1\\n[db]\\nhost = x\\n"), {"": {"a": "1"}, "db": {"host": "x"}})

    def test_value_with_equals(self):
        self.assertEqual(parse("[x]\\nurl = a=b\\n")["x"]["url"], "a=b")

    def test_bad_line(self):
        with self.assertRaises(ConfigError) as e:
            parse("[x]\\nnot a setting\\n")
        self.assertEqual(e.exception.line, 2)


if __name__ == "__main__":
    unittest.main()
''',
    },
    hidden=r'''import unittest
from config import parse, ConfigError


class Hidden(unittest.TestCase):
    def test_sections_and_keys(self):
        text = "Top = 1\n\n  [ db ]  \nHost = h\nport=5432\n[web]\nport = 80\n[db]\nport = 6543\nuser=u\n"
        self.assertEqual(parse(text), {"": {"top": "1"}, "db": {"host": "h", "port": "6543", "user": "u"}, "web": {"port": "80"}})
        self.assertEqual(parse(""), {"": {}})
        self.assertEqual(parse("[only]\n"), {"": {}, "only": {}})

    def test_comments(self):
        text = "# c\n; c\n   # indented\n[s]\na = 1  # note\nb = x#y\nc = v ;semi\nd = \ne=  spaced  value  \n"
        self.assertEqual(parse(text), {"": {}, "s": {"a": "1", "b": "x#y", "c": "v", "d": "", "e": "spaced  value"}})

    def test_quotes(self):
        text = '[q]\na = "hello # world"\nb = "say \\"hi\\""\nc = "a\\nb"\nd = ""\ne = "v"   # trailing\nf = "back\\\\slash"\ng = "semi;colon"\n'
        self.assertEqual(parse(text)["q"], {"a": "hello # world", "b": 'say "hi"', "c": "a\nb", "d": "", "e": "v",
                                            "f": "back\\slash", "g": "semi;colon"})

    def test_windows_newlines(self):
        self.assertEqual(parse("[a]\r\nk = v\r\n"), {"": {}, "a": {"k": "v"}})

    def test_errors_have_line_numbers(self):
        for text, line in [("a=1\nb=2\njust text\n", 3), ("[unclosed\n", 1), ("[s]\n = value\n", 2),
                           ('[s]\n\nk = "abc\n', 3), ('k = "a" b\n', 1), ("# ok\n[s]\nk\n", 3)]:
            with self.assertRaises(ConfigError, msg=text) as e:
                parse(text)
            self.assertEqual(e.exception.line, line, text)
''',
    fix={'config.py': lambda s: s[:s.index('def parse(text):')] + r'''def _value(raw, n):
    raw = raw.strip()
    if not raw.startswith('"'):
        for i, ch in enumerate(raw):
            if ch in "#;" and i and raw[i - 1].isspace():
                return raw[:i].rstrip()
        return raw
    out, i = [], 1
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw):
            out.append({"n": "\n", '"': '"', "\\": "\\"}.get(raw[i + 1], "\\" + raw[i + 1]))
            i += 2
            continue
        if ch == '"':
            rest = raw[i + 1:].strip()
            if rest and rest[0] not in "#;":
                raise ConfigError(n, "text after closing quote")
            return "".join(out)
        out.append(ch)
        i += 1
    raise ConfigError(n, "unterminated quote")


def parse(text):
    data = {"": {}}
    section = ""
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("["):
            if not line.endswith("]"):
                raise ConfigError(n, "unclosed section")
            section = line[1:-1].strip()
            data.setdefault(section, {})
            continue
        if "=" not in line:
            raise ConfigError(n, "expected key = value")
        key, raw = line.split("=", 1)
        key = key.strip().lower()
        if not key:
            raise ConfigError(n, "empty key")
        data[section][key] = _value(raw, n)
    return data
'''},
)

# 2. Thread safety ----------------------------------------------------------------
TASKS['thread-safe-metrics'] = dict(
    area='concurrency-safe code',
    instruction="""`Metrics` in metrics.py is shared by many worker threads (see worker.py) and loses updates under load. Make it thread-safe:

- no increment is ever lost;
- `move(src, dst, amount)` is atomic: it never leaves a counter negative and never loses or creates counts, even when other threads move counts the other way at the same time;
- `snapshot()` always returns a consistent copy (for example, the sum of counters only ever changes by `incr` calls, never half-way through a `move`);
- nothing may deadlock.

Keep the public API and behaviour (including the ValueError from `move`). Make sure `python3 -m unittest` passes.""",
    files={
        'metrics.py': '''"""Counters shared by worker threads."""
import time


class Metrics:
    def __init__(self):
        self.counters = {}

    def _trace(self, op, name):
        """Debug hook; subclasses log here. Yields to other threads like real logging would."""
        time.sleep(0)

    def incr(self, name, by=1):
        value = self.counters.get(name, 0)
        self._trace("incr", name)
        self.counters[name] = value + by

    def get(self, name):
        return self.counters.get(name, 0)

    def move(self, src, dst, amount):
        """Move `amount` from counter src to counter dst. ValueError if src has less than amount."""
        if amount <= 0 or self.get(src) < amount:
            raise ValueError(f"cannot move {amount} from {src}")
        self.incr(src, -amount)
        self.incr(dst, amount)

    def snapshot(self):
        return dict(self.counters)
''',
        'worker.py': '''"""Runs jobs on a thread pool and counts outcomes in a shared Metrics."""
from concurrent.futures import ThreadPoolExecutor

from metrics import Metrics


def run_jobs(jobs, metrics=None, workers=8):
    metrics = metrics or Metrics()

    def one(job):
        try:
            job()
            metrics.incr("ok")
        except Exception:
            metrics.incr("failed")

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, jobs))
    return metrics
''',
        'test_metrics.py': '''import unittest
from metrics import Metrics
from worker import run_jobs


class MetricsTest(unittest.TestCase):
    def test_single_thread(self):
        m = Metrics()
        m.incr("a"); m.incr("a", 4)
        m.move("a", "b", 2)
        self.assertEqual(m.snapshot(), {"a": 3, "b": 2})
        with self.assertRaises(ValueError):
            m.move("a", "b", 10)

    def test_run_jobs_counts_everything(self):
        jobs = [(lambda: None) for _ in range(3000)] + [(lambda: 1 / 0) for _ in range(1000)]
        m = run_jobs(jobs)
        self.assertEqual(m.snapshot(), {"ok": 3000, "failed": 1000})


if __name__ == "__main__":
    unittest.main()
''',
    },
    hidden='''import threading, unittest
from metrics import Metrics


def run(threads, timeout=30):
    for t in threads:
        t.daemon = True
        t.start()
    for t in threads:
        t.join(timeout)
    return not any(t.is_alive() for t in threads)


class Hidden(unittest.TestCase):
    def test_no_lost_increments(self):
        m = Metrics()
        ts = [threading.Thread(target=lambda: [m.incr("n") for _ in range(4000)]) for _ in range(8)]
        self.assertTrue(run(ts), "deadlock or too slow")
        self.assertEqual(m.get("n"), 32000)

    def test_moves_are_atomic_and_snapshots_consistent(self):
        m = Metrics(); m.incr("a", 500); m.incr("b", 500)
        bad, stop = [], threading.Event()

        def mover(src, dst):
            for _ in range(3000):
                try:
                    m.move(src, dst, 7)
                except ValueError:
                    pass

        def watcher():
            while not stop.is_set():
                s = m.snapshot()
                if sum(s.values()) != 1000 or min(s.values()) < 0:
                    bad.append(s)

        w = threading.Thread(target=watcher, daemon=True); w.start()
        ok = run([threading.Thread(target=mover, args=p) for p in [("a", "b"), ("b", "a")] * 3])
        stop.set(); w.join(10)
        self.assertTrue(ok, "deadlock or too slow")
        self.assertEqual(m.get("a") + m.get("b"), 1000)
        self.assertGreaterEqual(min(m.get("a"), m.get("b")), 0)
        self.assertEqual(bad[:3], [])

    def test_errors_still_raised(self):
        m = Metrics(); m.incr("a", 3)
        for args in [("a", "b", 4), ("a", "b", 0), ("zz", "a", 1)]:
            with self.assertRaises(ValueError):
                m.move(*args)
        self.assertEqual(m.snapshot(), {"a": 3})
''',
    fix={'metrics.py': lambda s: s.replace('import time\n', 'import threading, time\n')
         .replace('        self.counters = {}\n', '        self.counters = {}\n        self._lock = threading.RLock()\n')
         .replace('''        value = self.counters.get(name, 0)
        self._trace("incr", name)
        self.counters[name] = value + by''', '''        with self._lock:
            value = self.counters.get(name, 0)
            self._trace("incr", name)
            self.counters[name] = value + by''')
         .replace('''        if amount <= 0 or self.get(src) < amount:
            raise ValueError(f"cannot move {amount} from {src}")
        self.incr(src, -amount)
        self.incr(dst, amount)''', '''        with self._lock:
            if amount <= 0 or self.get(src) < amount:
                raise ValueError(f"cannot move {amount} from {src}")
            self.incr(src, -amount)
            self.incr(dst, amount)''')
         .replace('        return dict(self.counters)', '        with self._lock:\n            return dict(self.counters)')},
)

# 3. A refactor across files ------------------------------------------------------
TASKS['refactor-money'] = dict(
    area='refactor across several files',
    instruction="""Money formatting is duplicated in shop/cart.py, shop/invoice.py and shop/receipt.py, and the copies disagree. Move it into one function `format_cents(cents, currency="USD")` in a new module shop/money.py, make all three modules use it, and delete their private copies.

The one format, everywhere:
- USD `$1,234.50`, EUR `€1,234.50` (thousands separators, two decimals);
- JPY `¥1,234` (the amount is whole yen, no decimals);
- negative amounts put the sign first: `-$1.50`, `-¥300`;
- any other currency raises ValueError.

receipt.py today gets negative amounts and thousands separators wrong; that is fixed by the change. Keep every other output and function signature the same. Make sure `python3 -m unittest` passes.""",
    files={
        'shop/__init__.py': '',
        'shop/cart.py': '''class Cart:
    def __init__(self, currency="USD"):
        self.currency = currency
        self.lines = []

    def add(self, name, cents, qty=1):
        self.lines.append((name, cents, qty))

    def total_cents(self):
        return sum(cents * qty for _, cents, qty in self.lines)

    def total_text(self):
        return "Total: " + _fmt(self.total_cents(), self.currency)


def _fmt(cents, currency):
    symbol = {"USD": "$", "EUR": "€", "JPY": "¥"}[currency]
    if currency == "JPY":
        return f"{symbol}{cents:,}"
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{symbol}{cents // 100:,}.{cents % 100:02d}"
''',
        'shop/invoice.py': '''def _money(cents, currency="USD"):
    symbols = {"USD": "$", "EUR": "€", "JPY": "¥"}
    if currency not in symbols:
        raise ValueError(currency)
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    if currency == "JPY":
        return f"{sign}{symbols[currency]}{cents:,}"
    return f"{sign}{symbols[currency]}{cents // 100:,}.{cents % 100:02d}"


def line(description, qty, unit_cents, currency="USD"):
    return f"{description:<12}{qty:>3} x {_money(unit_cents, currency):>10} = {_money(qty * unit_cents, currency):>12}"


def invoice(rows, currency="USD"):
    body = [line(d, q, u, currency) for d, q, u in rows]
    total = sum(q * u for _, q, u in rows)
    return "\\n".join(body + [f"{'TOTAL':<29}{_money(total, currency):>12}"])
''',
        'shop/receipt.py': '''def _format_price(cents):
    return "$" + "%d.%02d" % (cents // 100, cents % 100)


def render(items, paid_cents):
    lines = [f"{name}: {_format_price(cents)}" for name, cents in items]
    total = sum(cents for _, cents in items)
    lines.append(f"Total: {_format_price(total)}")
    lines.append(f"Change: {_format_price(paid_cents - total)}")
    return "\\n".join(lines)
''',
        'test_shop.py': '''import unittest
from shop.cart import Cart
from shop.invoice import line, invoice
from shop.receipt import render


class ShopTest(unittest.TestCase):
    def test_cart(self):
        c = Cart(); c.add("tea", 450, 2); c.add("cake", 125000)
        self.assertEqual(c.total_text(), "Total: $1,259.00")

    def test_invoice_line(self):
        self.assertEqual(line("tea", 2, 450), "tea           2 x      $4.50 =        $9.00")

    def test_invoice_total(self):
        self.assertTrue(invoice([("tea", 2, 450)], "EUR").endswith("€9.00"))

    def test_receipt(self):
        self.assertEqual(render([("tea", 450)], 500), "tea: $4.50\\nTotal: $4.50\\nChange: $0.50")


if __name__ == "__main__":
    unittest.main()
''',
    },
    hidden='''import ast, unittest
from pathlib import Path


class Hidden(unittest.TestCase):
    def test_format_cents(self):
        from shop.money import format_cents
        cases = [((0,), "$0.00"), ((5,), "$0.05"), ((123450,), "$1,234.50"), ((-150,), "-$1.50"), ((100000000,), "$1,000,000.00"),
                 ((123450, "EUR"), "€1,234.50"), ((-5, "EUR"), "-€0.05"), ((1234, "JPY"), "¥1,234"), ((-300, "JPY"), "-¥300"),
                 ((0, "JPY"), "¥0")]
        for args, want in cases:
            self.assertEqual(format_cents(*args), want, args)
        for bad in ("GBP", "usd", ""):
            with self.assertRaises(ValueError):
                format_cents(100, bad)

    def test_modules_use_it(self):
        from shop.cart import Cart
        from shop.invoice import line, invoice
        from shop.receipt import render
        c = Cart("JPY"); c.add("a", 500); c.add("refund", -800)
        self.assertEqual(c.total_text(), "Total: -¥300")
        with self.assertRaises(ValueError):
            Cart("GBP").total_text()
        self.assertEqual(line("x", 3, 33334, "EUR"), 'x             3 x    €333.34 =    €1,000.02')
        self.assertEqual(invoice([("a", 1, 100), ("b", 2, -250)]).splitlines()[-1], "TOTAL" + " " * 24 + "      -$4.00")
        self.assertEqual(render([("tv", 129999), ("bag", 1)], 120000), "tv: $1,299.99\\nbag: $0.01\\nTotal: $1,300.00\\nChange: -$100.00")

    def test_no_private_copies(self):
        root = Path(__file__).parent / "shop"
        for name in ("cart.py", "invoice.py", "receipt.py"):
            tree = ast.parse((root / name).read_text())
            funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
            self.assertEqual(funcs - {"line", "invoice", "render"}, set(), name)
            self.assertIn("format_cents", (root / name).read_text(), name)
''',
    fix={'shop/money.py': lambda s: '''SYMBOLS = {"USD": "$", "EUR": "€", "JPY": "¥"}


def format_cents(cents, currency="USD"):
    if currency not in SYMBOLS:
        raise ValueError(currency)
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    if currency == "JPY":
        return f"{sign}{SYMBOLS[currency]}{cents:,}"
    return f"{sign}{SYMBOLS[currency]}{cents // 100:,}.{cents % 100:02d}"
''', 'shop/cart.py': lambda s: 'from .money import format_cents\n\n\n' + s[:s.index('\n\ndef _fmt')].replace('_fmt(', 'format_cents(') + '\n',
         'shop/invoice.py': lambda s: 'from .money import format_cents\n\n\n' + s[s.index('def line'):].replace('_money(', 'format_cents('),
         'shop/receipt.py': lambda s: 'from .money import format_cents\n\n\n' + s[s.index('def render'):].replace('_format_price(', 'format_cents(')},
)

# 4. An API client with error handling --------------------------------------------
TASKS['api-client'] = dict(
    area='API client with retries and error handling',
    instruction="""Finish `ApiClient` in client.py. The transport is injected (no network in tests): `transport(method, path, params)` returns `(status, headers, body_text)` or raises `TimeoutError` / `ConnectionError`.

- Retry a request on status 429 or 500-599, and on TimeoutError or ConnectionError, up to `retries` attempts in total (default 3). Before attempt k+1 wait with `self.sleep(0.5 * 2 ** (k - 1))` (0.5, 1, 2, ...), except that a 429 with a `Retry-After` header (whole seconds) waits that many seconds instead. Never sleep after the last attempt.
- When retries run out raise `ApiError`: `status` is the last status, or None after a network error.
- 404 raises `NotFound` (a subclass of `ApiError`). Any other 4xx raises `ApiError` with `status` and `message` taken from a JSON body `{"error": "..."}`, or the raw body text if it is not such JSON. 4xx other than 429 are never retried.
- A 2xx whose body is not valid JSON raises `ApiError` with that status.
- `get_user(user_id)` requires a positive int (not bool) id, else ValueError without calling the transport.
- `list_users()` follows pagination: each page is `{"items": [...], "next": cursor or null}`; the first request has no params, later ones pass `{"cursor": cursor}`. It returns all items in order, and raises `ApiError` if a cursor repeats.

Make sure `python3 -m unittest` passes.""",
    files={
        'client.py': '''"""A small client for the users API."""
import json
import time


class ApiError(Exception):
    def __init__(self, status, message=""):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


class NotFound(ApiError):
    pass


class ApiClient:
    def __init__(self, transport, sleep=time.sleep, retries=3):
        self.transport = transport
        self.sleep = sleep
        self.retries = retries

    def _get(self, path, params=None):
        status, headers, body = self.transport("GET", path, params or {})
        return json.loads(body)

    def get_user(self, user_id):
        return self._get(f"/users/{user_id}")

    def list_users(self):
        return self._get("/users")["items"]
''',
        'test_client.py': '''import json, unittest
from client import ApiClient, ApiError


def script(*responses):
    calls = []

    def transport(method, path, params):
        calls.append((method, path, params))
        r = responses[len(calls) - 1]
        if isinstance(r, Exception):
            raise r
        return r
    return transport, calls


class ClientTest(unittest.TestCase):
    def test_get_user(self):
        t, calls = script((200, {}, json.dumps({"id": 7, "name": "Ann"})))
        self.assertEqual(ApiClient(t, sleep=lambda s: None).get_user(7), {"id": 7, "name": "Ann"})
        self.assertEqual(calls, [("GET", "/users/7", {})])

    def test_retries_server_error(self):
        waits = []
        t, calls = script((503, {}, "busy"), (200, {}, "{}"))
        self.assertEqual(ApiClient(t, sleep=waits.append).get_user(1), {})
        self.assertEqual(waits, [0.5])


if __name__ == "__main__":
    unittest.main()
''',
    },
    hidden='''import json, unittest
from client import ApiClient, ApiError, NotFound


def script(*responses):
    calls = []

    def transport(method, path, params):
        calls.append((method, path, dict(params)))
        r = responses[len(calls) - 1]
        if isinstance(r, Exception):
            raise r
        return r
    return transport, calls


def client(*responses, retries=3):
    t, calls = script(*responses)
    waits = []
    return ApiClient(t, sleep=waits.append, retries=retries), calls, waits


class Hidden(unittest.TestCase):
    def test_retry_schedule_and_give_up(self):
        c, calls, waits = client((500, {}, "x"), (502, {}, "x"), (503, {}, "x"), (200, {}, "{}"))
        with self.assertRaises(ApiError) as e:
            c.get_user(1)
        self.assertEqual((e.exception.status, len(calls), waits), (503, 3, [0.5, 1.0]))
        c, calls, waits = client((500, {}, "x"), (500, {}, "x"), (500, {}, "x"), (500, {}, "x"), (200, {}, '{"ok": 1}'), retries=5)
        self.assertEqual(c.get_user(2), {"ok": 1})
        self.assertEqual(waits, [0.5, 1.0, 2.0, 4.0])

    def test_network_errors(self):
        c, calls, waits = client(TimeoutError(), ConnectionError(), (200, {}, '{"id": 3}'))
        self.assertEqual(c.get_user(3), {"id": 3})
        self.assertEqual(waits, [0.5, 1.0])
        c, calls, waits = client(TimeoutError(), TimeoutError(), ConnectionError())
        with self.assertRaises(ApiError) as e:
            c.get_user(3)
        self.assertIsNone(e.exception.status)
        self.assertEqual(len(waits), 2)

    def test_retry_after(self):
        c, calls, waits = client((429, {"Retry-After": "7"}, ""), (429, {}, ""), (200, {}, "{}"))
        self.assertEqual(c.get_user(1), {})
        self.assertEqual(waits, [7, 1.0])

    def test_client_errors_not_retried(self):
        c, calls, waits = client((404, {}, '{"error": "no such user"}'))
        with self.assertRaises(NotFound) as e:
            c.get_user(9)
        self.assertEqual((e.exception.status, len(calls), waits), (404, 1, []))
        self.assertTrue(issubclass(NotFound, ApiError))
        c, calls, waits = client((400, {}, '{"error": "bad id"}'))
        with self.assertRaises(ApiError) as e:
            c.get_user(9)
        self.assertEqual((e.exception.status, e.exception.message, len(calls)), (400, "bad id", 1))
        self.assertNotIsInstance(e.exception, NotFound)
        c, calls, waits = client((403, {}, "<html>forbidden</html>"))
        with self.assertRaises(ApiError) as e:
            c.get_user(9)
        self.assertEqual((e.exception.status, e.exception.message), (403, "<html>forbidden</html>"))

    def test_bad_json(self):
        c, calls, waits = client((200, {}, "not json"))
        with self.assertRaises(ApiError) as e:
            c.get_user(1)
        self.assertEqual(e.exception.status, 200)

    def test_user_id_validation(self):
        for bad in (0, -1, "1", 1.0, True, None):
            c, calls, waits = client()
            with self.assertRaises(ValueError, msg=repr(bad)):
                c.get_user(bad)
            self.assertEqual(calls, [])

    def test_pagination(self):
        pages = [(200, {}, json.dumps({"items": [1, 2], "next": "c1"})), (500, {}, ""),
                 (200, {}, json.dumps({"items": [3], "next": "c2"})), (200, {}, json.dumps({"items": [], "next": None}))]
        c, calls, waits = client(*pages)
        self.assertEqual(c.list_users(), [1, 2, 3])
        self.assertEqual([p for _, _, p in calls], [{}, {"cursor": "c1"}, {"cursor": "c1"}, {"cursor": "c2"}])
        self.assertEqual({path for _, path, _ in calls}, {"/users"})
        loop = [(200, {}, json.dumps({"items": [1], "next": "a"})), (200, {}, json.dumps({"items": [2], "next": "b"})),
                (200, {}, json.dumps({"items": [3], "next": "a"}))]
        c, calls, waits = client(*loop)
        with self.assertRaises(ApiError):
            c.list_users()
''',
    fix={'client.py': lambda s: s[:s.index('    def _get(')] + '''    def _get(self, path, params=None):
        status = None
        for attempt in range(1, self.retries + 1):
            wait = 0.5 * 2 ** (attempt - 1)
            try:
                status, headers, body = self.transport("GET", path, params or {})
            except (TimeoutError, ConnectionError):
                status = None
            else:
                if 200 <= status < 300:
                    try:
                        return json.loads(body)
                    except ValueError:
                        raise ApiError(status, "invalid JSON")
                if status == 429 or 500 <= status < 600:
                    if status == 429 and str((headers or {}).get("Retry-After", "")).isdigit():
                        wait = int(headers["Retry-After"])
                else:
                    try:
                        message = json.loads(body)["error"]
                    except Exception:
                        message = body
                    raise (NotFound if status == 404 else ApiError)(status, message)
            if attempt < self.retries:
                self.sleep(wait)
        raise ApiError(status, "retries exhausted")

    def get_user(self, user_id):
        if type(user_id) is not int or user_id <= 0:
            raise ValueError(user_id)
        return self._get(f"/users/{user_id}")

    def list_users(self):
        items, cursor, seen = [], None, set()
        while True:
            page = self._get("/users", {"cursor": cursor} if cursor is not None else {})
            items += page["items"]
            cursor = page.get("next")
            if cursor is None:
                return items
            if cursor in seen:
                raise ApiError(None, "repeated cursor")
            seen.add(cursor)
'''},
)

# 5. A performance fix with a hard time limit -------------------------------------
TASKS['perf-sessions'] = dict(
    area='performance fix with a hard time limit',
    instruction="""`sessionize` in sessions.py is correct but far too slow for a day of events (the large test times out). Make it fast enough that `python3 -m unittest` passes, without changing its results or its contract (read the docstring). The graders run larger inputs with a hard time limit.""",
    files={
        'sessions.py': '''"""Group user events into sessions."""


def sessionize(events, gap):
    """Group (user, timestamp) events into sessions.

    events is a list of (user: str, timestamp: int) in any order; the same event may
    appear more than once. A session is a maximal run of one user's timestamps (sorted)
    where consecutive timestamps differ by at most `gap`. Returns a list of
    (user, start, end, count) sorted by (start, user); count includes duplicates.
    The events list is not changed.
    """
    users = []
    for user, _ in events:
        if user not in users:
            users.append(user)
    out = []
    for user in users:
        times = sorted(t for u, t in events if u == user)
        start = prev = times[0]
        count = 1
        for t in times[1:]:
            if t - prev > gap:
                out.append((user, start, prev, count))
                start, count = t, 0
            prev = t
            count += 1
        out.append((user, start, prev, count))
    for i in range(len(out)):
        for j in range(len(out) - 1 - i):
            if (out[j][1], out[j][0]) > (out[j + 1][1], out[j + 1][0]):
                out[j], out[j + 1] = out[j + 1], out[j]
    return out
''',
        'test_sessions.py': '''import random, time, unittest
from sessions import sessionize


class SessionsTest(unittest.TestCase):
    def test_small(self):
        events = [("b", 5), ("a", 1), ("a", 3), ("a", 10), ("b", 6)]
        self.assertEqual(sessionize(events, 2), [("a", 1, 3, 2), ("b", 5, 6, 2), ("a", 10, 10, 1)])

    def test_large_is_fast(self):
        random.seed(1)
        events = [(f"u{random.randrange(5000)}", random.randrange(86400)) for _ in range(50000)]
        start = time.time()
        sessionize(events, 300)
        self.assertLess(time.time() - start, 2.0)


if __name__ == "__main__":
    unittest.main()
''',
    },
    hidden='''import random, time, unittest
from sessions import sessionize


def slow(events, gap):
    out = []
    for user in sorted({u for u, _ in events}):
        times = sorted(t for u, t in events if u == user)
        run = [times[0]]
        for t in times[1:]:
            if t - run[-1] > gap:
                out.append((user, run[0], run[-1], len(run))); run = []
            run.append(t)
        out.append((user, run[0], run[-1], len(run)))
    return sorted(out, key=lambda s: (s[1], s[0]))


class Hidden(unittest.TestCase):
    def test_matches_contract(self):
        random.seed(5)
        for _ in range(300):
            events = [(random.choice("abcd"), random.randrange(30)) for _ in range(random.randint(0, 25))]
            copy = list(events)
            for gap in (0, 1, 3):
                self.assertEqual(sessionize(events, gap), slow(events, gap), (events, gap))
            self.assertEqual(events, copy)

    def test_large_hard_limit(self):
        random.seed(6)
        events = [(f"user{random.randrange(80000)}", random.randrange(10 ** 6)) for _ in range(400000)]
        start = time.time()
        got = sessionize(events, 600)
        elapsed = time.time() - start
        self.assertLess(elapsed, 2.5)
        self.assertEqual(sum(s[3] for s in got), len(events))
''',
    fix={'sessions.py': lambda s: s[:s.index('    users = []')] + '''    by_user = {}
    for user, t in events:
        by_user.setdefault(user, []).append(t)
    out = []
    for user, times in by_user.items():
        times.sort()
        start = prev = times[0]
        count = 1
        for t in times[1:]:
            if t - prev > gap:
                out.append((user, start, prev, count))
                start, count = t, 0
            prev = t
            count += 1
        out.append((user, start, prev, count))
    out.sort(key=lambda s: (s[1], s[0]))
    return out
'''},
)


def validate():
    """As agentic-v1's validate(), but a reference fix may also create new files."""
    import tempfile
    bad = 0
    for name, task in TASKS.items():
        with tempfile.TemporaryDirectory() as start, tempfile.TemporaryDirectory() as fixed:
            write_files(task, start)
            passed, total, _ = run_hidden(task, start)
            write_files(task, fixed)
            for rel, change in task['fix'].items():
                p = Path(fixed, rel)
                p.write_text(change(p.read_text() if p.exists() else ''))
            fpassed, ftotal, out = run_hidden(task, fixed)
            vis_ok, vis_out = run_visible(fixed)
            ok = ftotal and fpassed == ftotal and passed < (total or 1) and vis_ok
            print(f"{'ok  ' if ok else 'FAIL'} {name}: start {passed}/{total}, reference fix {fpassed}/{ftotal}, visible tests with fix {'pass' if vis_ok else 'FAIL'}")
            if not ok:
                bad += 1
                print(out[-1500:], vis_out[-800:])
    return bad


if __name__ == '__main__':
    sys.exit(1 if validate() else 0)
