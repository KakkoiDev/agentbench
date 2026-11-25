"""Hidden tests for cron-dst: hand-checked cases, a minute-by-minute oracle around DST changes, speed limits."""
import random
from datetime import date, datetime, timedelta, timezone

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

# ---- an independent, slow oracle: walks the clock minute by minute ----
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
_UTC = timezone.utc
_MON = {n: i for i, n in enumerate('JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC'.split(), 1)}
_DOW = {n: i for i, n in enumerate('SUN MON TUE WED THU FRI SAT'.split())}
_MACRO = {'@yearly': '0 0 1 1 *', '@annually': '0 0 1 1 *', '@monthly': '0 0 1 * *', '@weekly': '0 0 * * 0',
          '@daily': '0 0 * * *', '@midnight': '0 0 * * *', '@hourly': '0 * * * *'}


def _oracle_field(text, lo, hi, vmax, names):
    vals = set()
    for item in text.split(','):
        base, slash, step = item.partition('/')
        step = int(step) if slash else 1
        conv = lambda x: names[x.upper()] if names and x.upper() in names else int(x)
        if base == '*':
            a, b = lo, hi
        elif '-' in base:
            a, b = map(conv, base.split('-'))
        else:
            a = conv(base)
            b = vmax if slash else a
        vals |= set(range(a, b + 1, step))
    return vals


def oracle_matcher(expr):
    expr = _MACRO.get(expr.strip(), expr)
    f = expr.split()
    mins = _oracle_field(f[0], 0, 59, 59, None)
    hours = _oracle_field(f[1], 0, 23, 23, None)
    doms = _oracle_field(f[2], 1, 31, 31, None)
    mons = _oracle_field(f[3], 1, 12, 12, _MON)
    dows = {d % 7 for d in _oracle_field(f[4], 0, 6, 7, _DOW)}
    either_star = f[2].startswith('*') or f[4].startswith('*')

    def match(w):
        if w.minute not in mins or w.hour not in hours or w.month not in mons:
            return False
        a, b = w.day in doms, (w.weekday() + 1) % 7 in dows
        return (a and b) if either_star else (a or b)
    return match


def oracle_fires(expr, start, end, tz):
    """Fire instants t, start <= t < end: each instant at which the wall clock first reaches or jumps over
    a matching wall time. Walks every UTC minute from two days before start."""
    z = ZoneInfo(tz)
    match = oracle_matcher(expr)
    t = (start - timedelta(days=2)).astimezone(_UTC).replace(second=0, microsecond=0)
    seen = t.astimezone(z).replace(tzinfo=None)     # the latest wall time shown so far
    out = []
    while t < end:
        t += timedelta(minutes=1)
        w = t.astimezone(z).replace(tzinfo=None)
        if w > seen:
            hit, x = False, seen + timedelta(minutes=1)
            while x <= w and not hit:
                hit = match(x)
                x += timedelta(minutes=1)
            if hit and start <= t < end:
                out.append(t)
            seen = w
    return out
# ---- end of oracle ----

from cron import CronError, fires_between, next_fire

U = timezone.utc


def s(t):
    return None if t is None else str(t)


