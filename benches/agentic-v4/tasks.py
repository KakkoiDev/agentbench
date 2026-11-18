"""agentic-v4: hard coding tasks, same format and grading as agentic-v1..v3.

Written on 2026-11-18 because the best local models solve most of v1..v3, and two near-perfect
scores cannot say which model codes better. These tasks are meant to be beyond every model we
have: long written specs with many edge cases, a change across several files, and a speed limit.
Each task has many hidden tests with descriptive names, so the score is partial credit (tests
passed) and the test names say what a model got right and what it missed.

Tasks: an expression evaluator with exact error positions; an iCalendar parser with recurrence,
time zones and DST; multi-currency money across a four-file package; JSON Patch (RFC 6902) with
atomic failure; and overlap counting fast enough for 200,000 intervals.

    .venv/bin/python benches/agentic-v4/tasks.py     # validate every task
"""
import importlib.util
import sys
from pathlib import Path

_v1 = importlib.util.spec_from_file_location('agentic_v1_tasks', Path(__file__).resolve().parents[1] / 'agentic-v1' / 'tasks.py')
V1 = importlib.util.module_from_spec(_v1)
_v1.loader.exec_module(V1)
write_files, run_hidden, run_visible = V1.write_files, V1.run_hidden, V1.run_visible

TASKS = {}

# 1. Expression evaluator ----------------------------------------------------------------
CALC_REF = r'''"""A small calculator: evaluate(text, variables=None)."""
import math


class CalcError(Exception):
    def __init__(self, message, position):
        super().__init__(f"{message} at {position}")
        self.message = message
        self.position = position


FUNCS = {'abs': (1, 1), 'min': (1, None), 'max': (1, None), 'round': (1, 2)}


def tokenize(text):
    toks, i = [], 0
    while i < len(text):
        c = text[i]
        if c.isspace():
            i += 1
        elif c.isdigit() or (c == '.' and i + 1 < len(text) and text[i + 1].isdigit()):
            j = i
            while j < len(text) and (text[j].isdigit() or text[j] == '.'):
                j += 1
            s = text[i:j]
            if s.count('.') > 1:
                raise CalcError('bad number', i)
            toks.append(('num', float(s) if '.' in s else int(s), i))
            i = j
        elif c.isalpha() or c == '_':
            j = i
            while j < len(text) and (text[j].isalnum() or text[j] == '_'):
                j += 1
            toks.append(('name', text[i:j], i))
            i = j
        elif text.startswith('**', i) or text.startswith('//', i):
            toks.append(('op', text[i:i + 2], i))
            i += 2
        elif c in '+-*/%(),':
            toks.append(('op', c, i))
            i += 1
        else:
            raise CalcError(f'unexpected character {c!r}', i)
    toks.append(('end', None, len(text)))
    return toks


class Parser:
    def __init__(self, text, variables):
        self.toks, self.i, self.vars = tokenize(text), 0, variables or {}

    def peek(self):
        return self.toks[self.i]

    def take(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, value):
        t = self.take()
        if t[1] != value or t[0] == 'num':
            if value == ')' and t[0] == 'end':
                raise CalcError('unclosed parenthesis', t[2])
            raise CalcError(f'expected {value!r}', t[2])

    def parse(self):
        v = self.sum()
        t = self.peek()
        if t[0] != 'end':
            raise CalcError('unexpected token', t[2])
        return v

    def sum(self):
        v = self.product()
        while self.peek()[0] == 'op' and self.peek()[1] in '+-':
            op = self.take()[1]
            r = self.product()
            v = v + r if op == '+' else v - r
        return v

    def product(self):
        v = self.unary()
        while self.peek()[0] == 'op' and self.peek()[1] in ('*', '/', '//', '%'):
            _, op, pos = self.take()
            r = self.unary()
            if op != '*' and r == 0:
                raise CalcError('division by zero', pos)
            v = v * r if op == '*' else v / r if op == '/' else v // r if op == '//' else v % r
        return v

    def unary(self):
        t = self.peek()
        if t[0] == 'op' and t[1] in '+-':
            self.take()
            v = self.unary()
            return -v if t[1] == '-' else v
        return self.power()

    def power(self):
        base = self.atom()
        if self.peek()[0] == 'op' and self.peek()[1] == '**':
            self.take()
            exp = self.unary()  # right-associative, and 2**-1 works
            return base ** exp
        return base

    def atom(self):
        t = self.take()
        kind, val, pos = t
        if kind == 'num':
            return val
        if kind == 'op' and val == '(':
            v = self.sum()
            self.expect(')')
            return v
        if kind == 'name':
            if self.peek()[1] == '(' and self.peek()[0] == 'op':
                if val not in FUNCS:
                    raise CalcError(f'unknown function {val!r}', pos)
                self.take()
                args = []
                if self.peek()[1] != ')':
                    args.append(self.sum())
                    while self.peek()[0] == 'op' and self.peek()[1] == ',':
                        self.take()
                        args.append(self.sum())
                self.expect(')')
                lo, hi = FUNCS[val]
                if len(args) < lo or (hi is not None and len(args) > hi):
                    raise CalcError(f'wrong number of arguments for {val}', pos)
                if val == 'round':
                    return round(*[args[0], int(args[1])] if len(args) == 2 else args)
                return {'abs': abs, 'min': min, 'max': max}[val](*args)
            if val not in self.vars:
                raise CalcError(f'unknown name {val!r}', pos)
            return self.vars[val]
        if kind == 'end':
            raise CalcError('unexpected end', pos)
        raise CalcError('unexpected token', pos)


def evaluate(text, variables=None):
    return Parser(text, variables).parse()
'''

