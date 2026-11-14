"""agentic-v1: small repositories an agent must change with its own tools (pi).

Each task has the files the agent starts with, the instruction it gets, hidden tests
that decide the grade, and a reference fix. `validate()` checks that the hidden tests
fail on the starting files and pass with the reference fix.

    .venv/bin/python benches/agentic-v1/tasks.py     # validate every task
"""
import shutil, subprocess, sys, tempfile
from pathlib import Path

TASKS = {}

# 1. Bug fixing guided by failing tests -----------------------------------------
TASKS['bugfix-inventory'] = dict(
    area='bug fixing with visible failing tests',
    instruction="The tests in this folder fail. Find and fix the bugs in inventory.py so that `python3 -m unittest` passes. Do not change the tests.",
    files={
        'inventory.py': '''"""A tiny stock-keeping module."""


class OutOfStock(Exception):
    pass


class Inventory:
    def __init__(self):
        self.items = {}  # sku -> [quantity, unit_price_cents]

    def add(self, sku, quantity, unit_price_cents):
        if sku in self.items:
            self.items[sku][0] += quantity
        else:
            self.items[sku] = [quantity, unit_price_cents]

    def remove(self, sku, quantity):
        have = self.items.get(sku, [0, 0])[0]
        if quantity >= have:
            raise OutOfStock(sku)
        self.items[sku][0] -= quantity

    def value_cents(self):
        return sum(q * p for q, p in self.items.values())

    def price_with_discount(self, sku, percent):
        """Unit price after a percentage discount, rounded to the nearest cent (half up)."""
        price = self.items[sku][1]
        return int(price * (100 - percent) / 100)

    def low_stock(self, threshold):
        """SKUs with quantity at or below threshold, sorted."""
        return sorted(s for s, (q, _) in self.items.items() if q < threshold)
''',
        'test_inventory.py': '''import unittest
from inventory import Inventory, OutOfStock


class InventoryTest(unittest.TestCase):
    def setUp(self):
        self.inv = Inventory()
        self.inv.add("apple", 10, 50)
        self.inv.add("pear", 2, 199)

    def test_remove_all_stock(self):
        self.inv.remove("apple", 10)
        self.assertEqual(self.inv.items["apple"][0], 0)

    def test_remove_too_much(self):
        with self.assertRaises(OutOfStock):
            self.inv.remove("pear", 3)

    def test_discount_rounds_half_up(self):
        self.assertEqual(self.inv.price_with_discount("pear", 15), 169)  # 169.15

    def test_low_stock_includes_threshold(self):
        self.assertEqual(self.inv.low_stock(2), ["pear"])


if __name__ == "__main__":
    unittest.main()
''',
    },
    hidden='''import unittest
from inventory import Inventory, OutOfStock


class Hidden(unittest.TestCase):
    def test_remove_exact_and_too_much(self):
        inv = Inventory(); inv.add("a", 3, 100)
        inv.remove("a", 3)
        self.assertEqual(inv.items["a"][0], 0)
        with self.assertRaises(OutOfStock):
            inv.remove("a", 1)
        with self.assertRaises(OutOfStock):
            inv.remove("missing", 1)

    def test_discount_rounding(self):
        inv = Inventory(); inv.add("x", 1, 199); inv.add("y", 1, 250); inv.add("z", 1, 999)
        self.assertEqual(inv.price_with_discount("x", 15), 169)   # 169.15
        self.assertEqual(inv.price_with_discount("y", 10), 225)   # 225.0
        self.assertEqual(inv.price_with_discount("z", 50), 500)   # 499.5 rounds up
        self.assertEqual(inv.price_with_discount("x", 0), 199)

    def test_low_stock(self):
        inv = Inventory(); inv.add("b", 2, 1); inv.add("a", 1, 1); inv.add("c", 3, 1)
        self.assertEqual(inv.low_stock(2), ["a", "b"])

    def test_value(self):
        inv = Inventory(); inv.add("a", 2, 50); inv.add("a", 1, 50)
        self.assertEqual(inv.value_cents(), 150)
''',
    fix={'inventory.py': lambda s: s.replace('if quantity >= have:', 'if quantity > have:')
         .replace('return int(price * (100 - percent) / 100)', 'return (price * (100 - percent) + 50) // 100')
         .replace('if q < threshold)', 'if q <= threshold)')},
)

