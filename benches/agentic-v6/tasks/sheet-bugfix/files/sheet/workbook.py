"""The workbook: cell contents, recalculation, copying formulas, inserting and deleting rows."""
import re

from .evaluator import Evaluator
from .graph import Graph
from .parser import parse, references
from .refs import (cell_key, delete_rows_range, delete_rows_ref, insert_rows_ref, key_name, parse_ref, range_cells,
                   shift_ref)
from .tokenizer import FormulaError, tokenize, untokenize
from .values import CIRC

NUMBER_RE = re.compile(r'[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$')


class Cell:
    __slots__ = ('text', 'tree', 'value')

    def __init__(self, text, tree, value):
        self.text, self.tree, self.value = text, tree, value


def literal(text):
    """The value typed into a cell that is not a formula."""
    t = text.strip()
    if text.startswith("'"):
        return text[1:]
    if NUMBER_RE.match(t):
        f = float(t)
        return int(f) if re.match(r'[+-]?\d+$', t) else f
    if t.upper() in ('TRUE', 'FALSE'):
        return t.upper() == 'TRUE'
    return text


class Workbook:
    """
    wb.set('A1', '=B1*2')   # a formula; '42', '3.5', 'TRUE' are values; "'=x" is the text '=x'; '' empties the cell
    wb.get('A1')            # the current value: None (empty), number, text, bool or a CellError
    wb.formula('A1')        # what was typed ('' for an empty cell), as rewritten by copy/insert/delete
    wb.copy('A1', 'C3')     # copy what A1 holds to C3; relative references move by the offset
    wb.insert_rows(at, n)   # n empty rows before row `at`
    wb.delete_rows(at, n)   # remove rows at..at+n-1
    """

    def __init__(self):
        self.cells = {}
        self.graph = Graph()

    # ---- reading
    def get(self, name):
        c = self.cells.get(cell_key(name))
        return None if c is None else c.value

    def formula(self, name):
        c = self.cells.get(cell_key(name))
        return '' if c is None else c.text

    def contents(self):
        return {key_name(k): c.text for k, c in sorted(self.cells.items(), key=lambda kv: (kv[0][1], kv[0][0]))}

    def _value(self, key):
        c = self.cells.get(key)
        return None if c is None else c.value

    # ---- writing
    def set(self, name, text):
        key = cell_key(name)
        self._store(key, text)
        self._recalc({key})

    def _store(self, key, text):
        """Put text in a cell and update the graph (no recalculation)."""
        if text is None or text == '':
            self.cells.pop(key, None)
            self.graph.set_precedents(key, ())
            return
        if text.startswith('='):
            tree = parse(text[1:])          # FormulaError leaves the cell as it was
            refs, ranges = references(tree)
            keys = [r.key() for r in refs]
            for a, b in ranges:
                keys += range_cells(a, b)
            self.cells[key] = Cell(text, tree, None)
            self.graph.set_precedents(key, keys, [r.key() for r in refs])
        else:
            self.cells[key] = Cell(text, None, literal(text))
            self.graph.set_precedents(key, ())

    def _recalc(self, starts):
        ev = Evaluator(self._value)
        for group, cyclic in self.graph.plan(starts):
            if cyclic:
                for k in group:
                    if k in self.cells:
                        self.cells[k].value = CIRC
                continue
            (k,) = group
            c = self.cells.get(k)
            if c is None or c.tree is None:
                continue
            v = ev.evaluate(c.tree)
            c.value = 0 if v is None else v

    def _recalc_all(self):
        self.graph.clear()
        texts = {k: c.text for k, c in self.cells.items()}
        self.cells = {}
        for k, t in texts.items():
            self._store(k, t)
        self._recalc(set(self.cells))

    # ---- rewriting formulas
    @staticmethod
    def _rewrite(text, ref_fn, range_fn):
        """Formula text with each single reference replaced by ref_fn(Ref) and each range by range_fn(Ref, Ref);
        a None result becomes #REF!."""
        if not text.startswith('='):
            return text
        src = text[1:]
        toks = tokenize(src)
        repl, glue = {}, set()
        i = 0
        while i < len(toks):
            kind = toks[i][0]
            if kind == 'ref' and i + 2 < len(toks) and toks[i + 1][0] == 'colon' and toks[i + 2][0] == 'ref':
                a, b = parse_ref(toks[i][1]), parse_ref(toks[i + 2][1])
                res = range_fn(a, b)
                if res is None:   # the whole range text, from its first reference to its last, becomes #REF!
                    repl[i], repl[i + 1], repl[i + 2] = '#REF!', '', ''
                    glue |= {i + 1, i + 2}
                else:
                    repl[i], repl[i + 2] = res[0].text(), res[1].text()
                i += 3
                continue
            if kind == 'ref':
                r = ref_fn(parse_ref(toks[i][1]))
                repl[i] = '#REF!' if r is None else r.text()
            i += 1
        return '=' + untokenize(src, toks, repl, glue)

    def copy(self, src, dst):
        (sc, sr), (dc, dr) = cell_key(src), cell_key(dst)
        c = self.cells.get((sc, sr))
        text = '' if c is None else c.text

        def move(r):
            return shift_ref(r, dc - sc, dr - sr)

        def move_range(a, b):
            ma, mb = move(a), move(b)
            return None if ma is None or mb is None else (ma, mb)
        self.set(dst, self._rewrite(text, move, move_range))

    def insert_rows(self, at, n=1):
        if at < 1 or n < 1:
            raise ValueError('bad row arguments')
        moved = {}
        for (col, row), c in self.cells.items():
            moved[(col, row + n if row >= at else row)] = c
        for c in moved.values():
            c.text = self._rewrite(c.text, lambda r: insert_rows_ref(r, at, n),
                                   lambda a, b: (insert_rows_ref(a, at, n), insert_rows_ref(b, at, n)))
        self.cells = moved
        self._recalc_all()

    def delete_rows(self, at, n=1):
        if at < 1 or n < 1:
            raise ValueError('bad row arguments')
        moved = {}
        for (col, row), c in self.cells.items():
            if at <= row < at + n:
                continue
            moved[(col, row - n if row >= at + n else row)] = c
        for c in moved.values():
            c.text = self._rewrite(c.text, lambda r: delete_rows_ref(r, at, n), lambda a, b: delete_rows_range(a, b, at, n))
        self.cells = moved
        self._recalc_all()