TASKS['calc-evaluator'] = dict(
    area='expression evaluator from a precise spec, with exact error positions',
    instruction="""calc.py has a half-working calculator. Make `evaluate(text, variables=None)` follow this spec exactly:

- Numbers: integers (`12`) and decimals (`2.5`, `.5`); a number with two dots is an error. Names (letters, digits, `_`, not starting with a digit) are looked up in `variables`.
- Operators: `+ - * / // % **`, parentheses, unary `+` and `-`.
- Precedence, highest first: `**` (right-associative: `2**3**2 == 512`); unary `+`/`-` (so `-2**2 == -4` and `2**-1 == 0.5`); `* / // %` (left to right); `+ -` (left to right: `10-4-3 == 3`).
- Functions: `abs(x)`, `min(a, ...)` and `max(a, ...)` with at least one argument, `round(x)` or `round(x, n)`.
- Results keep Python's types: `7//2 == 3` is an int, `7/2 == 3.5`.
- Errors raise `CalcError(message, position)` (it exists) where `position` is the 0-based index in `text` of the offending character or token:
  an unexpected character (e.g. `$`), an unexpected token (e.g. the second `*` in `2 * * 3`), an unknown name or function (where its name starts), a wrong number of function arguments (where the function name starts),
  division or modulo by zero, including `//` (where the operator is), an unclosed parenthesis or input that ends too early (position = `len(text)`), and leftover input such as `2 3` (where the leftover starts).
- Spaces are allowed anywhere between tokens.

Do not use `eval`, `exec`, `ast` or `compile`. Make sure `python3 -m unittest` passes.""",
    files={
        'calc.py': r'''"""A small calculator: evaluate(text, variables=None)."""
import re


class CalcError(Exception):
    def __init__(self, message, position):
        super().__init__(f"{message} at {position}")
        self.message = message
        self.position = position


TOKEN = re.compile(r"\s*(\d+\.?\d*|[A-Za-z_]\w*|\*\*|[-+*/()])")


def tokenize(text):
    pos, out = 0, []
    while pos < len(text.rstrip()):
        m = TOKEN.match(text, pos)
        if not m:
            raise CalcError("bad input", pos)
        out.append(m.group(1))
        pos = m.end()
    return out


def evaluate(text, variables=None):
    toks = tokenize(text)
    variables = variables or {}

    def atom(i):
        t = toks[i]
        if t == '(':
            v, i = expr(i + 1)
            return v, i + 1
        if t == '-':
            v, i = atom(i + 1)
            return -v, i
        if t[0].isdigit():
            return (float(t) if '.' in t else int(t)), i + 1
        return variables[t], i + 1

    def power(i):
        v, i = atom(i)
        while i < len(toks) and toks[i] == '**':
            r, i = atom(i + 1)
            v = v ** r
        return v, i

    def term(i):
        v, i = power(i)
        while i < len(toks) and toks[i] in '*/':
            op = toks[i]
            r, i = power(i + 1)
            v = v * r if op == '*' else v / r
        return v, i

    def expr(i):
        v, i = term(i)
        while i < len(toks) and toks[i] in '+-':
            op = toks[i]
            r, i = expr(i + 1)
            v = v + r if op == '+' else v - r
        return v, i

    v, _ = expr(0)
    return v
''',
        'test_calc.py': r'''import unittest
from calc import evaluate


class CalcTest(unittest.TestCase):
    def test_basics(self):
        self.assertEqual(evaluate('1 + 2 * 3'), 7)
        self.assertEqual(evaluate('(1 + 2) * 3'), 9)
        self.assertEqual(evaluate('x * 2', {'x': 4}), 8)


if __name__ == '__main__':
    unittest.main()
''',
    },
    hidden=r'''import pathlib, unittest
from calc import evaluate, CalcError


class Hidden(unittest.TestCase):
    def err(self, text, pos, variables=None):
        with self.assertRaises(CalcError) as c:
            evaluate(text, variables)
        self.assertEqual(c.exception.position, pos, c.exception)

    def test_left_associative_minus_and_division(self):
        self.assertEqual(evaluate('10-4-3'), 3)
        self.assertEqual(evaluate('100/10/5'), 2.0)
        self.assertEqual(evaluate('2-3+4'), 3)

    def test_power_right_associative(self):
        self.assertEqual(evaluate('2**3**2'), 512)

    def test_unary_minus_below_power(self):
        self.assertEqual(evaluate('-2**2'), -4)
        self.assertEqual(evaluate('2**-1'), 0.5)
        self.assertEqual(evaluate('--3'), 3)
        self.assertEqual(evaluate('+4'), 4)

    def test_floor_division_modulo_and_types(self):
        self.assertEqual(evaluate('7//2'), 3)
        self.assertIsInstance(evaluate('7//2'), int)
        self.assertEqual(evaluate('7/2'), 3.5)
        self.assertEqual(evaluate('7 % 3 * 2'), 2)

    def test_decimal_numbers(self):
        self.assertAlmostEqual(evaluate('.5 + 2.25'), 2.75)

    def test_functions(self):
        self.assertEqual(evaluate('abs(-3) + max(1, 7, 2) - min(4, 9)'), 6)
        self.assertEqual(evaluate('round(2.567, 2)'), 2.57)
        self.assertEqual(evaluate('round(2.5) + round(3.5)'), 6)

    def test_error_unexpected_character(self):
        self.err('1 + $', 4)

    def test_error_unexpected_token(self):
        self.err('2 * * 3', 4)
        self.err('2 3', 2)

    def test_error_unknown_name_and_function(self):
        self.err('1 + foo', 4)
        self.err('sqrt(4)', 0)

    def test_error_argument_count(self):
        self.err('1 + abs(1, 2)', 4)
        self.err('max()', 0)

    def test_error_division_by_zero_at_operator(self):
        self.err('1 / 0', 2)
        self.err('5 // (2-2)', 2)
        self.err('5 % 0', 2)

    def test_error_unclosed_and_early_end(self):
        self.err('(1 + 2', 6)
        self.err('3 *', 3)
        self.err('1..2', 0)

    def test_no_eval(self):
        src = pathlib.Path('calc.py').read_text()
        for word in ('eval(', 'exec(', 'import ast', 'compile('):
            self.assertNotIn(word, src.replace('def evaluate(', '').replace('evaluate(', ''))
''',
    fix={'calc.py': lambda _: CALC_REF},
)

