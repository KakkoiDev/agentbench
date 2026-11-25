"""Cell values and the coercions shared by operators and functions.

A value is None (empty), an int or float, a str, a bool, or a CellError. Errors propagate: an operator or
function that receives an error returns it (the first one, left to right) unless documented otherwise.
"""


class CellError:
    """An error value such as #DIV/0!. Two errors are equal when their codes are equal."""
    __slots__ = ('code',)

    def __init__(self, code):
        self.code = code

    def __eq__(self, other):
        return isinstance(other, CellError) and other.code == self.code

    def __hash__(self):
        return hash(self.code)

    def __repr__(self):
        return f'CellError({self.code!r})'

    def __str__(self):
        return self.code


DIV0 = CellError('#DIV/0!')
VALUE = CellError('#VALUE!')
REF = CellError('#REF!')
NAME = CellError('#NAME?')
NUM = CellError('#NUM!')
CIRC = CellError('#CIRC!')


class Propagate(Exception):
    """Raised inside evaluation to return an error value early."""

    def __init__(self, error):
        self.error = error


def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def to_number(v):
    """For arithmetic: empty -> 0, bools -> 1/0, numeric text -> number, other text -> #VALUE!."""
    if isinstance(v, CellError):
        raise Propagate(v)
    if v is None:
        return 0
    if isinstance(v, bool):
        return int(v)
    if is_number(v):
        return v
    if isinstance(v, str):
        try:
            f = float(v.strip())
        except ValueError:
            raise Propagate(VALUE)
        return int(f) if f.is_integer() and v.strip().lstrip('+-').isdigit() else f
    raise Propagate(VALUE)


def to_text(v):
    """For &: empty -> '', TRUE/FALSE, whole floats without '.0' (2.0 -> '2'), other numbers with repr."""
    if isinstance(v, CellError):
        raise Propagate(v)
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'TRUE' if v else 'FALSE'
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    return str(v)


def to_bool(v):
    """For IF/AND/OR/NOT: numbers are TRUE unless 0, empty is FALSE, text is #VALUE! (except 'TRUE'/'FALSE')."""
    if isinstance(v, CellError):
        raise Propagate(v)
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if is_number(v):
        return v != 0
    if isinstance(v, str) and v.upper() in ('TRUE', 'FALSE'):
        return v.upper() == 'TRUE'
    raise Propagate(VALUE)


def _rank(v):
    # numbers < text < booleans, like common spreadsheets; empty compares as the other side's empty value
    if isinstance(v, bool):
        return 2
    if isinstance(v, str):
        return 1
    return 0


def compare(op, a, b):
    if isinstance(a, CellError):
        raise Propagate(a)
    if isinstance(b, CellError):
        raise Propagate(b)
    if a is None:
        a = '' if isinstance(b, str) else (False if isinstance(b, bool) else 0)
    if b is None:
        b = '' if isinstance(a, str) else (False if isinstance(a, bool) else 0)
    ra, rb = _rank(a), _rank(b)
    if ra != rb:
        x, y = ra, rb
    elif ra == 1:
        x, y = a.lower(), b.lower()     # text compares case-insensitively
    else:
        x, y = a, b
    return {'=': x == y, '<>': x != y, '<': x < y, '<=': x <= y, '>': x > y, '>=': x >= y}[op]
