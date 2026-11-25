"""A small SQL SELECT engine over in-memory tables."""
import math


class SQLError(Exception):
    pass


KEYWORDS = {'SELECT', 'DISTINCT', 'FROM', 'WHERE', 'GROUP', 'BY', 'HAVING', 'ORDER', 'ASC', 'DESC', 'NULLS', 'FIRST', 'LAST',
            'LIMIT', 'OFFSET', 'AS', 'JOIN', 'INNER', 'LEFT', 'OUTER', 'CROSS', 'ON', 'AND', 'OR', 'NOT', 'IS', 'NULL', 'IN',
            'BETWEEN', 'LIKE', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'TRUE', 'FALSE'}
AGGREGATES = {'COUNT', 'SUM', 'AVG', 'MIN', 'MAX'}
SCALARS = {'COALESCE': (1, None), 'NULLIF': (2, 2), 'UPPER': (1, 1), 'LOWER': (1, 1), 'LENGTH': (1, 1), 'ABS': (1, 1)}


# ---- tokens -----------------------------------------------------------------------------------
def tokenize(sql):
    toks, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c.isspace():
            i += 1
        elif c.isalpha() or c == '_':
            j = i
            while j < n and (sql[j].isalnum() or sql[j] == '_'):
                j += 1
            word = sql[i:j]
            toks.append(('kw', word.upper(), i, j) if word.upper() in KEYWORDS else ('ident', word, i, j))
            i = j
        elif c.isdigit():
            j = i
            while j < n and sql[j].isdigit():
                j += 1
            if j < n and sql[j] == '.' and j + 1 < n and sql[j + 1].isdigit():
                j += 1
                while j < n and sql[j].isdigit():
                    j += 1
                toks.append(('num', float(sql[i:j]), i, j))
            else:
                toks.append(('num', int(sql[i:j]), i, j))
            if j < n and (sql[j].isalpha() or sql[j] == '_'):
                raise SQLError('bad number')
            i = j
        elif c == "'":
            j, out = i + 1, []
            while True:
                if j >= n:
                    raise SQLError('unterminated string')
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        out.append("'")
                        j += 2
                        continue
                    break
                out.append(sql[j])
                j += 1
            toks.append(('str', ''.join(out), i, j + 1))
            i = j + 1
        elif c == '"':
            j = sql.find('"', i + 1)
            if j < 0 or j == i + 1:
                raise SQLError('bad quoted identifier')
            toks.append(('ident', sql[i + 1:j], i, j + 1))
            i = j + 1
        else:
            for op in ('<>', '!=', '<=', '>=', '||'):
                if sql.startswith(op, i):
                    toks.append(('op', op, i, i + 2))
                    i += 2
                    break
            else:
                if c in '=<>+-*/%(),.;':
                    toks.append(('op', c, i, i + 1))
                    i += 1
                else:
                    raise SQLError(f'unexpected character {c!r}')
    toks.append(('end', None, n, n))
    return toks


# ---- AST --------------------------------------------------------------------------------------
# expressions are tuples: ('lit', v) ('col', table or None, name) ('bin', op, a, b) ('not', a) ('neg', a)
# ('isnull', a, negated) ('in', a, [items], negated) ('between', a, lo, hi, negated) ('like', a, pat, negated)
# ('case', operand or None, [(when, then)], else or None) ('func', NAME, [args], distinct) ('agg', NAME, arg or None, distinct)