# 2. iCalendar with recurrence, time zones and DST ------------------------------------------
ICS_REF = r'''"""Read events from iCalendar text and list their occurrences."""
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


@dataclass
class Event:
    summary: str
    start: object            # aware datetime, or date for all-day events
    end: object
    rrule: dict = field(default_factory=dict)
    exdates: list = field(default_factory=list)


def unfold(text):
    return re.sub(r'\r?\n[ \t]', '', text).splitlines()


def unescape(v):
    out, i = [], 0
    while i < len(v):
        if v[i] == '\\' and i + 1 < len(v):
            out.append({'n': '\n', 'N': '\n'}.get(v[i + 1], v[i + 1]))
            i += 2
        else:
            out.append(v[i])
            i += 1
    return ''.join(out)


def parse_line(line):
    name_params, _, value = line.partition(':')
    parts = name_params.split(';')
    params = dict(p.split('=', 1) for p in parts[1:] if '=' in p)
    return parts[0].upper(), params, value


def parse_time(value, params):
    if params.get('VALUE') == 'DATE' or re.fullmatch(r'\d{8}', value):
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    dt = datetime.strptime(value.rstrip('Z'), '%Y%m%dT%H%M%S')
    if value.endswith('Z'):
        return dt.replace(tzinfo=timezone.utc)
    return dt.replace(tzinfo=ZoneInfo(params['TZID'])) if 'TZID' in params else dt.replace(tzinfo=timezone.utc)


def parse_duration(v):
    m = re.fullmatch(r'P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?', v)
    w, d, h, mi, s = (int(x or 0) for x in m.groups())
    return timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def parse_ics(text):
    events, cur = [], None
    for line in unfold(text):
        if not line.strip():
            continue
        name, params, value = parse_line(line)
        if name == 'BEGIN' and value.upper() == 'VEVENT':
            cur = dict(summary='', exdates=[], rrule={})
        elif name == 'END' and value.upper() == 'VEVENT':
            start = cur['start']
            end = cur.get('end') or (start + cur['duration'] if 'duration' in cur else
                                     start + timedelta(days=1) if not isinstance(start, datetime) else start)
            events.append(Event(cur['summary'], start, end, cur['rrule'], cur['exdates']))
            cur = None
        elif cur is None:
            continue
        elif name == 'SUMMARY':
            cur['summary'] = unescape(value)
        elif name == 'DTSTART':
            cur['start'] = parse_time(value, params)
        elif name == 'DTEND':
            cur['end'] = parse_time(value, params)
        elif name == 'DURATION':
            cur['duration'] = parse_duration(value)
        elif name == 'RRULE':
            cur['rrule'] = dict(p.split('=', 1) for p in value.split(';'))
        elif name == 'EXDATE':
            cur['exdates'] += [parse_time(v, params) for v in value.split(',')]
    return events


DAYS = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU']


def _key(x):
    return datetime(x.year, x.month, x.day, tzinfo=timezone.utc) if not isinstance(x, datetime) else x.astimezone(timezone.utc)


def _starts(ev):
    r = ev.rrule
    if not r:
        yield ev.start
        return
    freq, step = r['FREQ'], int(r.get('INTERVAL', 1))
    count = int(r['COUNT']) if 'COUNT' in r else None
    until = parse_time(r['UNTIL'], {}) if 'UNTIL' in r else None
    days = [DAYS.index(d) for d in r['BYDAY'].split(',')] if 'BYDAY' in r and freq == 'WEEKLY' else [ev.start.weekday()]
    local = ev.start
    made, n = 0, 0
    week0 = local - timedelta(days=local.weekday())
    while n < 5000:
        if freq == 'DAILY':
            cands = [local + timedelta(days=n * step)]
        else:
            ws = week0 + timedelta(weeks=n * step)
            cands = sorted(ws + timedelta(days=d) for d in days)
        for c in cands:
            if c < ev.start:
                continue
            if isinstance(c, datetime) and c.tzinfo is not None and not isinstance(c.tzinfo, type(timezone.utc)):
                c = c.replace(tzinfo=ev.start.tzinfo)  # same wall clock time in its zone, across DST
            if until is not None and _key(c) > _key(until):
                return
            if count is not None and made >= count:
                return
            made += 1
            yield c
        n += 1


def occurrences(events, start, end):
    """(start, end, summary) of every occurrence starting in [start, end), sorted by start (UTC);
    datetimes are returned in UTC, all-day occurrences as dates."""
    out = []
    lo, hi = _key(start), _key(end)
    for ev in events:
        length = ev.end - ev.start
        ex = {_key(x) for x in ev.exdates}
        for s in _starts(ev):
            k = _key(s)
            if k >= hi:
                break
            if k < lo or k in ex:
                continue
            if isinstance(s, datetime):
                out.append((s.astimezone(timezone.utc), (s + length).astimezone(timezone.utc), ev.summary))
            else:
                out.append((s, s + length, ev.summary))
    return sorted(out, key=lambda o: (_key(o[0]), o[2]))
'''

