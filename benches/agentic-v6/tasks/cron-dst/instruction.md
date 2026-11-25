Write `cron.py`: cron expressions evaluated against the wall clock of a real time zone (Python 3 standard library only; use `zoneinfo` for time zones).

## API

- `next_fire(expr, after, tz)` -> the first fire time strictly after `after`, or `None` if the expression can never fire.
- `fires_between(expr, start, end, tz)` -> the list of all fire times `t` with `start <= t < end`, in order.
- `tz` is an IANA zone name (`"America/New_York"`). `after`, `start` and `end` are timezone-aware datetimes in any zone. Returned datetimes are aware datetimes in `ZoneInfo(tz)` (so they print with the local offset, and their `fold` is right for repeated wall times).
- Invalid expressions, unknown zones and naive datetimes raise `CronError` (an `Exception` subclass defined in cron.py).

## Expressions

Five fields separated by spaces or tabs (leading/trailing whitespace ignored): minute (0-59), hour (0-23), day of month (1-31), month (1-12 or `JAN`-`DEC`), day of week (0-7 where 0 and 7 are Sunday, or `SUN`-`SAT`). Names are case-insensitive and allowed only in their own field (also in ranges: `MON-FRI`). Each field is a comma-separated list of items:

- `*`: every value of the field (for day of week: 0-6).
- `n`: one value; `a-b`: a range with `a <= b` (no wrap-around; `5-7` in day of week is Fri, Sat, Sun).
- `*/s`, `a-b/s`: every s-th value of the whole range or of `a-b`, starting at its start (`*/15` in minutes = 0,15,30,45; `*/10` in day of month = 1,11,21,31). `a/s` means `a-MAX/s` with MAX = 59, 23, 31, 12, 7 for the five fields.
- `s` is a positive integer. Values out of range, `a > b`, a step of 0, empty items, unknown names, a wrong number of fields are errors.

Macros (lowercase): `@yearly` and `@annually` (`0 0 1 1 *`), `@monthly` (`0 0 1 * *`), `@weekly` (`0 0 * * 0`), `@daily` and `@midnight` (`0 0 * * *`), `@hourly` (`0 * * * *`). Any other `@...` is an error.

**Which days match** (the classic Vixie cron rule): if the day-of-month field or the day-of-week field starts with `*` (this includes `*/s`), a day must match both fields; otherwise (both fields restricted) a day matches if it matches either field. So `0 0 13 * 5` fires on every 13th and on every Friday, `0 0 */10 * 1` only on Mondays that are the 1st, 11th, 21st or 31st, and `0 0 31 2 1` fires on Mondays (in February). An expression that can never match (e.g. `0 0 30 2 *`, `0 0 31 4,6,9,11 *`) gives `None` from `next_fire` and `[]` from `fires_between`, quickly. Seconds are always 0.

## Time zones and daylight saving

The fields are matched against the **local wall-clock time** in `tz`. Think of the clock on the wall as time passes: **the job fires at each instant at which the wall clock first reaches or jumps over a wall time that matches.** Consequences:

- Spring forward (a gap): matching wall times that do not exist that day fire at the instant the clock jumps, once, however many matching times were skipped. In New York on 2025-03-09 the clock jumps from 01:59:59 EST to 03:00 EDT, so `30 2 * * *` fires at 03:00 EDT (07:00 UTC) that day, and `*/15 * * * *` fires once at 03:00 EDT for 02:00-02:45 and 03:00 together. This also applies to gaps of 30 minutes (Australia/Lord_Howe), gaps at midnight (America/Havana: `0 0 * * *` fires at 01:00 local on its spring-forward day) and a whole skipped day (Pacific/Apia skipped 2011-12-30: a job at noon every day fires at the jump, 2011-12-31 00:00 local).
- Fall back (repeated wall times): a wall time that occurs twice fires only the first time the clock reaches it. In New York on 2025-11-02 (01:59:59 EDT is followed by 01:00 EST), `30 1 * * *` fires at 01:30 EDT only; `*/30 * * * *` fires at 01:00 EDT, 01:30 EDT and then 02:00 EST. If `after` falls in the second occurrence (say 01:10 EST), the next fire of `30 1 * * *` is the next day, and the next fire of `0 2 * * *` is 02:00 EST.
- Zones with offsets that are not whole hours (Asia/Kolkata +05:30, America/St_Johns, Pacific/Chatham +12:45/+13:45) work the same way.

## Speed

`next_fire` must jump over months and days instead of trying every minute: the tests call it hundreds of times on expressions that fire rarely, for example `0 0 29 2 *` after 2096-03-01 is 2104-02-29 (2100 is not a leap year), within a few seconds in total. `fires_between` over a month of `* * * * *` (about 43,200 results) must also take only a few seconds.

Make sure `python3 -m unittest` passes. The grading tests compare your results with a slow minute-by-minute simulation of the rule above, around many daylight-saving changes in several zones.