class Parser:
    def __init__(self, sql):
        self.sql, self.toks, self.i = sql, tokenize(sql), 0

    def peek(self, k=0):
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def at(self, kind, value=None, k=0):
        t = self.peek(k)
        return t[0] == kind and (value is None or t[1] == value)

    def kw(self, *words):
        if self.at('kw') and self.peek()[1] in words:
            self.i += 1
            return True
        return False

    def op(self, *ops):
        if self.at('op') and self.peek()[1] in ops:
            self.i += 1
            return True
        return False

    def expect_kw(self, w):
        if not self.kw(w):
            raise SQLError(f'expected {w}')

    def expect_op(self, o):
        if not self.op(o):
            raise SQLError(f'expected {o!r}')

    def ident(self):
        t = self.peek()
        if t[0] != 'ident':
            raise SQLError('expected a name')
        self.i += 1
        return t[1]

    def query(self):
        self.expect_kw('SELECT')
        q = dict(distinct=self.kw('DISTINCT'), items=[], joins=[], where=None, group=[], having=None, order=[],
                 limit=None, offset=None)
        while True:
            q['items'].append(self.select_item())
            if not self.op(','):
                break
        self.expect_kw('FROM')
        q['from'] = self.table_ref()
        while True:
            if self.kw('CROSS'):
                self.expect_kw('JOIN')
                q['joins'].append(('cross', self.table_ref(), None))
            elif self.kw('LEFT'):
                self.kw('OUTER')
                self.expect_kw('JOIN')
                ref = self.table_ref()
                self.expect_kw('ON')
                q['joins'].append(('left', ref, self.expr()))
            elif self.at('kw', 'INNER') or self.at('kw', 'JOIN'):
                self.kw('INNER')
                self.expect_kw('JOIN')
                ref = self.table_ref()
                self.expect_kw('ON')
                q['joins'].append(('inner', ref, self.expr()))
            else:
                break
        if self.kw('WHERE'):
            q['where'] = self.expr()
        if self.kw('GROUP'):
            self.expect_kw('BY')
            q['group'].append(self.expr())
            while self.op(','):
                q['group'].append(self.expr())
        if self.kw('HAVING'):
            q['having'] = self.expr()
        if self.kw('ORDER'):
            self.expect_kw('BY')
            while True:
                start = self.i
                e = self.expr()
                bare = self.i == start + 1
                desc = False
                if self.kw('DESC'):
                    desc = True
                else:
                    self.kw('ASC')
                nulls_first = not desc
                if self.kw('NULLS'):
                    if self.kw('FIRST'):
                        nulls_first = True
                    else:
                        self.expect_kw('LAST')
                        nulls_first = False
                q['order'].append((e, desc, nulls_first, bare))
                if not self.op(','):
                    break
        if self.kw('LIMIT'):
            q['limit'] = self.int_literal()
            if self.kw('OFFSET'):
                q['offset'] = self.int_literal()
        self.op(';')
        if not self.at('end'):
            raise SQLError(f'unexpected {self.peek()[1]!r}')
        return q

    def int_literal(self):
        t = self.peek()
        if t[0] != 'num' or not isinstance(t[1], int):
            raise SQLError('expected an integer')
        self.i += 1
        return t[1]

    def table_ref(self):
        name = self.ident()
        alias = name
        if self.kw('AS'):
            alias = self.ident()
        elif self.at('ident'):
            alias = self.ident()
        return name, alias

    def select_item(self):
        if self.at('op', '*'):
            self.i += 1
            return ('star', None)
        if self.at('ident') and self.at('op', '.', 1) and self.at('op', '*', 2):
            t = self.ident()
            self.i += 2
            return ('star', t)
        start = self.peek()[2]
        e = self.expr()
        end = self.toks[self.i - 1][3]
        alias = None
        if self.kw('AS'):
            alias = self.ident()
        elif self.at('ident'):
            alias = self.ident()
        if alias is not None:
            name = alias
        elif e[0] == 'col':
            name = e[2]
        else:
            name = self.sql[start:end].strip()
        return ('expr', e, name, alias)

    # precedence: OR < AND < NOT < comparison < || < + - < * / % < unary
    def expr(self):
        a = self.and_expr()
        while self.kw('OR'):
            a = ('bin', 'OR', a, self.and_expr())
        return a

    def and_expr(self):
        a = self.not_expr()
        while self.kw('AND'):
            a = ('bin', 'AND', a, self.not_expr())
        return a

    def not_expr(self):
        if self.kw('NOT'):
            return ('not', self.not_expr())
        return self.comparison()

    def comparison(self):
        a = self.concat()
        if self.at('op') and self.peek()[1] in ('=', '<>', '!=', '<', '<=', '>', '>='):
            o = self.peek()[1]
            self.i += 1
            a = ('bin', '<>' if o == '!=' else o, a, self.concat())
        elif self.kw('IS'):
            neg = self.kw('NOT')
            self.expect_kw('NULL')
            a = ('isnull', a, neg)
        else:
            neg = False
            if self.at('kw', 'NOT') and self.peek(1)[0] == 'kw' and self.peek(1)[1] in ('IN', 'BETWEEN', 'LIKE'):
                self.i += 1
                neg = True
            if self.kw('IN'):
                self.expect_op('(')
                items = [self.expr()]
                while self.op(','):
                    items.append(self.expr())
                self.expect_op(')')
                a = ('in', a, items, neg)
            elif self.kw('BETWEEN'):
                lo = self.concat()
                self.expect_kw('AND')
                a = ('between', a, lo, self.concat(), neg)
            elif self.kw('LIKE'):
                a = ('like', a, self.concat(), neg)
            elif neg:
                raise SQLError('expected IN, BETWEEN or LIKE')
        if self.at('op') and self.peek()[1] in ('=', '<>', '!=', '<', '<=', '>', '>=') or self.at('kw', 'IS') \
                or self.at('kw', 'IN') or self.at('kw', 'BETWEEN') or self.at('kw', 'LIKE'):
            raise SQLError('comparisons cannot be chained')
        return a

    def concat(self):
        a = self.additive()
        while self.op('||'):
            a = ('bin', '||', a, self.additive())
        return a

    def additive(self):
        a = self.term()
        while self.at('op') and self.peek()[1] in '+-':
            o = self.peek()[1]
            self.i += 1
            a = ('bin', o, a, self.term())
        return a

    def term(self):
        a = self.unary()
        while self.at('op') and self.peek()[1] in ('*', '/', '%'):
            o = self.peek()[1]
            self.i += 1
            a = ('bin', o, a, self.unary())
        return a

    def unary(self):
        if self.op('-'):
            return ('neg', self.unary())
        if self.op('+'):
            return self.unary()
        return self.primary()

    def primary(self):
        t = self.peek()
        if t[0] == 'num' or t[0] == 'str':
            self.i += 1
            return ('lit', t[1])
        if self.kw('NULL'):
            return ('lit', None)
        if self.kw('TRUE'):
            return ('lit', True)
        if self.kw('FALSE'):
            return ('lit', False)
        if self.op('('):
            e = self.expr()
            self.expect_op(')')
            return e
        if self.kw('CASE'):
            operand = None
            if not self.at('kw', 'WHEN'):
                operand = self.expr()
            whens = []
            while self.kw('WHEN'):
                w = self.expr()
                self.expect_kw('THEN')
                whens.append((w, self.expr()))
            if not whens:
                raise SQLError('CASE needs WHEN')
            other = self.expr() if self.kw('ELSE') else None
            self.expect_kw('END')
            return ('case', operand, whens, other)
        if t[0] == 'ident':
            self.i += 1
            if self.at('op', '('):
                return self.call(t[1])
            if self.op('.'):
                return ('col', t[1], self.ident())
            return ('col', None, t[1])
        raise SQLError(f'unexpected {t[1]!r}')

    def call(self, name):
        up = name.upper()
        self.expect_op('(')
        if up in AGGREGATES:
            if up == 'COUNT' and self.op('*'):
                self.expect_op(')')
                return ('agg', 'COUNT', None, False)
            distinct = self.kw('DISTINCT')
            arg = self.expr()
            self.expect_op(')')
            return ('agg', up, arg, distinct)
        if up not in SCALARS:
            raise SQLError(f'unknown function {name}')
        args = []
        if not self.at('op', ')'):
            args.append(self.expr())
            while self.op(','):
                args.append(self.expr())
        self.expect_op(')')
        lo, hi = SCALARS[up]
        if len(args) < lo or (hi is not None and len(args) > hi):
            raise SQLError(f'wrong number of arguments to {name}')
        return ('func', up, args, False)


