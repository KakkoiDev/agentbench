"""Built-in functions. Each gets the evaluator and its argument trees (so IF and IFERROR can be lazy).

Aggregates (SUM AVERAGE MIN MAX COUNT): a range argument contributes only its number cells (text, booleans
and empty cells are skipped, errors propagate); a direct argument is converted with to_number (except COUNT,
which counts direct arguments that are numbers). AVERAGE of nothing is #DIV/0!; MIN/MAX of nothing is 0.
"""
from decimal import ROUND_HALF_UP, Decimal

from .values import DIV0, VALUE, CellError, Propagate, is_number, to_bool, to_number, to_text


def _numbers(ev, args):
    out = []
    for a in args:
        if a[0] == 'range':
            for v in ev.range_values(a):
                if isinstance(v, CellError):
                    raise Propagate(v)
                if is_number(v):
                    out.append(v)
        else:
            out.append(to_number(ev.value(a)))
    return out


def f_sum(ev, args):
    return sum(_numbers(ev, args))


def f_average(ev, args):
    nums = _numbers(ev, args)
    if not nums:
        return DIV0
    return sum(nums) / len(nums)


def f_min(ev, args):
    nums = _numbers(ev, args)
    return min(nums) if nums else 0


def f_max(ev, args):
    nums = _numbers(ev, args)
    return max(nums) if nums else 0


def f_count(ev, args):
    n = 0
    for a in args:
        if a[0] == 'range':
            n += sum(1 for v in ev.range_values(a) if is_number(v))
        else:
            v = ev.value(a)
            n += 1 if is_number(v) else 0
    return n


def f_if(ev, args):
    cond = to_bool(ev.value(args[0]))
    if cond:
        return ev.value(args[1])
    return ev.value(args[2]) if len(args) > 2 else False


def f_iferror(ev, args):
    try:
        v = ev.value(args[0])
    except Propagate as p:
        v = p.error
    return ev.value(args[1]) if isinstance(v, CellError) else v


def f_round(ev, args):
    """ROUND(x, digits=0): half away from zero on the decimal digits as written (2.675 -> 2.68), like spreadsheets."""
    x = to_number(ev.value(args[0]))
    digits = to_number(ev.value(args[1])) if len(args) > 1 else 0
    digits = int(digits)
    d = Decimal(repr(x)) if isinstance(x, float) else Decimal(x)
    q = d.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    if digits > 0:
        return float(q)
    r = float(q)
    return int(r) if isinstance(x, int) or r.is_integer() else r


def f_abs(ev, args):
    return abs(to_number(ev.value(args[0])))


def f_concat(ev, args):
    parts = []
    for a in args:
        if a[0] == 'range':
            parts += [to_text(v) for v in ev.range_values(a)]
        else:
            parts.append(to_text(ev.value(a)))
    return ''.join(parts)


def f_and(ev, args):
    return all([to_bool(ev.value(a)) for a in args])


def f_or(ev, args):
    return any([to_bool(ev.value(a)) for a in args])


def f_not(ev, args):
    return not to_bool(ev.value(args[0]))


def f_len(ev, args):
    return len(to_text(ev.value(args[0])))


# name -> (function, min args, max args or None)
FUNCS = {
    'SUM': (f_sum, 1, None), 'AVERAGE': (f_average, 1, None), 'MIN': (f_min, 1, None), 'MAX': (f_max, 1, None),
    'COUNT': (f_count, 1, None), 'IF': (f_if, 2, 3), 'IFERROR': (f_iferror, 2, 2), 'ROUND': (f_round, 1, 2),
    'ABS': (f_abs, 1, 1), 'CONCAT': (f_concat, 1, None), 'AND': (f_and, 1, None), 'OR': (f_or, 1, None),
    'NOT': (f_not, 1, 1), 'LEN': (f_len, 1, 1),
}


def call(ev, name, args):
    from .values import NAME
    spec = FUNCS.get(name)
    if spec is None:
        return NAME
    fn, lo, hi = spec
    if len(args) < lo or (hi is not None and len(args) > hi):
        return VALUE
    return fn(ev, args)