TASKS['ics-recurrence'] = dict(
    area='iCalendar parsing with recurrence rules, time zones and DST',
    instruction="""events.py reads iCalendar (.ics) text but handles only the simplest files. Make `parse_ics(text)` and `occurrences(events, start, end)` follow this spec:

Parsing (`parse_ics` returns a list of `Event`, one per VEVENT, in file order):
- Lines may end with CRLF or LF. A line starting with a space or a tab continues the previous line (remove the line break and that one character).
- Property names and parameters: `NAME;PARAM=VALUE;...:value`. SUMMARY values are unescaped: `\\,` `\\;` `\\\\` and `\\n` (newline).
- DTSTART / DTEND / EXDATE values: `20261201T090000Z` is UTC; `DTSTART;TZID=Europe/Paris:20261201T090000` is that zone's local time (use `zoneinfo`); a value with no Z and no TZID is UTC; `;VALUE=DATE:20261201` (or any 8-digit value) is an all-day `date`.
- The end comes from DTEND, else DURATION (e.g. `PT1H30M`, `P1D`, `P1W`), else one day for all-day events and zero length otherwise.
- RRULE: FREQ=DAILY or WEEKLY, with optional INTERVAL, COUNT, UNTIL (a UTC or date value) and, for WEEKLY, BYDAY (e.g. `MO,WE`). EXDATE may hold several comma-separated values and may appear on several lines.

Occurrences (`occurrences(events, start, end)`):
- Every occurrence whose start is in `[start, end)` as `(start, end, summary)` tuples, sorted by start then summary. `start`/`end` arguments are aware datetimes or dates (a date means midnight UTC).
- Datetimes in the result are converted to UTC; all-day occurrences stay `date`s and sort as midnight UTC.
- A recurring event keeps its local wall-clock time in its TZID across daylight-saving changes (a weekly 09:00 Europe/Paris meeting is 08:00 UTC in October before the change and 08:00 → 09:00... i.e. 07:00 UTC in summer, 08:00 UTC in winter).
- COUNT counts occurrences from DTSTART on, including ones removed by EXDATE. UNTIL is inclusive. EXDATE removes the occurrence with that exact start.
- Weekly with BYDAY: occurrences on those weekdays in every INTERVAL-th week, counting weeks from DTSTART's week (weeks start on Monday); never before DTSTART.

Make sure `python3 -m unittest` passes.""",
    files={
        'events.py': r'''"""Read events from iCalendar text and list their occurrences."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass
class Event:
    summary: str
    start: object
    end: object
    rrule: dict = field(default_factory=dict)
    exdates: list = field(default_factory=list)


def parse_ics(text):
    events, cur = [], None
    for line in text.splitlines():
        if line == 'BEGIN:VEVENT':
            cur = {}
        elif line == 'END:VEVENT':
            events.append(Event(cur.get('SUMMARY', ''), cur['start'], cur.get('end', cur['start'])))
            cur = None
        elif cur is not None and ':' in line:
            name, value = line.split(':', 1)
            if name == 'SUMMARY':
                cur['SUMMARY'] = value
            elif name == 'DTSTART':
                cur['start'] = datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
            elif name == 'DTEND':
                cur['end'] = datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
    return events


def occurrences(events, start, end):
    out = [(e.start, e.end, e.summary) for e in events if start <= e.start < end]
    return sorted(out)
''',
        'test_events.py': r'''import unittest
from datetime import datetime, timezone
from events import parse_ics, occurrences

ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Standup
DTSTART:20261201T090000Z
DTEND:20261201T091500Z
END:VEVENT
END:VCALENDAR
"""


class EventsTest(unittest.TestCase):
    def test_single(self):
        ev = parse_ics(ICS)
        utc = timezone.utc
        got = occurrences(ev, datetime(2026, 12, 1, tzinfo=utc), datetime(2026, 12, 2, tzinfo=utc))
        self.assertEqual(got, [(datetime(2026, 12, 1, 9, tzinfo=utc), datetime(2026, 12, 1, 9, 15, tzinfo=utc), 'Standup')])


if __name__ == '__main__':
    unittest.main()
''',
    },
    hidden=r'''import unittest
from datetime import date, datetime, timedelta, timezone
from events import parse_ics, occurrences

UTC = timezone.utc


def cal(*events):
    return "BEGIN:VCALENDAR\r\n" + "".join("BEGIN:VEVENT\r\n" + e.strip().replace("\n", "\r\n") + "\r\nEND:VEVENT\r\n" for e in events) + "END:VCALENDAR\r\n"


def d(*a):
    return datetime(*a, tzinfo=UTC)


class Hidden(unittest.TestCase):
    def test_folded_lines_and_escapes(self):
        ev = parse_ics(cal("SUMMARY:Lunch\\, then a long\n  walk\\; bring\\nshoes\nDTSTART:20261201T120000Z\nDURATION:PT1H30M"))
        self.assertEqual(ev[0].summary, "Lunch, then a long walk; bring\nshoes")
        self.assertEqual(occurrences(ev, d(2026, 12, 1), d(2026, 12, 2))[0][1], d(2026, 12, 1, 13, 30))

    def test_lf_only_and_tab_folding(self):
        ev = parse_ics("BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:A\n\tB\nDTSTART:20261201T080000Z\nEND:VEVENT\nEND:VCALENDAR\n")
        self.assertEqual(ev[0].summary, "AB")

    def test_tzid_converted_to_utc(self):
        ev = parse_ics(cal("SUMMARY:Paris\nDTSTART;TZID=Europe/Paris:20261201T090000\nDTEND;TZID=Europe/Paris:20261201T100000"))
        self.assertEqual(occurrences(ev, d(2026, 12, 1), d(2026, 12, 2)), [(d(2026, 12, 1, 8), d(2026, 12, 1, 9), "Paris")])

    def test_all_day_and_duration_week(self):
        ev = parse_ics(cal("SUMMARY:Holiday\nDTSTART;VALUE=DATE:20261224\nDTEND;VALUE=DATE:20261226",
                           "SUMMARY:Trip\nDTSTART:20261220\nDURATION:P1W"))
        got = occurrences(ev, date(2026, 12, 1), date(2027, 1, 1))
        self.assertEqual(got, [(date(2026, 12, 20), date(2026, 12, 27), "Trip"), (date(2026, 12, 24), date(2026, 12, 26), "Holiday")])

    def test_daily_count_and_interval(self):
        ev = parse_ics(cal("SUMMARY:Pill\nDTSTART:20261201T070000Z\nRRULE:FREQ=DAILY;INTERVAL=2;COUNT=3"))
        got = [o[0] for o in occurrences(ev, d(2026, 11, 1), d(2027, 1, 1))]
        self.assertEqual(got, [d(2026, 12, 1, 7), d(2026, 12, 3, 7), d(2026, 12, 5, 7)])

    def test_until_inclusive(self):
        ev = parse_ics(cal("SUMMARY:X\nDTSTART:20261201T070000Z\nRRULE:FREQ=DAILY;UNTIL=20261203T070000Z"))
        self.assertEqual(len(occurrences(ev, d(2026, 11, 1), d(2027, 1, 1))), 3)

    def test_weekly_byday_interval(self):
        ev = parse_ics(cal("SUMMARY:Gym\nDTSTART:20261202T180000Z\nRRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;COUNT=4"))
        got = [o[0] for o in occurrences(ev, d(2026, 11, 1), d(2027, 2, 1))]
        # DTSTART is Wednesday 2 Dec; that week's Monday is before DTSTART, so it is skipped
        self.assertEqual(got, [d(2026, 12, 2, 18), d(2026, 12, 14, 18), d(2026, 12, 16, 18), d(2026, 12, 28, 18)])

    def test_exdate_multiple_values_and_lines_count_includes_them(self):
        ev = parse_ics(cal("SUMMARY:Y\nDTSTART:20261201T070000Z\nRRULE:FREQ=DAILY;COUNT=5\nEXDATE:20261202T070000Z,20261203T070000Z\nEXDATE:20261205T070000Z"))
        got = [o[0] for o in occurrences(ev, d(2026, 11, 1), d(2027, 1, 1))]
        self.assertEqual(got, [d(2026, 12, 1, 7), d(2026, 12, 4, 7)])

    def test_weekly_keeps_local_time_across_dst(self):
        ev = parse_ics(cal("SUMMARY:Team\nDTSTART;TZID=Europe/Paris:20261019T090000\nDURATION:PT1H\nRRULE:FREQ=WEEKLY;COUNT=3"))
        got = [o[0] for o in occurrences(ev, d(2026, 10, 1), d(2026, 12, 1))]
        self.assertEqual(got, [d(2026, 10, 19, 7), d(2026, 10, 26, 8), d(2026, 11, 2, 8)])

    def test_exdate_with_tzid(self):
        ev = parse_ics(cal("SUMMARY:Z\nDTSTART;TZID=Asia/Tokyo:20261201T100000\nRRULE:FREQ=DAILY;COUNT=3\nEXDATE;TZID=Asia/Tokyo:20261202T100000"))
        got = [o[0] for o in occurrences(ev, d(2026, 11, 1), d(2027, 1, 1))]
        self.assertEqual(got, [d(2026, 12, 1, 1), d(2026, 12, 3, 1)])

    def test_window_and_sorting(self):
        ev = parse_ics(cal("SUMMARY:b\nDTSTART:20261201T090000Z", "SUMMARY:a\nDTSTART:20261201T090000Z", "SUMMARY:late\nDTSTART:20261202T000000Z"))
        got = occurrences(ev, d(2026, 12, 1, 9), d(2026, 12, 2))
        self.assertEqual([o[2] for o in got], ["a", "b"])
''',
    fix={'events.py': lambda _: ICS_REF},
)