# ---- evaluation -------------------------------------------------------------------------------
def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_cmp(a, b):
    if _num(a) and _num(b) or isinstance(a, str) and isinstance(b, str) or isinstance(a, bool) and isinstance(b, bool):
        return
    raise SQLError(f'cannot compare {a!r} and {b!r}')


def _like(s, p):
    # dynamic programming over the pattern; % any run, _ one character
    m, n = len(s), len(p)
    cur = [True] + [False] * m
    for j in range(n):
        c = p[j]
        nxt = [False] * (m + 1)
        if c == '%':
            acc = False
            for i in range(m + 1):
                acc = acc or cur[i]
                nxt[i] = acc
        else:
            for i in range(m):
                if cur[i] and (c == '_' or s[i] == c):
                    nxt[i + 1] = True
        cur = nxt
    return cur[m]


def _arith(op, a, b):
    if a is None or b is None:
        return None
    if not (_num(a) and _num(b)):
        raise SQLError(f'bad operands for {op}')
    if op == '+':
        return a + b
    if op == '-':
        return a - b
    if op == '*':
        return a * b
    if b == 0:
        return None
    if op == '/':
        if isinstance(a, int) and isinstance(b, int):
            q = abs(a) // abs(b)
            return q if (a >= 0) == (b > 0) else -q
        return a / b
    if isinstance(a, int) and isinstance(b, int):
        r = abs(a) % abs(b)
        return r if a >= 0 else -r
    return math.fmod(a, b)