class Basics(Limited):
    def nf(self, expr, after, tz='UTC'):
        return s(next_fire(expr, after, tz))

    def test_result_is_in_the_zone(self):
        t = next_fire('0 12 * * *', datetime(2025, 6, 1, 2, 59, tzinfo=U), 'Asia/Tokyo')
        self.assertEqual(str(t), '2025-06-01 12:00:00+09:00')
        self.assertEqual(getattr(t.tzinfo, 'key', None), 'Asia/Tokyo')

    def test_strictly_after(self):
        self.assertEqual(self.nf('0 * * * *', datetime(2025, 1, 1, 10, 0, tzinfo=U)), '2025-01-01 11:00:00+00:00')
        self.assertEqual(self.nf('0 * * * *', datetime(2025, 1, 1, 9, 59, 59, 999999, tzinfo=U)), '2025-01-01 10:00:00+00:00')
        self.assertEqual(self.nf('* * * * *', datetime(2025, 1, 1, 10, 0, 30, tzinfo=U)), '2025-01-01 10:01:00+00:00')

    def test_after_given_in_another_zone(self):
        after = datetime(2025, 1, 1, 23, 30, tzinfo=timezone(timedelta(hours=-5)))   # 04:30 UTC on Jan 2
        self.assertEqual(self.nf('0 5 * * *', after, 'Europe/London'), '2025-01-02 05:00:00+00:00')

    def test_lists_ranges_steps(self):
        got = fires_between('5,10-12,50/5 */6 * * *', datetime(2025, 1, 1, tzinfo=U), datetime(2025, 1, 1, 1, tzinfo=U), 'UTC')
        self.assertEqual([t.minute for t in got], [5, 10, 11, 12, 50, 55])
        got = fires_between('0 1-10/4 * * *', datetime(2025, 1, 1, tzinfo=U), datetime(2025, 1, 2, tzinfo=U), 'UTC')
        self.assertEqual([t.hour for t in got], [1, 5, 9])
        got = fires_between('0 0 */10 * *', datetime(2025, 1, 1, tzinfo=U), datetime(2025, 2, 1, tzinfo=U), 'UTC')
        self.assertEqual([t.day for t in got], [1, 11, 21, 31])

    def test_names_case_insensitive(self):
        got = fires_between('0 0 1 jan,Mar-MAY *', datetime(2025, 1, 1, tzinfo=U), datetime(2026, 1, 1, tzinfo=U), 'UTC')
        self.assertEqual([t.month for t in got], [1, 3, 4, 5])
        got = fires_between('0 0 * * mon-WED', datetime(2025, 1, 5, tzinfo=U), datetime(2025, 1, 12, tzinfo=U), 'UTC')
        self.assertEqual([t.day for t in got], [6, 7, 8])

    def test_sunday_is_0_and_7(self):
        for dow in ('0', '7', 'SUN', '7-7', '6-7'):
            got = fires_between(f'0 0 * * {dow}', datetime(2025, 1, 1, tzinfo=U), datetime(2025, 1, 13, tzinfo=U), 'UTC')
            self.assertEqual([t.day for t in got], [4, 5, 11, 12] if dow == '6-7' else [5, 12], dow)
        got = fires_between('0 0 * * 5/1', datetime(2025, 1, 1, tzinfo=U), datetime(2025, 1, 8, tzinfo=U), 'UTC')
        self.assertEqual([t.day for t in got], [3, 4, 5])
        got = fires_between('0 0 * * */3', datetime(2025, 1, 1, tzinfo=U), datetime(2025, 1, 8, tzinfo=U), 'UTC')
        self.assertEqual([t.day for t in got], [1, 4, 5])   # Wed, Sat, Sun

    def test_macros(self):
        a = datetime(2025, 5, 14, 13, 7, tzinfo=U)
        self.assertEqual(self.nf('@yearly', a), '2026-01-01 00:00:00+00:00')
        self.assertEqual(self.nf('@annually', a), '2026-01-01 00:00:00+00:00')
        self.assertEqual(self.nf('@monthly', a), '2025-06-01 00:00:00+00:00')
        self.assertEqual(self.nf('@weekly', a), '2025-05-18 00:00:00+00:00')
        self.assertEqual(self.nf('@daily', a), '2025-05-15 00:00:00+00:00')
        self.assertEqual(self.nf('@midnight', a), '2025-05-15 00:00:00+00:00')
        self.assertEqual(self.nf('@hourly', a), '2025-05-14 14:00:00+00:00')
        self.assertEqual(self.nf('  @hourly ', a), '2025-05-14 14:00:00+00:00')

    def test_whitespace_between_fields(self):
        self.assertEqual(self.nf(' 0\t12  * *   * ', datetime(2025, 1, 1, tzinfo=U)), '2025-01-01 12:00:00+00:00')

    def test_vixie_day_rule_or(self):
        got = fires_between('0 0 13 * 5', datetime(2025, 6, 1, tzinfo=U), datetime(2025, 7, 1, tzinfo=U), 'UTC')
        self.assertEqual([t.day for t in got], [6, 13, 20, 27])
        got = fires_between('0 0 1,15 * 1', datetime(2025, 9, 1, tzinfo=U), datetime(2025, 10, 1, tzinfo=U), 'UTC')
        self.assertEqual([t.day for t in got], [1, 8, 15, 22, 29])

    def test_vixie_day_rule_star_step_means_both(self):
        got = fires_between('0 0 */10 * 1', datetime(2025, 1, 1, tzinfo=U), datetime(2026, 1, 1, tzinfo=U), 'UTC')
        self.assertEqual([(t.month, t.day) for t in got], [(3, 31), (4, 21), (7, 21), (8, 11), (9, 1), (12, 1)])
        got = fires_between('0 0 1 * */2', datetime(2025, 1, 1, tzinfo=U), datetime(2025, 7, 1, tzinfo=U), 'UTC')
        self.assertEqual([(t.month, t.day) for t in got], [(2, 1), (3, 1), (4, 1), (5, 1), (6, 1)])   # Sun/Tue/Thu/Sat only

    def test_impossible_dates(self):
        a = datetime(2025, 1, 1, tzinfo=U)
        self.assertIsNone(next_fire('0 0 30 2 *', a, 'UTC'))
        self.assertIsNone(next_fire('0 0 31 4,6,9,11 *', a, 'UTC'))
        self.assertIsNone(next_fire('* * 31 2-4/2 *', a, 'UTC'))
        self.assertEqual(fires_between('0 0 30 2 *', a, datetime(2030, 1, 1, tzinfo=U), 'UTC'), [])
        self.assertEqual(self.nf('0 0 31 2 1', a), '2025-02-03 00:00:00+00:00')
        self.assertEqual(self.nf('0 0 31 * *', datetime(2025, 4, 1, tzinfo=U)), '2025-05-31 00:00:00+00:00')

    def test_leap_days(self):
        self.assertEqual(self.nf('0 0 29 2 *', datetime(2024, 3, 1, tzinfo=U)), '2028-02-29 00:00:00+00:00')
        self.assertEqual(self.nf('0 0 29 2 *', datetime(2096, 3, 1, tzinfo=U)), '2104-02-29 00:00:00+00:00')
        self.assertEqual(self.nf('0 0 29 2 */7', datetime(2025, 1, 1, tzinfo=U)), '2032-02-29 00:00:00+00:00')

    def test_errors(self):
        a = datetime(2025, 1, 1, tzinfo=U)
        for e in ['60 * * * *', '* 24 * * *', '* * 0 * *', '* * 32 * *', '* * * 0 *', '* * * 13 *', '* * * * 8', '*/0 * * * *',
                  '5-1 * * * *', '* * * *', '* * * * * *', '', '@reboot', '@DAILY', 'a * * * *', '1,,2 * * * *', ',1 * * * *',
                  '* * * JAN-FOO *', 'MON * * * *', '* * * * JAN', '-1 * * * *', '1-2-3 * * * *', '*/x * * * *', '1/ * * * *',
                  '** * * * *', '0 0 * FEB-JAN *']:
            with self.assertRaises(CronError, msg=repr(e)):
                next_fire(e, a, 'UTC')
        with self.assertRaises(CronError):
            next_fire('* * * * *', a, 'Mars/Olympus_Mons')
        with self.assertRaises(CronError):
            next_fire('* * * * *', datetime(2025, 1, 1), 'UTC')
        with self.assertRaises(CronError):
            fires_between('* * * * *', datetime(2025, 1, 1), datetime(2025, 1, 2, tzinfo=U), 'UTC')