# 3. Multi-currency across a package ----------------------------------------------------------
MONEY_REF = {
    'shop/money.py': r'''"""Money in minor units (cents, or yen for JPY)."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN

MINOR = {'EUR': 2, 'USD': 2, 'JPY': 0}
SYMBOL = {'EUR': '€', 'USD': '$', 'JPY': '¥'}


class CurrencyMismatch(ValueError):
    pass


@dataclass(frozen=True)
class Money:
    minor: int
    currency: str = 'EUR'

    def __post_init__(self):
        if self.currency not in MINOR:
            raise ValueError(f'unknown currency {self.currency}')

    @classmethod
    def parse(cls, text, currency='EUR'):
        d = Decimal(text) * (10 ** MINOR[currency])
        return cls(int(d.quantize(Decimal(1), rounding=ROUND_HALF_EVEN)), currency)

    def _same(self, other):
        if not isinstance(other, Money) or other.currency != self.currency:
            raise CurrencyMismatch(f'{self.currency} vs {getattr(other, "currency", other)}')

    def __add__(self, other):
        self._same(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other):
        self._same(other)
        return Money(self.minor - other.minor, self.currency)

    def times(self, n):
        return Money(self.minor * n, self.currency)

    def scaled(self, factor):
        """Multiplied by a Decimal factor, rounded half to even in minor units."""
        v = (Decimal(self.minor) * Decimal(str(factor))).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
        return Money(int(v), self.currency)

    def convert(self, currency, rates):
        """Into another currency; rates maps 'EUR' -> 1 and other codes to how many of them one euro buys."""
        if currency == self.currency:
            return self
        major = Decimal(self.minor) / (10 ** MINOR[self.currency])
        eur = major / Decimal(str(rates[self.currency]))
        target = eur * Decimal(str(rates[currency])) * (10 ** MINOR[currency])
        return Money(int(target.quantize(Decimal(1), rounding=ROUND_HALF_EVEN)), currency)

    def format(self):
        digits = MINOR[self.currency]
        sign = '-' if self.minor < 0 else ''
        n = abs(self.minor)
        whole, frac = divmod(n, 10 ** digits) if digits else (n, 0)
        text = f'{whole:,}' + (f'.{frac:0{digits}d}' if digits else '')
        return f'{sign}{SYMBOL[self.currency]}{text}'
''',
    'shop/tax.py': r'''"""VAT per line, rounded half to even in the currency's minor units."""
from decimal import Decimal

RATES = {'standard': Decimal('0.20'), 'reduced': Decimal('0.055'), 'zero': Decimal('0')}


def line_tax(amount, kind):
    return amount.scaled(RATES[kind])
''',
    'shop/cart.py': r'''"""A cart in one currency."""
from .money import Money
from .tax import line_tax


class Cart:
    def __init__(self, currency='EUR'):
        self.currency = currency
        self.lines = []  # (name, unit Money, quantity, tax kind)

    def add(self, name, unit, quantity=1, tax='standard', rates=None):
        if unit.currency != self.currency:
            if rates is None:
                raise ValueError('rates needed to add a price in another currency')
            unit = unit.convert(self.currency, rates)
        self.lines.append((name, unit, quantity, tax))

    def subtotal(self):
        return sum((u.times(q) for _, u, q, _ in self.lines), Money(0, self.currency))

    def tax(self):
        return sum((line_tax(u.times(q), t) for _, u, q, t in self.lines), Money(0, self.currency))

    def total(self):
        return self.subtotal() + self.tax()
''',
    'shop/report.py': r'''"""A plain-text receipt."""


def receipt(cart):
    lines = [f'{q} x {name}  {u.times(q).format()}' for name, u, q, _ in cart.lines]
    lines += [f'Subtotal  {cart.subtotal().format()}', f'Tax  {cart.tax().format()}', f'Total  {cart.total().format()}']
    return '\n'.join(lines)
''',
}

TASKS['money-multicurrency'] = dict(
    area='a feature across a four-file package (multi-currency money)',
    instruction="""The `shop` package (money.py, tax.py, cart.py, report.py) only handles euros, as integer cents. Add currencies across the package, keeping the existing API working for euros:

- `Money(minor, currency='EUR')` stores an integer amount in the currency's minor units: EUR and USD have 2 decimals, JPY has 0. An unknown currency raises `ValueError`.
- `Money.parse(text, currency='EUR')` reads a decimal string ("12.345" EUR -> 1234 cents, rounding half to even; "1500" JPY -> 1500).
- Adding or subtracting two amounts in different currencies raises `CurrencyMismatch` (a `ValueError` subclass, defined in money.py).
- `money.convert(currency, rates)`: `rates` maps "EUR" to 1 and every other code to how many units of it one euro buys (e.g. {"EUR": 1, "JPY": 160, "USD": 1.08}). Convert through euros using `Decimal`, rounding half to even only once at the end, in the target's minor units.
- `money.format()`: symbol, thousands separators and the currency's decimals: "€1,234.50", "-€0.05", "¥1,235", "$12.00".
- Tax (tax.py): per line, rate times line amount, rounded half to even in the currency's minor units (reduced rate is 5.5%).
- `Cart(currency='EUR')`: `add(name, unit, quantity=1, tax='standard', rates=None)`; a price in another currency is converted to the cart's currency when added (with `rates`; without them, `ValueError`). `subtotal()`, `tax()`, `total()` return Money in the cart's currency.
- `report.receipt(cart)` uses `format()` for every amount.

Make sure `python3 -m unittest` passes.""",
    files={
        'shop/__init__.py': '',
        'shop/money.py': r'''"""Money in euro cents."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Money:
    cents: int

    @classmethod
    def parse(cls, text):
        return cls(round(float(text) * 100))

    def __add__(self, other):
        return Money(self.cents + other.cents)

    def times(self, n):
        return Money(self.cents * n)

    def format(self):
        return f"€{self.cents / 100:.2f}"
''',
        'shop/tax.py': r'''"""VAT per line."""
from .money import Money

RATES = {'standard': 0.20, 'reduced': 0.055, 'zero': 0.0}


def line_tax(amount, kind):
    return Money(round(amount.cents * RATES[kind]))
''',
        'shop/cart.py': r'''"""A cart in euros."""
from .money import Money
from .tax import line_tax


class Cart:
    def __init__(self):
        self.lines = []

    def add(self, name, unit, quantity=1, tax='standard'):
        self.lines.append((name, unit, quantity, tax))

    def subtotal(self):
        return sum((u.times(q) for _, u, q, _ in self.lines), Money(0))

    def tax(self):
        return sum((line_tax(u.times(q), t) for _, u, q, t in self.lines), Money(0))

    def total(self):
        return self.subtotal() + self.tax()
''',
        'shop/report.py': r'''"""A plain-text receipt."""


def receipt(cart):
    lines = [f"{q} x {name}  {u.times(q).format()}" for name, u, q, _ in cart.lines]
    lines += [f"Subtotal  {cart.subtotal().format()}", f"Tax  {cart.tax().format()}", f"Total  {cart.total().format()}"]
    return "\n".join(lines)
''',
        'test_shop.py': r'''import unittest
from shop.money import Money
from shop.cart import Cart


class ShopTest(unittest.TestCase):
    def test_euro_cart(self):
        c = Cart()
        c.add('book', Money.parse('10.00'), 2)
        self.assertEqual(c.total().format(), '€24.00')


if __name__ == '__main__':
    unittest.main()
''',
    },
    hidden=r'''import unittest
from shop.money import Money, CurrencyMismatch
from shop.cart import Cart
from shop.report import receipt

RATES = {'EUR': 1, 'JPY': 160, 'USD': 1.08}


class Hidden(unittest.TestCase):
    def test_minor_units_and_unknown_currency(self):
        self.assertEqual(Money(1500, 'JPY').minor, 1500)
        self.assertEqual(Money(250).currency, 'EUR')
        with self.assertRaises(ValueError):
            Money(1, 'XYZ')

    def test_parse_rounds_half_even(self):
        self.assertEqual(Money.parse('12.345').minor, 1234)
        self.assertEqual(Money.parse('12.355').minor, 1236)
        self.assertEqual(Money.parse('1500', 'JPY').minor, 1500)

    def test_mismatch(self):
        with self.assertRaises(CurrencyMismatch):
            Money(1) + Money(1, 'JPY')
        self.assertTrue(issubclass(CurrencyMismatch, ValueError))

    def test_format(self):
        self.assertEqual(Money(123450).format(), '€1,234.50')
        self.assertEqual(Money(-5).format(), '-€0.05')
        self.assertEqual(Money(1235, 'JPY').format(), '¥1,235')
        self.assertEqual(Money(1200, 'USD').format(), '$12.00')

    def test_convert_through_euros_rounding_once(self):
        self.assertEqual(Money(1000, 'USD').convert('JPY', RATES), Money(1481, 'JPY'))
        self.assertEqual(Money(1234, 'JPY').convert('EUR', RATES), Money(771, 'EUR'))
        self.assertEqual(Money(5, 'EUR').convert('EUR', RATES), Money(5, 'EUR'))

    def test_tax_rounds_per_line_half_even(self):
        c = Cart()
        c.add('a', Money(25), 1, 'reduced')   # 1.375 -> 1
        c.add('b', Money(30), 1, 'reduced')   # 1.65 -> 2
        c.add('c', Money(1250), 1, 'standard')  # 250
        self.assertEqual(c.tax(), Money(253))

    def test_yen_cart(self):
        c = Cart('JPY')
        c.add('tea', Money(1080, 'JPY'), 3, 'reduced')  # 3240, tax 178.2 -> 178
        self.assertEqual(c.subtotal(), Money(3240, 'JPY'))
        self.assertEqual(c.tax(), Money(178, 'JPY'))
        self.assertEqual(c.total().format(), '¥3,418')

    def test_foreign_price_converted_on_add(self):
        c = Cart('EUR')
        c.add('gadget', Money(2000, 'USD'), 1, 'zero', rates=RATES)
        self.assertEqual(c.total(), Money(1852))
        with self.assertRaises(ValueError):
            Cart('EUR').add('x', Money(1, 'JPY'))

    def test_receipt_formats_every_amount(self):
        c = Cart('JPY')
        c.add('tea', Money(1080, 'JPY'), 2, 'zero')
        self.assertEqual(receipt(c), '2 x tea  ¥2,160\nSubtotal  ¥2,160\nTax  ¥0\nTotal  ¥2,160')

    def test_euro_api_still_works(self):
        c = Cart()
        c.add('book', Money.parse('10.00'), 2)
        self.assertEqual(c.total().format(), '€24.00')
''',
    fix={rel: (lambda text, _t=text: _t) for rel, text in MONEY_REF.items()},
)

