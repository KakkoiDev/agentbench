"""Cron expressions evaluated against the wall clock of a real time zone."""
from zoneinfo import ZoneInfo


class CronError(Exception):
    pass


def next_fire(expr, after, tz):
    raise NotImplementedError


def fires_between(expr, start, end, tz):
    raise NotImplementedError