class DaylightSaving(Limited):
    NY = 'America/New_York'

    def test_spring_gap_fires_at_the_jump(self):
        self.assertEqual(s(next_fire('30 2 * * *', datetime(2025, 3, 9, 5, tzinfo=U), self.NY)), '2025-03-09 03:00:00-04:00')
        self.assertEqual(s(next_fire('30 2 * * *', datetime(2025, 3, 9, 7, tzinfo=U), self.NY)), '2025-03-10 02:30:00-04:00')

    def test_spring_gap_fires_once_for_many_times(self):
        got = fires_between('*/15 * * * *', datetime(2025, 3, 9, 6, 30, tzinfo=U), datetime(2025, 3, 9, 7, 31, tzinfo=U), self.NY)
        self.assertEqual([s(t) for t in got], ['2025-03-09 01:30:00-05:00', '2025-03-09 01:45:00-05:00',
                                               '2025-03-09 03:00:00-04:00', '2025-03-09 03:15:00-04:00', '2025-03-09 03:30:00-04:00'])

    def test_spring_gap_only_on_that_date(self):
        self.assertEqual(s(next_fire('45 2 9 3 *', datetime(2025, 3, 1, tzinfo=U), self.NY)), '2025-03-09 03:00:00-04:00')
        self.assertEqual(s(next_fire('0 2 * * 0', datetime(2025, 3, 2, 12, tzinfo=U), self.NY)), '2025-03-09 03:00:00-04:00')

    def test_fall_back_fires_first_occurrence_only(self):
        got = fires_between('*/30 * * * *', datetime(2025, 11, 2, 4, tzinfo=U), datetime(2025, 11, 2, 8, 1, tzinfo=U), self.NY)
        self.assertEqual([s(t) for t in got], ['2025-11-02 00:00:00-04:00', '2025-11-02 00:30:00-04:00', '2025-11-02 01:00:00-04:00',
                                               '2025-11-02 01:30:00-04:00', '2025-11-02 02:00:00-05:00', '2025-11-02 02:30:00-05:00',
                                               '2025-11-02 03:00:00-05:00'])
        self.assertEqual([t.fold for t in got], [0, 0, 0, 0, 0, 0, 0])

    def test_after_inside_the_repeated_hour(self):
        after = datetime(2025, 11, 2, 6, 10, tzinfo=U)   # 01:10 EST, the second 01:10
        self.assertEqual(s(next_fire('30 1 * * *', after, self.NY)), '2025-11-03 01:30:00-05:00')
        self.assertEqual(s(next_fire('0 2 * * *', after, self.NY)), '2025-11-02 02:00:00-05:00')
        self.assertEqual(s(next_fire('* * * * *', after, self.NY)), '2025-11-02 02:00:00-05:00')
        after = datetime(2025, 11, 2, 5, 10, tzinfo=U)   # 01:10 EDT, the first 01:10
        self.assertEqual(s(next_fire('30 1 * * *', after, self.NY)), '2025-11-02 01:30:00-04:00')

    def test_midnight_gap_havana(self):
        self.assertEqual(s(next_fire('0 0 * * *', datetime(2025, 3, 8, 12, tzinfo=U), 'America/Havana')), '2025-03-09 01:00:00-04:00')
        self.assertEqual(s(next_fire('@daily', datetime(2025, 3, 9, 6, tzinfo=U), 'America/Havana')), '2025-03-10 00:00:00-04:00')

    def test_half_hour_gap_lord_howe(self):
        self.assertEqual(s(next_fire('15 2 * * *', datetime(2025, 10, 4, 12, tzinfo=U), 'Australia/Lord_Howe')), '2025-10-05 02:30:00+11:00')
        got = fires_between('*/20 1-2 * * *', datetime(2026, 4, 4, 14, tzinfo=U), datetime(2026, 4, 4, 16, tzinfo=U), 'Australia/Lord_Howe')
        self.assertEqual([s(t) for t in got], ['2026-04-05 01:00:00+11:00', '2026-04-05 01:20:00+11:00', '2026-04-05 01:40:00+11:00',
                                               '2026-04-05 02:00:00+10:30', '2026-04-05 02:20:00+10:30'])

    def test_skipped_day_apia(self):
        got = fires_between('0 12 * * *', datetime(2011, 12, 28, tzinfo=U), datetime(2012, 1, 1, tzinfo=U), 'Pacific/Apia')
        self.assertEqual([s(t) for t in got], ['2011-12-28 12:00:00-10:00', '2011-12-29 12:00:00-10:00',
                                               '2011-12-31 00:00:00+14:00', '2011-12-31 12:00:00+14:00', '2012-01-01 12:00:00+14:00'])

    def test_fall_back_berlin_fold(self):
        t = next_fire('30 2 * * *', datetime(2025, 10, 26, tzinfo=U), 'Europe/Berlin')
        self.assertEqual((s(t), t.astimezone(U).hour), ('2025-10-26 02:30:00+02:00', 0))