# 4. JSON Patch -------------------------------------------------------------------------------
PATCH_REF = r'''"""Apply a JSON Patch (RFC 6902) to a document."""
import copy


class PatchError(Exception):
    pass


def _tokens(pointer):
    if pointer == '':
        return []
    if not pointer.startswith('/'):
        raise PatchError(f'bad pointer {pointer!r}')
    return [t.replace('~1', '/').replace('~0', '~') for t in pointer[1:].split('/')]


def _index(container, tok, adding=False):
    if tok == '-' and adding:
        return len(container)
    if not tok.isdigit() or (tok != '0' and tok.startswith('0')):
        raise PatchError(f'bad array index {tok!r}')
    i = int(tok)
    if i > len(container) or (not adding and i == len(container)):
        raise PatchError(f'index {i} out of range')
    return i


def _get(doc, tokens):
    cur = doc
    for t in tokens:
        if isinstance(cur, list):
            cur = cur[_index(cur, t)]
        elif isinstance(cur, dict):
            if t not in cur:
                raise PatchError(f'no member {t!r}')
            cur = cur[t]
        else:
            raise PatchError('path goes through a value')
    return cur


def _add(doc, tokens, value):
    if not tokens:
        return value
    parent = _get(doc, tokens[:-1])
    last = tokens[-1]
    if isinstance(parent, list):
        parent.insert(_index(parent, last, adding=True), value)
    elif isinstance(parent, dict):
        parent[last] = value
    else:
        raise PatchError('parent is not a container')
    return doc


def _remove(doc, tokens):
    if not tokens:
        raise PatchError('cannot remove the root')
    parent = _get(doc, tokens[:-1])
    last = tokens[-1]
    if isinstance(parent, list):
        return doc, parent.pop(_index(parent, last))
    if isinstance(parent, dict):
        if last not in parent:
            raise PatchError(f'no member {last!r}')
        return doc, parent.pop(last)
    raise PatchError('parent is not a container')


def _equal(a, b):
    if type(a) is bool or type(b) is bool:
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_equal(a[k], b[k]) for k in a)
    return a == b


def apply_patch(doc, patch):
    """A new document with the patch applied; doc is never changed. Any failure raises PatchError
    and nothing is applied."""
    doc = copy.deepcopy(doc)
    for op in patch:
        try:
            kind, path = op['op'], op['path']
        except (KeyError, TypeError):
            raise PatchError('operation needs op and path')
        tokens = _tokens(path)
        if kind == 'add':
            if 'value' not in op:
                raise PatchError('add needs a value')
            doc = _add(doc, tokens, copy.deepcopy(op['value']))
        elif kind == 'remove':
            doc, _ = _remove(doc, tokens)
        elif kind == 'replace':
            if 'value' not in op:
                raise PatchError('replace needs a value')
            if not tokens:
                doc = copy.deepcopy(op['value'])
                continue
            _get(doc, tokens)
            doc, _ = _remove(doc, tokens)
            doc = _add(doc, tokens, copy.deepcopy(op['value']))
        elif kind in ('move', 'copy'):
            if 'from' not in op:
                raise PatchError(f'{kind} needs from')
            src = _tokens(op['from'])
            if kind == 'move' and tokens[:len(src)] == src and len(tokens) > len(src):
                raise PatchError('cannot move a value into itself')
            if kind == 'move':
                doc, value = _remove(doc, src)
            else:
                value = copy.deepcopy(_get(doc, src))
            doc = _add(doc, tokens, value)
        elif kind == 'test':
            if 'value' not in op or not _equal(_get(doc, tokens), op['value']):
                raise PatchError(f'test failed at {path!r}')
        else:
            raise PatchError(f'unknown op {kind!r}')
    return doc
'''

