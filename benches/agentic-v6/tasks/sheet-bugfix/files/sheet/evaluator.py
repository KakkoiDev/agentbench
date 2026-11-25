"""Evaluates a formula tree against the current values of the other cells."""
from . import functions
from .refs import range_cells
from .values import DIV0, NUM, VALUE, CellError, Propagate, compare, to_number, to_text


class Evaluator:
    def __init__(self, lookup):
        self.lookup = lookup          # (col, row) -> current value of that cell

    def evaluate(self, node):
        try:
            return self.value(node)
        except Propagate as p:
            return p.error

    def range_values(self, node):
        return [self.lookup(k) for k in range_cells(node[1], node[2])]

    def value(self, node):
        kind = node[0]
        if kind in ('num', 'str', 'bool'):
            return node[1]
        if kind == 'err':
            return CellError(node[1])
        if kind == 'ref':
            v = self.lookup(node[1].key())
            if isinstance(v, CellError):
                raise Propagate(v)
            return v
        if kind == 'range':
            raise Propagate(VALUE)        # a range is only allowed as a function argument
        if kind == 'neg':
            return -to_number(self.value(node[1]))
        if kind == 'pct':
            return to_number(self.value(node[1])) / 100
        if kind == 'call':
            v = functions.call(self, node[1], node[2])
            if isinstance(v, CellError):
                raise Propagate(v)
            return v
        op, a, b = node[1], node[2], node[3]
        if op == '&':
            return to_text(self.value(a)) + to_text(self.value(b))
        if op in ('=', '<>', '<', '<=', '>', '>='):
            return compare(op, self.value(a), self.value(b))
        x, y = to_number(self.value(a)), to_number(self.value(b))
        if op == '+':
            return x + y
        if op == '-':
            return x - y
        if op == '*':
            return x * y
        if op == '/':
            if y == 0:
                raise Propagate(DIV0)
            return x / y
        if op == '^':
            if x == 0 and y < 0:
                raise Propagate(DIV0)
            try:
                r = float(x) ** y
            except (OverflowError, ZeroDivisionError):
                raise Propagate(NUM)
            if isinstance(r, complex):
                raise Propagate(NUM)
            return r
        raise Propagate(VALUE)