# 2. A feature across a small module, from a written spec ------------------------
TASKS['feature-todo'] = dict(
    area='new feature from a spec',
    instruction="""Extend todo.py (keep the existing behaviour and names):

1. `TodoList.complete(task_id)` marks a task done. Unknown ids raise `KeyError`.
2. `TodoList.remove(task_id)` deletes a task. Unknown ids raise `KeyError`. Ids are never reused, even after removal.
3. `TodoList.pending()` returns the titles of tasks not done, oldest first.
4. `TodoList.to_json()` returns a JSON string: a list of objects with keys "id", "title" and "done", ordered by id.
5. `TodoList.from_json(text)` is a classmethod that rebuilds a list from that JSON; the next new id continues after the highest id.

Add tests for the new behaviour to test_todo.py, and make sure `python3 -m unittest` passes.""",
    files={
        'todo.py': '''"""A minimal to-do list."""


class TodoList:
    def __init__(self):
        self._tasks = {}   # id -> {"title": str, "done": bool}
        self._next_id = 1

    def add(self, title):
        title = title.strip()
        if not title:
            raise ValueError("title is empty")
        task_id = self._next_id
        self._next_id += 1
        self._tasks[task_id] = {"title": title, "done": False}
        return task_id

    def titles(self):
        return [t["title"] for _, t in sorted(self._tasks.items())]
''',
        'test_todo.py': '''import unittest
from todo import TodoList


class TodoTest(unittest.TestCase):
    def test_add(self):
        todo = TodoList()
        self.assertEqual(todo.add(" milk "), 1)
        self.assertEqual(todo.add("eggs"), 2)
        self.assertEqual(todo.titles(), ["milk", "eggs"])

    def test_empty_title(self):
        with self.assertRaises(ValueError):
            TodoList().add("   ")


if __name__ == "__main__":
    unittest.main()
''',
    },
    hidden='''import json, unittest
from todo import TodoList


class Hidden(unittest.TestCase):
    def test_complete_pending(self):
        t = TodoList(); a = t.add("a"); b = t.add("b"); c = t.add("c")
        t.complete(b)
        self.assertEqual(t.pending(), ["a", "c"])
        with self.assertRaises(KeyError):
            t.complete(99)

    def test_remove_never_reuses_ids(self):
        t = TodoList(); a = t.add("a"); b = t.add("b")
        t.remove(b)
        self.assertEqual(t.add("c"), 3)
        self.assertEqual(t.titles(), ["a", "c"])
        with self.assertRaises(KeyError):
            t.remove(b)

    def test_json_round_trip(self):
        t = TodoList(); t.add("a"); t.add("b"); t.add("c"); t.complete(1); t.remove(2)
        data = json.loads(t.to_json())
        self.assertEqual(data, [{"id": 1, "title": "a", "done": True}, {"id": 3, "title": "c", "done": False}])
        u = TodoList.from_json(t.to_json())
        self.assertEqual(u.pending(), ["c"])
        self.assertEqual(u.add("d"), 4)
        self.assertEqual(TodoList.from_json("[]").add("x"), 1)
''',
    fix={'todo.py': lambda s: s + '''
    def complete(self, task_id):
        self._tasks[task_id]["done"] = True

    def remove(self, task_id):
        del self._tasks[task_id]

    def pending(self):
        return [t["title"] for _, t in sorted(self._tasks.items()) if not t["done"]]

    def to_json(self):
        import json
        return json.dumps([{"id": i, "title": t["title"], "done": t["done"]} for i, t in sorted(self._tasks.items())])

    @classmethod
    def from_json(cls, text):
        import json
        new = cls()
        for row in json.loads(text):
            new._tasks[row["id"]] = {"title": row["title"], "done": row["done"]}
        new._next_id = max(new._tasks, default=0) + 1
        return new
'''},
)