TASKS['json-patch'] = dict(
    area='JSON Patch (RFC 6902) with JSON Pointer escaping and atomic failure',
    instruction="""patch.py applies JSON Patch documents, but only partly and with bugs. Make `apply_patch(doc, patch)` implement RFC 6902 on Python values (dicts, lists, str, numbers, bool, None):

- Operations: add, remove, replace, move, copy, test. Paths are JSON Pointers (RFC 6901): "" is the whole document, `~1` means `/` and `~0` means `~` in a token (decode `~1` before `~0`).
- add: into an object sets the member; into an array inserts at the index (0..len) or appends with `-`; the parent must exist. Path "" replaces the whole document.
- remove / replace: the target must exist. replace of "" replaces the document.
- move: like remove from `from` then add at `path`; moving a value into one of its own children is an error. copy: add a deep copy of the value at `from`.
- test: the value at `path` must equal `value`: numbers compare by value (1 == 1.0), but `true` is not `1`, and lists/objects compare deeply.
- Array indexes are decimal without leading zeros ("01" is invalid); an index past the end is an error (except with add, where len is allowed).
- Any error (bad pointer, missing target, failed test, unknown op, missing `value`/`from`) raises `PatchError`, and the patch is atomic: either every operation applies or none does. `apply_patch` never changes `doc`; it returns the new document.

Make sure `python3 -m unittest` passes.""",
    files={
        'patch.py': r'''"""Apply a JSON Patch (RFC 6902) to a document."""


class PatchError(Exception):
    pass


def _walk(doc, path):
    parts = path.lstrip('/').split('/')
    cur = doc
    for p in parts[:-1]:
        cur = cur[int(p)] if isinstance(cur, list) else cur[p]
    return cur, parts[-1]


def apply_patch(doc, patch):
    for op in patch:
        parent, key = _walk(doc, op['path'])
        if isinstance(parent, list):
            key = len(parent) if key == '-' else int(key)
        if op['op'] == 'add':
            if isinstance(parent, list):
                parent.insert(key, op['value'])
            else:
                parent[key] = op['value']
        elif op['op'] == 'remove':
            del parent[key]
        elif op['op'] == 'replace':
            parent[key] = op['value']
        elif op['op'] == 'test':
            if parent[key] != op['value']:
                raise PatchError('test failed')
    return doc
''',
        'test_patch.py': r'''import unittest
from patch import apply_patch


class PatchTest(unittest.TestCase):
    def test_add_member(self):
        self.assertEqual(apply_patch({'a': 1}, [{'op': 'add', 'path': '/b', 'value': 2}]), {'a': 1, 'b': 2})


if __name__ == '__main__':
    unittest.main()
''',
    },
    hidden=r'''import copy, unittest
from patch import apply_patch, PatchError


class Hidden(unittest.TestCase):
    def bad(self, doc, patch):
        before = copy.deepcopy(doc)
        with self.assertRaises(PatchError):
            apply_patch(doc, patch)
        self.assertEqual(doc, before)

    def test_doc_not_mutated(self):
        doc = {'a': [1, 2]}
        out = apply_patch(doc, [{'op': 'add', 'path': '/a/-', 'value': 3}])
        self.assertEqual(out, {'a': [1, 2, 3]})
        self.assertEqual(doc, {'a': [1, 2]})

    def test_pointer_escapes(self):
        doc = {'a/b': 1, 'm~n': 2}
        out = apply_patch(doc, [{'op': 'replace', 'path': '/a~1b', 'value': 10}, {'op': 'remove', 'path': '/m~0n'}])
        self.assertEqual(out, {'a/b': 10})
        self.assertEqual(apply_patch({'~1': 5}, [{'op': 'test', 'path': '/~01', 'value': 5}]), {'~1': 5})

    def test_array_insert_and_bounds(self):
        self.assertEqual(apply_patch([1, 3], [{'op': 'add', 'path': '/1', 'value': 2}]), [1, 2, 3])
        self.assertEqual(apply_patch([1], [{'op': 'add', 'path': '/1', 'value': 2}]), [1, 2])
        self.bad([1], [{'op': 'add', 'path': '/3', 'value': 2}])
        self.bad([1, 2], [{'op': 'remove', 'path': '/01'}])
        self.bad([1, 2], [{'op': 'replace', 'path': '/2', 'value': 0}])

    def test_root_operations(self):
        self.assertEqual(apply_patch({'a': 1}, [{'op': 'replace', 'path': '', 'value': [1]}]), [1])
        self.assertEqual(apply_patch({'a': 1}, [{'op': 'add', 'path': '', 'value': 'x'}]), 'x')

    def test_missing_targets_fail(self):
        self.bad({'a': 1}, [{'op': 'remove', 'path': '/b'}])
        self.bad({'a': 1}, [{'op': 'replace', 'path': '/b', 'value': 1}])
        self.bad({'a': 1}, [{'op': 'add', 'path': '/x/y', 'value': 1}])

    def test_move_and_copy(self):
        doc = {'a': {'b': [1, 2]}, 'c': None}
        out = apply_patch(doc, [{'op': 'copy', 'from': '/a/b', 'path': '/c'}, {'op': 'move', 'from': '/a/b/0', 'path': '/d'}])
        self.assertEqual(out, {'a': {'b': [2]}, 'c': [1, 2], 'd': 1})
        self.bad({'a': {'b': 1}}, [{'op': 'move', 'from': '/a', 'path': '/a/b/c'}])

    def test_copy_is_deep(self):
        out = apply_patch({'a': {'x': 1}}, [{'op': 'copy', 'from': '/a', 'path': '/b'}, {'op': 'replace', 'path': '/b/x', 'value': 2}])
        self.assertEqual(out, {'a': {'x': 1}, 'b': {'x': 2}})

    def test_test_semantics(self):
        apply_patch({'n': 1}, [{'op': 'test', 'path': '/n', 'value': 1.0}])
        apply_patch({'l': [1, {'a': None}]}, [{'op': 'test', 'path': '/l', 'value': [1, {'a': None}]}])
        self.bad({'n': 1}, [{'op': 'test', 'path': '/n', 'value': True}])
        self.bad({'n': True}, [{'op': 'test', 'path': '/n', 'value': 1}])

    def test_atomic(self):
        self.bad({'a': [1]}, [{'op': 'add', 'path': '/a/-', 'value': 2}, {'op': 'test', 'path': '/a/0', 'value': 9}])

    def test_malformed_operations(self):
        self.bad({}, [{'op': 'jump', 'path': '/a'}])
        self.bad({}, [{'op': 'add', 'path': '/a'}])
        self.bad({'a': 1}, [{'op': 'copy', 'path': '/b'}])
        self.bad({}, [{'op': 'add', 'path': 'a', 'value': 1}])
''',
    fix={'patch.py': lambda _: PATCH_REF},
)