ZONES = {
    'America/New_York': [datetime(2025, 3, 9, 7, tzinfo=U), datetime(2025, 11, 2, 6, tzinfo=U)],
    'Europe/Berlin': [datetime(2025, 3, 30, 1, tzinfo=U), datetime(2025, 10, 26, 1, tzinfo=U)],
    'Australia/Lord_Howe': [datetime(2025, 10, 4, 15, 30, tzinfo=U), datetime(2025, 4, 5, 15, tzinfo=U)],
    'America/Havana': [datetime(2025, 3, 9, 5, tzinfo=U), datetime(2025, 11, 2, 5, tzinfo=U)],
    'Pacific/Apia': [datetime(2011, 12, 30, 10, tzinfo=U)],
    'America/Sao_Paulo': [datetime(2018, 11, 4, 3, tzinfo=U), datetime(2019, 2, 17, 2, tzinfo=U)],
    'Pacific/Chatham': [datetime(2025, 9, 27, 14, tzinfo=U), datetime(2025, 4, 5, 14, tzinfo=U)],
    'Asia/Kolkata': [datetime(2025, 6, 1, tzinfo=U)],
    'America/St_Johns': [datetime(2025, 3, 9, 5, 30, tzinfo=U), datetime(2025, 11, 2, 4, 30, tzinfo=U)],
}
MENU = [['*', '0', '30', '*/15', '5-10', '0,45', '59', '*/7', '10/20'], ['*', '1', '2', '3', '0', '1-3', '*/2', '23', '2/3'],
        ['*', '1', '15', '*/10', '9,30', '2-4', '31'], ['*', '3', '10,11', 'MAR-NOV', '*/2', 'dec', '2/3'],
        ['*', '0', 'SUN', '1-5', 'MON,FRI', '7', '5/1', '*/3', '6-7']]