# 3. A change that must reach every copy of duplicated logic ---------------------
TASKS['refactor-dates'] = dict(
    area='change spread across duplicated code',
    instruction="""report.py parses dates in three places. Add support for the date format YYYY/MM/DD (for example 2026/03/09) everywhere dates are accepted, alongside the existing YYYY-MM-DD. Invalid dates must still raise ValueError. Keep function names and behaviour otherwise unchanged, and make sure `python3 -m unittest` passes.""",
    files={
        'report.py': '''"""Sales report helpers."""
from datetime import date


def parse_sale(line):
    """'2026-03-09,12.50' -> (date, amount)."""
    day, amount = line.split(",")
    y, m, d = day.strip().split("-")
    return date(int(y), int(m), int(d)), float(amount)


def in_range(day_text, start_text, end_text):
    """True if day is between start and end, inclusive."""
    y, m, d = day_text.split("-")
    day = date(int(y), int(m), int(d))
    y, m, d = start_text.split("-")
    start = date(int(y), int(m), int(d))
    y, m, d = end_text.split("-")
    end = date(int(y), int(m), int(d))
    return start <= day <= end


def total_between(lines, start_text, end_text):
    total = 0.0
    for line in lines:
        day_text = line.split(",")[0].strip()
        if in_range(day_text, start_text, end_text):
            total += parse_sale(line)[1]
    return round(total, 2)
''',
        'test_report.py': '''import unittest
from datetime import date
from report import parse_sale, in_range, total_between


class ReportTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_sale("2026-03-09,12.50"), (date(2026, 3, 9), 12.5))

    def test_total(self):
        lines = ["2026-01-01,10", "2026-01-15,5.25", "2026-02-01,100"]
        self.assertEqual(total_between(lines, "2026-01-01", "2026-01-31"), 15.25)


if __name__ == "__main__":
    unittest.main()
''',
    },
    hidden='''import unittest
from datetime import date
from report import parse_sale, in_range, total_between


class Hidden(unittest.TestCase):
    def test_slash_everywhere(self):
        self.assertEqual(parse_sale("2026/03/09,12.50"), (date(2026, 3, 9), 12.5))
        self.assertTrue(in_range("2026/01/15", "2026-01-01", "2026/01/31"))
        self.assertFalse(in_range("2026-02-01", "2026/01/01", "2026/01/31"))
        lines = ["2026/01/01,10", "2026-01-15,5.25", "2026/02/01,100"]
        self.assertEqual(total_between(lines, "2026/01/01", "2026-01-31"), 15.25)

    def test_dash_still_works(self):
        self.assertEqual(parse_sale(" 2026-12-31 ,1"), (date(2026, 12, 31), 1.0))

    def test_invalid(self):
        for bad in ["2026.03.09,1", "2026/13/01,1", "2026-03/09,1", "nonsense,1"]:
            with self.assertRaises(ValueError):
                parse_sale(bad)
        with self.assertRaises(ValueError):
            in_range("2026/02/30", "2026-01-01", "2026-12-31")
''',
    fix={'report.py': lambda s: s.replace('from datetime import date\n', '''from datetime import date
import re


def _day(text):
    m = re.fullmatch(r"(\\d{4})([-/])(\\d{2})\\2(\\d{2})", text.strip())
    if not m:
        raise ValueError(text)
    return date(int(m[1]), int(m[3]), int(m[4]))
''').replace('''    y, m, d = day.strip().split("-")
    return date(int(y), int(m), int(d)), float(amount)''', '''    return _day(day), float(amount)''').replace('''    y, m, d = day_text.split("-")
    day = date(int(y), int(m), int(d))
    y, m, d = start_text.split("-")
    start = date(int(y), int(m), int(d))
    y, m, d = end_text.split("-")
    end = date(int(y), int(m), int(d))
    return start <= day <= end''', '''    return _day(start_text) <= _day(day_text) <= _day(end_text)''')},
)