def _cmp(op, a, b):
    if a is None or b is None:
        return None
    _check_cmp(a, b)
    if op == '=':
        return a == b
    if op == '<>':
        return a != b
    if op == '<':
        return a < b
    if op == '<=':
        return a <= b
    if op == '>':
        return a > b
    return a >= b


def _and(a, b):
    if a is False or b is False:
        return False
    if a is None or b is None:
        return None
    return True


def _or(a, b):
    if a is True or b is True:
        return True
    if a is None or b is None:
        return None
    return False


def _not(a):
    return None if a is None else (not a)


def _text(v):
    if isinstance(v, str):
        return v
    if _num(v):
        return str(v)
    raise SQLError('|| needs text or numbers')


class Compiler:
    """Turns expressions into functions of a row tuple (or, for grouped queries, of a list of rows)."""

    def __init__(self, scope):
        self.scope = scope   # list of (alias, columns, offset)

    def resolve(self, table, name):
        hits = []
        for alias, cols, off in self.scope:
            if table is not None and alias != table:
                continue
            hits += [off + k for k, c in enumerate(cols) if c == name]
        if table is not None and not any(a == table for a, _, _ in self.scope):
            raise SQLError(f'unknown table {table}')
        if not hits:
            raise SQLError(f'unknown column {name}')
        if len(hits) > 1:
            raise SQLError(f'ambiguous column {name}')
        return hits[0]

    def row_fn(self, e):
        """A function of one row; aggregates are not allowed here."""
        return self._fn(e, None)

    def group_fn(self, e):
        """A function of a group (a list of rows): aggregates run over it, bare columns use its first row."""
        return self._fn(e, 'group')

    def _fn(self, e, mode):
        k = e[0]
        if k == 'lit':
            v = e[1]
            return lambda r: v
        if k == 'col':
            idx = self.resolve(e[1], e[2])
            if mode == 'group':
                return lambda g: g[0][idx] if g else None
            return lambda r: r[idx]
        if k == 'agg':
            if mode != 'group':
                raise SQLError('aggregate not allowed here')
            return self._agg(e)
        sub = [self._fn(x, mode) for x in _children(e)]
        if k == 'bin':
            op = e[1]
            fa, fb = sub
            if op == 'AND':
                return lambda r: _and(fa(r), fb(r))
            if op == 'OR':
                return lambda r: _or(fa(r), fb(r))
            if op == '||':
                def cat(r):
                    a, b = fa(r), fb(r)
                    return None if a is None or b is None else _text(a) + _text(b)
                return cat
            if op in ('+', '-', '*', '/', '%'):
                return lambda r: _arith(op, fa(r), fb(r))
            return lambda r: _cmp(op, fa(r), fb(r))
        if k == 'not':
            f = sub[0]
            return lambda r: _not(f(r))
        if k == 'neg':
            f = sub[0]

            def neg(r):
                v = f(r)
                if v is None:
                    return None
                if not _num(v):
                    raise SQLError('bad operand for -')
                return -v
            return neg
        if k == 'isnull':
            f, negated = sub[0], e[2]
            return lambda r: (f(r) is None) != negated
        if k == 'in':
            f, items, negated = sub[0], sub[1:], e[3]

            def isin(r):
                v = f(r)
                if v is None:
                    return None
                res, saw_null = False, False
                for it in items:
                    x = it(r)
                    if x is None:
                        saw_null = True
                    elif _cmp('=', v, x):
                        res = True
                        break
                if not res and saw_null:
                    res = None
                return _not(res) if negated else res
            return isin
        if k == 'between':
            f, lo, hi = sub
            negated = e[4]

            def between(r):
                v = f(r)
                res = _and(_cmp('>=', v, lo(r)), _cmp('<=', v, hi(r)))
                return _not(res) if negated else res
            return between
        if k == 'like':
            f, p = sub
            negated = e[3]

            def like(r):
                s, pat = f(r), p(r)
                if s is None or pat is None:
                    return None
                if not isinstance(s, str) or not isinstance(pat, str):
                    raise SQLError('LIKE needs text')
                res = _like(s, pat)
                return (not res) if negated else res
            return like
        if k == 'case':
            operand = e[1]
            fo = self._fn(operand, mode) if operand is not None else None
            whens = [(self._fn(w, mode), self._fn(t, mode)) for w, t in e[2]]
            fe = self._fn(e[3], mode) if e[3] is not None else (lambda r: None)

            def case(r):
                if fo is not None:
                    v = fo(r)
                    for w, t in whens:
                        if _cmp('=', v, w(r)) is True:
                            return t(r)
                else:
                    for w, t in whens:
                        if w(r) is True:
                            return t(r)
                return fe(r)
            return case
        if k == 'func':
            name = e[1]
            if name == 'COALESCE':
                def coalesce(r):
                    for f in sub:
                        v = f(r)
                        if v is not None:
                            return v
                    return None
                return coalesce
            if name == 'NULLIF':
                fa, fb = sub
                return lambda r: (lambda a, b: None if _cmp('=', a, b) is True else a)(fa(r), fb(r))
            f = sub[0]

            def scalar(r):
                v = f(r)
                if v is None:
                    return None
                if name in ('UPPER', 'LOWER', 'LENGTH'):
                    if not isinstance(v, str):
                        raise SQLError(f'{name} needs text')
                    return v.upper() if name == 'UPPER' else v.lower() if name == 'LOWER' else len(v)
                if not _num(v):
                    raise SQLError('ABS needs a number')
                return abs(v)
            return scalar
        raise SQLError(f'cannot evaluate {k}')

    def _agg(self, e):
        _, name, arg, distinct = e
        if arg is None:
            return lambda g: len(g)
        if _has_agg(arg):
            raise SQLError('nested aggregate')
        f = self.row_fn(arg)

        def agg(g):
            vals = [v for v in map(f, g) if v is not None]
            if distinct:
                seen, out = set(), []
                for v in vals:
                    if v not in seen:
                        seen.add(v)
                        out.append(v)
                vals = out
            if name == 'COUNT':
                return len(vals)
            if not vals:
                return None
            if name in ('SUM', 'AVG'):
                if not all(_num(v) for v in vals):
                    raise SQLError(f'{name} needs numbers')
                total = sum(vals)
                return total if name == 'SUM' else total / len(vals)
            for a in vals[1:]:
                _check_cmp(vals[0], a)
            return min(vals) if name == 'MIN' else max(vals)
        return agg


