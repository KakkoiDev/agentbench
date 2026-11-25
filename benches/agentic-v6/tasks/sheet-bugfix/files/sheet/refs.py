"""Cell references: A1 notation, absolute markers ($), ranges, and how references move.

A reference is a `Ref(col, row, col_abs, row_abs)` with 1-based col/row. `A1` is col 1 row 1, `$B$3` is
col 2 row 3 with both parts absolute. Column letters go A..Z, AA..AZ, BA.. (like spreadsheets).
"""
import re
from dataclasses import dataclass

REF_RE = re.compile(r'(\$?)([A-Za-z]{1,3})(\$?)([0-9]+)$')


@dataclass(frozen=True)
class Ref:
    col: int
    row: int
    col_abs: bool = False
    row_abs: bool = False

    def key(self):
        """The cell this reference points to, without the $ markers: (col, row)."""
        return (self.col, self.row)

    def text(self):
        return ('$' if self.col_abs else '') + col_name(self.col) + ('$' if self.row_abs else '') + str(self.row)


def col_name(n):
    s = ''
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def col_number(letters):
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def parse_ref(text):
    """'B3' / '$B$3' / 'b3' -> Ref; None if the text is not a reference."""
    m = REF_RE.match(text)
    if not m or int(m.group(4)) == 0:
        return None
    return Ref(col_number(m.group(2)), int(m.group(4)), m.group(1) == '$', m.group(3) == '$')


def cell_key(name):
    """'b3' -> (2, 3); raises ValueError for anything that is not a plain cell name."""
    r = parse_ref(name.strip())
    if r is None or r.col_abs or r.row_abs:
        raise ValueError(f'not a cell name: {name!r}')
    return r.key()


def key_name(key):
    return col_name(key[0]) + str(key[1])


def range_cells(a, b):
    """All (col, row) keys of the rectangle between two references, row by row."""
    c1, c2 = sorted((a.col, b.col))
    r1, r2 = sorted((a.row, b.row))
    return [(c, r) for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)]


# ---- moving references -------------------------------------------------------------------------
def shift_ref(ref, dcol, drow):
    """The reference as it reads after copying a formula dcol columns right and drow rows down.
    Relative parts move, absolute ($) parts stay. None if it would leave the sheet (#REF!)."""
    col = ref.col if ref.row_abs else ref.col + dcol
    row = ref.row if ref.col_abs else ref.row + drow
    if col < 1 or row < 1:
        return None
    return Ref(col, row, ref.col_abs, ref.row_abs)


def insert_rows_ref(ref, at, n):
    """Rows at and below `at` move down by n (absolute or not: the sheet itself changed)."""
    if ref.row >= at:
        return Ref(ref.col, ref.row + n, ref.col_abs, ref.row_abs)
    return ref


def delete_rows_ref(ref, at, n):
    """Rows at..at+n-1 are removed; rows below move up."""
    if ref.row > at + n:
        return Ref(ref.col, ref.row - n, ref.col_abs, ref.row_abs)
    return ref


def delete_rows_range(a, b, at, n):
    """A range after deleting rows at..at+n-1."""
    return delete_rows_ref(a, at, n), delete_rows_ref(b, at, n)