# 5. Overlaps, fast ---------------------------------------------------------------------------
INTERVALS_REF = r'''"""Busy times from a list of half-open intervals [start, end)."""
import heapq


def busiest(intervals):
    """(most intervals overlapping at one moment, the earliest moment it happens); (0, None) if none."""
    events = []
    for s, e in intervals:
        if e > s:
            events.append((s, 1))
            events.append((e, -1))
    events.sort(key=lambda x: (x[0], x[1]))  # an end before a start at the same time: touching is not overlapping
    best, when, cur = 0, None, 0
    for t, d in events:
        cur += d
        if cur > best:
            best, when = cur, t
    return best, when


def free_slots(intervals, day_start, day_end, min_length):
    """Gaps of at least min_length inside [day_start, day_end) not covered by any interval, in order."""
    spans = sorted((max(s, day_start), min(e, day_end)) for s, e in intervals if e > s and e > day_start and s < day_end)
    out, cur = [], day_start
    for s, e in spans:
        if s > cur and s - cur >= min_length:
            out.append((cur, s))
        cur = max(cur, e)
    if day_end - cur >= min_length and day_end > cur:
        out.append((cur, day_end))
    return out


def rooms_needed(intervals):
    """Fewest rooms so that no two intervals in the same room overlap."""
    return busiest(intervals)[0]
'''

TASKS['busy-intervals'] = dict(
    area='correct and fast: overlap counting for 200,000 intervals',
    instruction="""intervals.py computes busy times from half-open intervals `(start, end)` meaning [start, end) (integers; touching intervals such as (1, 3) and (3, 5) do not overlap; intervals with end <= start are ignored). It gives wrong answers on edge cases and is far too slow. Make it correct, and fast enough that each function handles 200,000 intervals in well under 2 seconds:

- `busiest(intervals)` returns `(count, moment)`: the largest number of intervals that overlap at one moment, and the earliest moment where that count is reached; `(0, None)` when there are no valid intervals.
- `free_slots(intervals, day_start, day_end, min_length)` returns the gaps inside [day_start, day_end) that no interval covers, as `(start, end)` tuples in order, keeping only gaps of length >= min_length. Intervals may extend outside the day.
- `rooms_needed(intervals)` returns the fewest rooms so that no two intervals in one room overlap.

Make sure `python3 -m unittest` passes.""",
    files={
        'intervals.py': r'''"""Busy times from a list of intervals."""


def busiest(intervals):
    best, when = 0, None
    for s, _ in intervals:
        n = sum(1 for a, b in intervals if a <= s <= b)
        if n > best:
            best, when = n, s
    return best, when


def free_slots(intervals, day_start, day_end, min_length):
    out, t = [], day_start
    for s, e in sorted(intervals):
        if s - t >= min_length:
            out.append((t, s))
        t = e
    if day_end - t >= min_length:
        out.append((t, day_end))
    return out


def rooms_needed(intervals):
    rooms = []
    for s, e in sorted(intervals):
        for i, end in enumerate(rooms):
            if end < s:
                rooms[i] = e
                break
        else:
            rooms.append(e)
    return len(rooms)
''',
        'test_intervals.py': r'''import unittest
from intervals import busiest, free_slots, rooms_needed


class IntervalsTest(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(busiest([(1, 5), (2, 6), (8, 9)]), (2, 2))
        self.assertEqual(free_slots([(2, 4)], 0, 10, 1), [(0, 2), (4, 10)])
        self.assertEqual(rooms_needed([(1, 5), (2, 6), (8, 9)]), 2)


if __name__ == '__main__':
    unittest.main()
''',
    },
    hidden=r'''import random, time, unittest
from intervals import busiest, free_slots, rooms_needed


def brute_busiest(iv):
    pts = sorted({s for s, e in iv if e > s})
    best, when = 0, None
    for p in pts:
        n = sum(1 for s, e in iv if s <= p < e)
        if n > best:
            best, when = n, p
    return best, when


class Hidden(unittest.TestCase):
    def test_touching_is_not_overlap(self):
        self.assertEqual(busiest([(1, 3), (3, 5)]), (1, 1))
        self.assertEqual(rooms_needed([(1, 3), (3, 5), (5, 7)]), 1)

    def test_empty_and_invalid(self):
        self.assertEqual(busiest([]), (0, None))
        self.assertEqual(busiest([(5, 5), (6, 2)]), (0, None))
        self.assertEqual(rooms_needed([(4, 4)]), 0)

    def test_busiest_matches_brute_force(self):
        rnd = random.Random(7)
        for _ in range(200):
            iv = [(a, a + rnd.randint(-2, 6)) for a in (rnd.randint(0, 20) for _ in range(rnd.randint(0, 12)))]
            self.assertEqual(busiest(iv), brute_busiest(iv), iv)

    def test_free_slots_edges(self):
        self.assertEqual(free_slots([(-5, 2), (8, 20)], 0, 10, 1), [(2, 8)])
        self.assertEqual(free_slots([(2, 5), (3, 4), (6, 7)], 0, 10, 2), [(0, 2), (7, 10)])
        self.assertEqual(free_slots([], 0, 10, 11), [])
        self.assertEqual(free_slots([(0, 10)], 0, 10, 0), [])

    def test_rooms_match_overlap(self):
        rnd = random.Random(3)
        for _ in range(100):
            iv = [(a, a + rnd.randint(1, 5)) for a in (rnd.randint(0, 15) for _ in range(rnd.randint(1, 10)))]
            self.assertEqual(rooms_needed(iv), brute_busiest(iv)[0], iv)

    def _fast(self, fn):
        import threading
        rnd = random.Random(1)
        iv = [(a, a + rnd.randint(1, 500)) for a in (rnd.randint(0, 10**7) for _ in range(200_000))]
        th = threading.Thread(target=fn, args=(iv,), daemon=True)  # a slow version fails this test only
        t = time.perf_counter()
        th.start()
        th.join(3.0)
        self.assertFalse(th.is_alive(), 'slower than 3 s on 200,000 intervals')
        self.assertLess(time.perf_counter() - t, 3.0)

    def test_busiest_fast_on_200k(self):
        self._fast(busiest)

    def test_free_slots_fast_on_200k(self):
        self._fast(lambda iv: free_slots(iv, 0, 10**7, 50))

    def test_rooms_fast_on_200k(self):
        self._fast(rooms_needed)
''',
    fix={'intervals.py': lambda _: INTERVALS_REF},
)


def _validate():
    import shutil, tempfile
    bad = 0
    for name, task in TASKS.items():
        with tempfile.TemporaryDirectory() as start, tempfile.TemporaryDirectory() as fixed:
            write_files(task, start)
            passed, total, _ = run_hidden(task, start)
            write_files(task, fixed)
            for rel, change in task['fix'].items():
                p = Path(fixed, rel)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(change(p.read_text() if p.exists() else ''))
            fpassed, ftotal, out = run_hidden(task, fixed)
            vis_ok, vis_out = run_visible(fixed)
            ok = ftotal and fpassed == ftotal and passed < (total or 1) and vis_ok
            print(f"{'ok  ' if ok else 'FAIL'} {name}: start {passed}/{total}, reference fix {fpassed}/{ftotal}, visible tests with fix {'pass' if vis_ok else 'FAIL'}")
            if not ok:
                bad += 1
                print(out[-2500:], vis_out[-800:])
    return bad


if __name__ == '__main__':
    sys.exit(1 if _validate() else 0)