def _children(e):
    k = e[0]
    if k == 'bin':
        return [e[2], e[3]]
    if k in ('not', 'neg', 'isnull'):
        return [e[1]]
    if k == 'in':
        return [e[1]] + list(e[2])
    if k == 'between':
        return [e[1], e[2], e[3]]
    if k == 'like':
        return [e[1], e[2]]
    if k == 'func':
        return list(e[2])
    if k == 'case':
        out = [] if e[1] is None else [e[1]]
        for w, t in e[2]:
            out += [w, t]
        return out + ([] if e[3] is None else [e[3]])
    if k == 'agg':
        return [] if e[2] is None else [e[2]]
    return []


def _has_agg(e):
    return e[0] == 'agg' or any(_has_agg(c) for c in _children(e))


def _cols(e, out):
    if e[0] == 'col':
        out.append(e)
    for c in _children(e):
        _cols(c, out)
    return out


def _conjuncts(e):
    if e[0] == 'bin' and e[1] == 'AND':
        return _conjuncts(e[2]) + _conjuncts(e[3])
    return [e]


# ---- execution --------------------------------------------------------------------------------
def _sort_key_cmp(rows, keys):
    """Stable multi-key sort of (unit, row) pairs; keys: (column index in row, desc, nulls_first)."""
    for col, desc, nulls_first in reversed(keys):
        vals = [r[col] for _, r in rows if r[col] is not None]
        if vals:
            kinds = {'num' if _num(v) else 'str' if isinstance(v, str) else 'bool' for v in vals}
            if len(kinds) > 1:
                raise SQLError('cannot sort values of different types')
        # with reverse=True Python's sort is still stable; NULLs get a rank below or above every value
        null_rank = 0 if nulls_first != desc else 2
        rows = sorted(rows, key=lambda x: (null_rank,) if x[1][col] is None else (1, x[1][col]), reverse=desc)
    return rows


