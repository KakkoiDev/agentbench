"""Cron expressions evaluated against the wall clock of a real time zone."""
import calendar
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc


class CronError(Exception):
    pass


MONTHS = {n: i for i, n in enumerate(['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'], 1)}
DAYS = {n: i for i, n in enumerate(['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'])}
MACROS = {'@yearly': '0 0 1 1 *', '@annually': '0 0 1 1 *', '@monthly': '0 0 1 * *', '@weekly': '0 0 * * 0',
          '@daily': '0 0 * * *', '@midnight': '0 0 * * *', '@hourly': '0 * * * *'}
# (min, max for '*', max for 'v/s', names)
FIELDS = [(0, 59, 59, None), (0, 23, 23, None), (1, 31, 31, None), (1, 12, 12, MONTHS), (0, 6, 7, DAYS)]


def _value(text, lo, hi, names):
    if names and text.upper() in names:
        return names[text.upper()]
    if not text.isdigit() or not text.isascii():
        raise CronError(f'bad value {text!r}')
    v = int(text)
    if not lo <= v <= hi:
        raise CronError(f'value {v} out of range')
    return v


def _field(text, index):
    lo, star_hi, step_hi, names = FIELDS[index]
    hi = 7 if index == 4 else star_hi
    out = set()
    for item in text.split(','):
        if not item:
            raise CronError('empty list item')
        base, _, step_s = item.partition('/')
        if _:
            if not step_s.isdigit() or not step_s.isascii() or int(step_s) == 0:
                raise CronError(f'bad step in {item!r}')
            step = int(step_s)
        else:
            step = 1
        if base == '*':
            a, b = lo, star_hi
        elif '-' in base:
            x, _, y = base.partition('-')
            a, b = _value(x, lo, hi, names), _value(y, lo, hi, names)
            if a > b:
                raise CronError(f'bad range {item!r}')
        else:
            a = _value(base, lo, hi, names)
            b = step_hi if _ else a
        out.update(range(a, b + 1, step))
    if index == 4 and 7 in out:
        out.discard(7)
        out.add(0)
    return frozenset(out)


@lru_cache(maxsize=1024)
def parse(expr):
    if not isinstance(expr, str):
        raise CronError('expression must be a string')
    e = expr.strip()
    if e.startswith('@'):
        if e not in MACROS:
            raise CronError(f'unknown macro {e!r}')
        e = MACROS[e]
    parts = e.split()
    if len(parts) != 5:
        raise CronError('a cron expression has 5 fields')
    sets = [_field(p, i) for i, p in enumerate(parts)]
    return sets, parts[2].startswith('*'), parts[4].startswith('*')


def _zone(tz):
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as ex:
        raise CronError(f'unknown time zone {tz!r}') from ex


def _aware(dt):
    if not isinstance(dt, datetime) or dt.tzinfo is None or dt.utcoffset() is None:
        raise CronError('datetimes must be timezone-aware')
    return dt.astimezone(UTC)


def _day_ok(d, spec):
    """Vixie cron: if either day field starts with '*', a day must match both; otherwise either one."""
    (_, _, doms, _, dows), dom_star, dow_star = spec
    dom_ok = d.day in doms
    dow_ok = (d.weekday() + 1) % 7 in dows
    if dom_star or dow_star:
        return dom_ok and dow_ok
    return dom_ok or dow_ok


def _possible(spec):
    """Only a day-of-month field that names impossible dates (with day of week starting with '*') never fires."""
    (_, _, doms, months, _), dom_star, dow_star = spec
    if dom_star or not dow_star:
        return True
    return any(d <= (29 if m == 2 else calendar.monthrange(2001, m)[1]) for m in months for d in doms)


def _walls(spec, start):
    """Matching naive wall times >= start (whole minutes), in order."""
    (mins, hours, _, months, _), _, _ = spec
    mins, hours, months = sorted(mins), sorted(hours), sorted(months)
    d = start.date()
    first = True
    limit = d + timedelta(days=366 * 60)
    while d < limit:
        if d.month not in months:
            nxt = [m for m in months if m > d.month]
            d = date(d.year, nxt[0], 1) if nxt else date(d.year + 1, months[0], 1)
            first = False
            continue
        if _day_ok(d, spec):
            for h in hours:
                if first and h < start.hour:
                    continue
                for m in mins:
                    if first and h == start.hour and m < start.minute:
                        continue
                    yield datetime(d.year, d.month, d.day, h, m)
        first = False
        d += timedelta(days=1)


def _local(t, z):
    return t.astimezone(z).replace(tzinfo=None)


def _max_wall(after, z):
    """The latest wall time the clock has shown up to the instant `after` (naive)."""
    now = _local(after, z)
    before = after - timedelta(hours=26)
    off_b, off_a = before.astimezone(z).utcoffset(), after.astimezone(z).utcoffset()
    if off_b <= off_a:
        return now
    lo, hi = before, after   # find the backward jump: offset still off_b at lo, already changed at hi
    while hi - lo > timedelta(seconds=1):
        mid = lo + (hi - lo) / 2
        if mid.astimezone(z).utcoffset() == off_b:
            lo = mid
        else:
            hi = mid
    return max(now, _local(lo, z))


def _instant(w, z):
    """The instant the clock first reaches or passes wall time w."""
    a = w.replace(tzinfo=z, fold=0).astimezone(UTC)
    b = w.replace(tzinfo=z, fold=1).astimezone(UTC)
    if a <= b:   # normal, or repeated (fold 0 is the first occurrence)
        return a
    # in a gap: the instant of the jump, between b and a
    off_before = (b - timedelta(seconds=1)).astimezone(z).utcoffset()
    lo, hi = b - timedelta(seconds=1), a
    while hi - lo > timedelta(seconds=1):
        mid = lo + (hi - lo) / 2
        if mid.astimezone(z).utcoffset() == off_before:
            lo = mid
        else:
            hi = mid
    return hi.replace(microsecond=0)


def _iter(expr, after, z):
    """Fire instants strictly after `after` (UTC), in order."""
    spec = parse(expr)
    if not _possible(spec):
        return
    m = _max_wall(after, z)
    start = (m + timedelta(minutes=1)).replace(second=0, microsecond=0)
    last = None
    for w in _walls(spec, start):
        t = _instant(w, z)
        if t <= after or t == last:
            continue
        last = t
        yield t


def next_fire(expr, after, tz):
    z = _zone(tz)
    after = _aware(after)
    for t in _iter(expr, after, z):
        return t.astimezone(z)
    return None


def fires_between(expr, start, end, tz):
    z = _zone(tz)
    start, end = _aware(start), _aware(end)
    out = []
    for t in _iter(expr, start - timedelta(microseconds=1), z):
        if t >= end:
            break
        out.append(t.astimezone(z))
    return out