# 4. A performance bug with a behaviour contract --------------------------------
TASKS['perf-top-words'] = dict(
    area='performance fix that keeps behaviour',
    instruction="""`top_words` in words.py is far too slow on large inputs (the test with a large input times out). Make it fast enough that `python3 -m unittest` passes, without changing its results: the k most frequent words, ties broken by the word that appears first in the text.""",
    files={
        'words.py': '''"""Word frequency helpers."""


def top_words(text, k):
    """Return the k most frequent lowercase words in text.

    Words are runs of letters a-z (case-insensitive). Ties are broken by first
    appearance in the text. Returns a list of (word, count).
    """
    words = []
    current = ""
    for ch in text.lower() + " ":
        if "a" <= ch <= "z":
            current += ch
        elif current:
            words.append(current)
            current = ""
    order = []
    for w in words:
        if w not in order:
            order.append(w)
    counted = [(w, words.count(w)) for w in order]
    counted.sort(key=lambda pair: (-pair[1], order.index(pair[0])))
    return counted[:k]
''',
        'test_words.py': '''import time, unittest
from words import top_words


class WordsTest(unittest.TestCase):
    def test_small(self):
        self.assertEqual(top_words("b a b, A c!", 2), [("b", 2), ("a", 2)])

    def test_large_is_fast(self):
        text = " ".join(f"w{chr(97 + i % 26)}{chr(97 + (i // 26) % 26)}x" for i in range(200000))
        start = time.time()
        top_words(text, 5)
        self.assertLess(time.time() - start, 2.0)


if __name__ == "__main__":
    unittest.main()
''',
    },
    hidden='''import random, time, unittest
from words import top_words


def slow(text, k):
    words, cur = [], ""
    for ch in text.lower() + " ":
        if "a" <= ch <= "z": cur += ch
        elif cur: words.append(cur); cur = ""
    order = list(dict.fromkeys(words))
    counts = {w: words.count(w) for w in order}
    return sorted(((w, counts[w]) for w in order), key=lambda p: (-p[1], order.index(p[0])))[:k]


class Hidden(unittest.TestCase):
    def test_matches_contract(self):
        random.seed(3)
        for _ in range(50):
            text = " ".join(random.choice(["Ab", "ab!", "c-d", "e", "E9e", "ff", "gg.gg"]) for _ in range(random.randint(0, 40)))
            for k in (0, 1, 3, 10):
                self.assertEqual(top_words(text, k), slow(text, k), (text, k))

    def test_large(self):
        random.seed(4)
        text = " ".join("".join(random.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(5)) for _ in range(300000))
        start = time.time()
        top_words(text, 10)
        self.assertLess(time.time() - start, 3.0)
''',
    fix={'words.py': lambda s: s[:s.index('    words = []')] + '''    import re
    from collections import Counter
    words = re.findall(r"[a-z]+", text.lower())
    counts = Counter(words)
    first = {}
    for i, w in enumerate(words):
        first.setdefault(w, i)
    return sorted(counts.items(), key=lambda p: (-p[1], first[p[0]]))[:k]
'''},
)