class AgainstOracle(Limited):
    LIMIT = 90

    def run_zone(self, tz, seed, n=40):
        rnd = random.Random(seed)
        for _ in range(n):
            c = rnd.choice(ZONES[tz])
            start = c + timedelta(minutes=rnd.randint(-36 * 60, 12 * 60), seconds=rnd.choice([0, 0, 30]))
            end = start + timedelta(hours=rnd.randint(1, 50))
            e = ' '.join(rnd.choice(m) for m in MENU)
            exp = oracle_fires(e, start, end, tz)
            got = fires_between(e, start, end, tz)
            self.assertEqual([t.astimezone(U) for t in got], exp, (e, tz, start, end))
            self.assertTrue(all(getattr(t.tzinfo, 'key', None) == tz for t in got))
            later = [t for t in exp if t > start]
            if later:
                self.assertEqual(next_fire(e, start, tz).astimezone(U), later[0], (e, tz, start))

    def test_new_york(self):
        self.run_zone('America/New_York', 1)

    def test_berlin(self):
        self.run_zone('Europe/Berlin', 2)

    def test_lord_howe(self):
        self.run_zone('Australia/Lord_Howe', 3)

    def test_havana(self):
        self.run_zone('America/Havana', 4)

    def test_apia(self):
        self.run_zone('Pacific/Apia', 5, 25)

    def test_sao_paulo(self):
        self.run_zone('America/Sao_Paulo', 6)

    def test_chatham(self):
        self.run_zone('Pacific/Chatham', 7)

    def test_kolkata_and_st_johns(self):
        self.run_zone('Asia/Kolkata', 8, 20)
        self.run_zone('America/St_Johns', 9, 30)


class Speed(Limited):
    LIMIT = 120

    def test_rare_expressions(self):
        setup = '''
from datetime import datetime, timedelta, timezone
from cron import next_fire
U = timezone.utc
import random
rnd = random.Random(5)
cases = []
for i in range(300):
    e = rnd.choice(['0 0 29 2 *', '59 23 31 12 *', '0 0 31 * *', '0 0 29 2 1', '0 12 13 * 5', '0 0 1 1 1', '30 2 29 2 */7', '0 0 31 2 *'])
    a = datetime(2000 + rnd.randint(0, 120), rnd.randint(1, 12), rnd.randint(1, 28), tzinfo=U)
    cases.append((e, a, rnd.choice(['UTC', 'America/New_York', 'Europe/Berlin'])))
'''
        check = '''
assert results[0] is not None
assert sum(r is None for r in results) == sum(c[0] == '0 0 31 2 *' for c in cases), 'only 31 February never fires'
'''
        self.timed(setup, 'results = [next_fire(e, a, tz) for e, a, tz in cases]', 6, check)

    def test_a_month_of_minutes(self):
        setup = '''
from datetime import datetime, timezone
from cron import fires_between
U = timezone.utc
'''
        self.timed(setup, "r = fires_between('* * * * *', datetime(2025, 10, 15, tzinfo=U), datetime(2025, 11, 15, tzinfo=U), 'America/New_York')", 8,
                   "assert len(r) == 31 * 1440 - 60, len(r)")

    def test_working_hours_for_a_year(self):
        setup = '''
from datetime import datetime, timezone
from cron import fires_between
U = timezone.utc
'''
        self.timed(setup, "r = fires_between('*/5 9-17 * * MON-FRI', datetime(2025, 1, 1, tzinfo=U), datetime(2026, 1, 1, tzinfo=U), 'Europe/Berlin')", 8,
                   "assert len(r) == 261 * 108, len(r)")


if __name__ == '__main__':
    unittest.main()