def execute(sql, tables):
    q = Parser(sql).query()
    refs = [q['from']] + [j[1] for j in q['joins']]
    scope, off = [], 0
    for name, alias in refs:
        if name not in tables:
            raise SQLError(f'unknown table {name}')
        if any(a == alias for a, _, _ in scope):
            raise SQLError(f'duplicate table name {alias}')
        cols = list(tables[name]['columns'])
        scope.append((alias, cols, off))
        off += len(cols)

    # FROM and JOINs, left to right
    first = tables[refs[0][0]]
    rows = [tuple(r) for r in first['rows']]
    for j, (kind, (name, alias), on) in enumerate(q['joins'], 1):
        right = [tuple(r) for r in tables[name]['rows']]
        width = len(scope[j][1])
        comp = Compiler(scope[:j + 1])
        if kind == 'cross':
            rows = [l + r for l in rows for r in right]
            continue
        if _has_agg(on):
            raise SQLError('aggregate not allowed in ON')
        full = comp.row_fn(on)
        # hash join on equalities between the new table and the tables before it
        left_scope = Compiler(scope[:j])
        right_scope = Compiler([(alias, scope[j][1], 0)])
        lkeys, rkeys, rest = [], [], []
        for c in _conjuncts(on):
            used = None
            if c[0] == 'bin' and c[1] == '=' and not _has_agg(c):
                for a, b in ((c[2], c[3]), (c[3], c[2])):
                    try:
                        fa, fb = left_scope.row_fn(a), right_scope.row_fn(b)
                        if not _cols(b, []) or not _side_ok(b, alias, scope[j][1], scope[:j]):
                            continue
                        used = (fa, fb)
                        break
                    except SQLError:
                        continue
            if used:
                lkeys.append(used[0])
                rkeys.append(used[1])
            else:
                rest.append(c)
        out = []
        if lkeys:
            index = {}
            for r in right:
                key = tuple(f(r) for f in rkeys)
                if any(v is None for v in key):
                    continue
                index.setdefault(key, []).append(r)
            restf = [comp.row_fn(c) for c in rest]
            nulls = (None,) * width
            for l in rows:
                key = tuple(f(l) for f in lkeys)
                matched = False
                if not any(v is None for v in key):
                    for r in index.get(key, ()):
                        row = l + r
                        if all(f(row) is True for f in restf):
                            out.append(row)
                            matched = True
                if kind == 'left' and not matched:
                    out.append(l + nulls)
        else:
            nulls = (None,) * width
            for l in rows:
                matched = False
                for r in right:
                    row = l + r
                    if full(row) is True:
                        out.append(row)
                        matched = True
                if kind == 'left' and not matched:
                    out.append(l + nulls)
        rows = out

    comp = Compiler(scope)
    if q['where'] is not None:
        if _has_agg(q['where']):
            raise SQLError('aggregate not allowed in WHERE')
        w = comp.row_fn(q['where'])
        rows = [r for r in rows if w(r) is True]

    # select list
    items = []   # (name, expression or None for star column index, alias)
    for it in q['items']:
        if it[0] == 'star':
            found = False
            for alias, cols, off in scope:
                if it[1] is None or alias == it[1]:
                    found = True
                    for k, c in enumerate(cols):
                        items.append((c, ('idx', off + k), None))
            if not found:
                raise SQLError(f'unknown table {it[1]}')
        else:
            items.append((it[2], it[1], it[3]))

    grouped = bool(q['group']) or q['having'] is not None or \
        any(e[0] != 'idx' and _has_agg(e) for _, e, _ in items) or any(_has_agg(o[0]) for o in q['order'])
    for g in q['group']:
        if _has_agg(g):
            raise SQLError('aggregate not allowed in GROUP BY')

    def item_fn(e, mode):
        if e[0] == 'idx':
            idx = e[1]
            if mode == 'group':
                return lambda g: g[0][idx] if g else None
            return lambda r: r[idx]
        return comp.group_fn(e) if mode == 'group' else comp.row_fn(e)

    mode = 'group' if grouped else 'row'
    if grouped:
        if q['group']:
            gfs = [comp.row_fn(g) for g in q['group']]
            groups = {}
            for r in rows:
                groups.setdefault(tuple(f(r) for f in gfs), []).append(r)
            units = list(groups.values())
        else:
            units = [rows]
        if q['having'] is not None:
            h = comp.group_fn(q['having'])
            units = [g for g in units if h(g) is True]
    else:
        units = rows
    fns = [item_fn(e, mode) for _, e, _ in items]
    names = [n for n, _, _ in items]
    out = [(u, tuple(f(u) for f in fns)) for u in units]

    if q['distinct']:
        seen, kept = set(), []
        for u, row in out:
            if row not in seen:
                seen.add(row)
                kept.append((u, row))
        out = kept

    if q['order']:
        keys = []
        extra = []
        for e, desc, nulls_first, bare in q['order']:
            pos = None
            if e[0] == 'lit' and isinstance(e[1], int) and not isinstance(e[1], bool) and bare:
                if not 1 <= e[1] <= len(items):
                    raise SQLError('ORDER BY position out of range')
                pos = e[1] - 1
            elif e[0] == 'col' and e[1] is None:
                hits = [k for k, (n, ex, alias) in enumerate(items)
                        if n == e[2] and (alias is not None or ex[0] in ('col', 'idx'))]
                if len(hits) > 1:
                    raise SQLError(f'ambiguous ORDER BY {e[2]}')
                if hits:
                    pos = hits[0]
            if pos is not None:
                keys.append((pos, desc, nulls_first))
            else:
                if q['distinct']:
                    raise SQLError('ORDER BY with DISTINCT must use output columns')
                if not grouped and _has_agg(e):
                    raise SQLError('aggregate not allowed here')
                extra.append(item_fn(e, mode))
                keys.append((len(items) + len(extra) - 1, desc, nulls_first))
        decorated = [(u, row + tuple(f(u) for f in extra)) for u, row in out]
        decorated = _sort_key_cmp(decorated, keys)
        out = [(u, row[:len(items)]) for u, row in decorated]

    result = [row for _, row in out]
    if q['offset']:
        result = result[q['offset']:]
    if q['limit'] is not None:
        result = result[:q['limit']]
    return names, result


def _side_ok(e, alias, cols, left_scope):
    """True if every column in e belongs to the new table (so the equality can drive a hash join)."""
    for c in _cols(e, []):
        if c[1] is not None:
            if c[1] != alias:
                return False
        elif any(c[2] in lcols for _, lcols, _ in left_scope):
            return False   # an unqualified name also visible on the left: let the general path decide
    return True
