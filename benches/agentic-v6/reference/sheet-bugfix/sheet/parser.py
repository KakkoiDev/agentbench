"""Formula parser: tokens -> tree.

Precedence, lowest first (like common spreadsheets):
    comparison  = <> < <= > >=      (at most one per level, left to right)
    &           text concatenation
    + -
    * /
    ^           left-associative: 2^3^2 = 64
    unary - +   binds tighter than ^:  -2^2 = 4
    %           postfix percent: 50% = 0.5
    primary     number, "text" ("" is a quote), TRUE/FALSE, #REF!, A1, $A$1, A1:B3, NAME(args), (expr)

Nodes: ('num', v) ('str', s) ('bool', b) ('err', code) ('ref', Ref) ('range', Ref, Ref)
       ('neg', x) ('pct', x) ('bin', op, a, b) ('call', NAME, [args])
"""
from .refs import parse_ref
from .tokenizer import FormulaError, tokenize

COMPARE = ('=', '<>', '<', '<=', '>', '>=')


class Parser:
    def __init__(self, src):
        self.toks = tokenize(src)
        self.i = 0

    def peek(self, kind=None, text=None):
        if self.i >= len(self.toks):
            return None
        t = self.toks[self.i]
        if kind is not None and t[0] != kind:
            return None
        if text is not None and t[1] != text:
            return None
        return t

    def take(self, kind=None, text=None):
        t = self.peek(kind, text)
        if t is None:
            got = self.toks[self.i][1] if self.i < len(self.toks) else 'end of formula'
            raise FormulaError(f'expected {text or kind}, got {got!r}')
        self.i += 1
        return t

    def parse(self):
        node = self.comparison()
        if self.i != len(self.toks):
            raise FormulaError(f'unexpected {self.toks[self.i][1]!r}')
        return node

    def comparison(self):
        a = self.concat()
        t = self.peek('op')
        if t and t[1] in COMPARE:
            self.i += 1
            a = ('bin', t[1], a, self.concat())
        return a

    def concat(self):
        a = self.additive()
        while self.peek('op', '&'):
            self.i += 1
            a = ('bin', '&', a, self.additive())
        return a

    def additive(self):
        a = self.term()
        while True:
            t = self.peek('op')
            if not t or t[1] not in '+-':
                return a
            self.i += 1
            a = ('bin', t[1], a, self.term())

    def term(self):
        a = self.power()
        while True:
            t = self.peek('op')
            if not t or t[1] not in '*/':
                return a
            self.i += 1
            a = ('bin', t[1], a, self.power())

    def power(self):
        a = self.unary()
        while self.peek('op', '^'):
            self.i += 1
            a = ('bin', '^', a, self.unary())
        return a

    def unary(self):
        t = self.peek('op')
        if t and t[1] in '+-':
            self.i += 1
            x = self.unary()
            return ('neg', x) if t[1] == '-' else x
        return self.postfix()

    def postfix(self):
        x = self.primary()
        while self.peek('op', '%'):
            self.i += 1
            x = ('pct', x)
        return x

    def primary(self):
        t = self.peek()
        if t is None:
            raise FormulaError('unexpected end of formula')
        kind, text, _ = t
        if kind == 'num':
            self.i += 1
            v = float(text)
            return ('num', int(v) if v.is_integer() and '.' not in text and 'e' not in text.lower() else v)
        if kind == 'str':
            self.i += 1
            return ('str', text[1:-1].replace('""', '"'))
        if kind == 'bool':
            self.i += 1
            return ('bool', text.upper() == 'TRUE')
        if kind == 'err':
            self.i += 1
            return ('err', '#REF!')
        if kind == 'ref':
            self.i += 1
            a = parse_ref(text)
            if a is None:
                raise FormulaError(f'bad reference {text!r}')
            if self.peek('colon'):
                self.i += 1
                bt = self.take('ref')
                b = parse_ref(bt[1])
                if b is None:
                    raise FormulaError(f'bad reference {bt[1]!r}')
                return ('range', a, b)
            return ('ref', a)
        if kind == 'name':
            self.i += 1
            self.take('lparen')
            args = []
            if not self.peek('rparen'):
                args.append(self.comparison())
                while self.peek('comma'):
                    self.i += 1
                    args.append(self.comparison())
            self.take('rparen')
            return ('call', text.upper(), args)
        if kind == 'lparen':
            self.i += 1
            x = self.comparison()
            self.take('rparen')
            return x
        raise FormulaError(f'unexpected {text!r}')


def parse(src):
    return Parser(src).parse()


def references(node, out=None):
    """(single refs, ranges) used by a tree: lists of Ref and of (Ref, Ref)."""
    if out is None:
        out = ([], [])
    kind = node[0]
    if kind == 'ref':
        out[0].append(node[1])
    elif kind == 'range':
        out[1].append((node[1], node[2]))
    elif kind in ('neg', 'pct'):
        references(node[1], out)
    elif kind == 'bin':
        references(node[2], out)
        references(node[3], out)
    elif kind == 'call':
        for a in node[2]:
            references(a, out)
    return out