# 5. Multi-file change with an error contract ------------------------------------
TASKS['bank-transfer'] = dict(
    area='multi-file feature with error handling',
    instruction="""Implement `transfer(bank, source_id, target_id, amount_cents)` in bank/ledger.py.

- It moves money between two accounts and records the transfer in `bank.history` as a tuple (source_id, target_id, amount_cents).
- `amount_cents` must be a positive int, else raise `InvalidAmount`.
- Unknown account ids raise `UnknownAccount`; transferring to the same account raises `InvalidAmount`.
- If the source balance is too low raise `InsufficientFunds`.
- When any error is raised, no balance and no history may change.
- The exceptions belong in bank/errors.py and must all subclass `BankError`.

Make sure `python3 -m unittest` passes.""",
    files={
        'bank/__init__.py': '',
        'bank/errors.py': '''class BankError(Exception):
    """Base class for bank errors."""
''',
        'bank/accounts.py': '''class Account:
    def __init__(self, account_id, balance_cents=0):
        self.id = account_id
        self.balance_cents = balance_cents


class Bank:
    def __init__(self):
        self.accounts = {}
        self.history = []

    def open(self, account_id, balance_cents=0):
        self.accounts[account_id] = Account(account_id, balance_cents)
        return self.accounts[account_id]
''',
        'bank/ledger.py': '''from .accounts import Bank


def transfer(bank: Bank, source_id, target_id, amount_cents):
    raise NotImplementedError
''',
        'test_bank.py': '''import unittest
from bank.accounts import Bank
from bank.ledger import transfer


class BankTest(unittest.TestCase):
    def test_simple_transfer(self):
        bank = Bank(); bank.open("a", 500); bank.open("b")
        transfer(bank, "a", "b", 200)
        self.assertEqual(bank.accounts["a"].balance_cents, 300)
        self.assertEqual(bank.accounts["b"].balance_cents, 200)


if __name__ == "__main__":
    unittest.main()
''',
    },
    hidden='''import unittest
from bank.accounts import Bank
from bank.ledger import transfer
from bank import errors


class Hidden(unittest.TestCase):
    def setUp(self):
        self.bank = Bank(); self.bank.open("a", 500); self.bank.open("b", 0)

    def state(self):
        return ({k: v.balance_cents for k, v in self.bank.accounts.items()}, list(self.bank.history))

    def test_ok_and_history(self):
        transfer(self.bank, "a", "b", 500)
        self.assertEqual(self.state(), ({"a": 0, "b": 500}, [("a", "b", 500)]))

    def test_errors_change_nothing(self):
        before = self.state()
        cases = [(("a", "b", 0), "InvalidAmount"), (("a", "b", -5), "InvalidAmount"), (("a", "b", 1.5), "InvalidAmount"),
                 (("a", "b", "10"), "InvalidAmount"), (("a", "a", 10), "InvalidAmount"), (("a", "zz", 10), "UnknownAccount"),
                 (("zz", "a", 10), "UnknownAccount"), (("a", "b", 501), "InsufficientFunds"), (("a", "b", True), "InvalidAmount")]
        for args, name in cases:
            exc = getattr(errors, name)
            self.assertTrue(issubclass(exc, errors.BankError), name)
            with self.assertRaises(exc, msg=args):
                transfer(self.bank, *args)
            self.assertEqual(self.state(), before, args)
''',
    fix={'bank/errors.py': lambda s: s + '''

class InvalidAmount(BankError):
    pass


class UnknownAccount(BankError):
    pass


class InsufficientFunds(BankError):
    pass
''', 'bank/ledger.py': lambda s: '''from .accounts import Bank
from .errors import InvalidAmount, UnknownAccount, InsufficientFunds


def transfer(bank: Bank, source_id, target_id, amount_cents):
    if type(amount_cents) is not int or amount_cents <= 0 or source_id == target_id:
        raise InvalidAmount(amount_cents)
    if source_id not in bank.accounts or target_id not in bank.accounts:
        raise UnknownAccount(source_id if source_id not in bank.accounts else target_id)
    src, dst = bank.accounts[source_id], bank.accounts[target_id]
    if src.balance_cents < amount_cents:
        raise InsufficientFunds(source_id)
    src.balance_cents -= amount_cents
    dst.balance_cents += amount_cents
    bank.history.append((source_id, target_id, amount_cents))
'''},
)


def write_files(task, dest):
    for rel, text in task['files'].items():
        p = Path(dest, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def run_hidden(task, workspace, timeout=60):
    """Copy the workspace, add the hidden tests, run them. Returns (passed, total, output)."""
    with tempfile.TemporaryDirectory() as d:
        shutil.copytree(workspace, d, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '.pi*'))
        Path(d, 'test_hidden_grade.py').write_text(task['hidden'])
        try:
            r = subprocess.run([sys.executable, '-I', '-m', 'unittest', 'discover', '-s', '.', '-p', 'test_hidden_grade.py', '-v'], cwd=d,
                               capture_output=True, text=True, timeout=timeout)
            out = r.stdout + r.stderr
        except subprocess.TimeoutExpired as e:
            return 0, None, f'timeout after {timeout}s'
        import re
        total = int(m[1]) if (m := re.search(r'Ran (\d+) test', out)) else 0
        failed = sum(int(x) for x in re.findall(r'(?:failures|errors)=(\d+)', out))
        return (total - failed if r.returncode == 0 or total else 0), total, out


def run_visible(workspace, timeout=60):
    try:
        r = subprocess.run([sys.executable, '-I', '-m', 'unittest'], cwd=workspace, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return False, 'timeout'


def validate():
    bad = 0
    for name, task in TASKS.items():
        with tempfile.TemporaryDirectory() as start, tempfile.TemporaryDirectory() as fixed:
            write_files(task, start)
            passed, total, _ = run_hidden(task, start)
            write_files(task, fixed)
            for rel, change in task['fix'].items():
                p = Path(fixed, rel)
                p.write_text(change(p.read_text()))
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
