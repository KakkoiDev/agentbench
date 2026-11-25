"""A regular expression engine with Python re's matching rules and no backtracking (a Pike VM)."""


class RegexError(Exception):
    pass


DIGIT = frozenset('0123456789')
WORD = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_')
SPACE = frozenset(' \t\n\r\f\v')
CLASS_ESC = {'d': (DIGIT, False), 'D': (DIGIT, True), 'w': (WORD, False), 'W': (WORD, True),
             's': (SPACE, False), 'S': (SPACE, True)}
CHAR_ESC = {'n': '\n', 't': '\t', 'r': '\r', 'f': '\f', 'v': '\v'}
ASSERT_ESC = {'b', 'B', 'A', 'Z'}


# ---- parser: pattern -> tree ------------------------------------------------------------------
# nodes: ('empty',) ('char', c) ('any',) ('set', ranges, sets, negated_sets, negated) ('cat', [n]) ('alt', [n])
#        ('group', index or None, n) ('repeat', n, min, max or None, greedy) ('assert', kind)

class _Parser:
    def __init__(self, p):
        self.p, self.i, self.ngroups = p, 0, 0

    def peek(self):
        return self.p[self.i] if self.i < len(self.p) else None

    def parse(self):
        node = self.alt()
        if self.i < len(self.p):
            raise RegexError(f'unbalanced parenthesis at {self.i}')
        return node

    def alt(self):
        branches = [self.cat()]
        while self.peek() == '|':
            self.i += 1
            branches.append(self.cat())
        return branches[0] if len(branches) == 1 else ('alt', branches)

    def cat(self):
        items = []
        while self.peek() is not None and self.peek() not in '|)':
            items.append(self.quantified())
        return ('cat', items) if items else ('empty',)

    def quantified(self):
        c = self.peek()
        if c in '*+?':
            raise RegexError(f'nothing to repeat at {self.i}')
        if c == '{' and self._brace() is not None:
            raise RegexError(f'nothing to repeat at {self.i}')
        atom = self.atom()
        q = self._quant()
        if q is None:
            return atom
        lo, hi = q
        greedy = True
        if self.peek() == '?':
            self.i += 1
            greedy = False
        if self.peek() is not None and (self.peek() in '*+?' or (self.peek() == '{' and self._brace() is not None)):
            raise RegexError(f'multiple repeat at {self.i}')
        if atom[0] == 'assert':
            raise RegexError('nothing to repeat')
        return ('repeat', atom, lo, hi, greedy)

    def _brace(self):
        """If a valid {n}, {n,}, {n,m} or {,m} starts here: (lo, hi, length); else None (a literal '{')."""
        p, j = self.p, self.i + 1
        k = j
        while k < len(p) and p[k].isdigit():
            k += 1
        lo_s = p[j:k]
        if k < len(p) and p[k] == '}':
            if not lo_s:
                return None
            n = int(lo_s)
            return n, n, k + 1 - self.i
        if k < len(p) and p[k] == ',':
            m = k + 1
            while m < len(p) and p[m].isdigit():
                m += 1
            hi_s = p[k + 1:m]
            if m < len(p) and p[m] == '}':
                lo = int(lo_s) if lo_s else 0
                hi = int(hi_s) if hi_s else None
                return lo, hi, m + 1 - self.i
        return None

    def _quant(self):
        c = self.peek()
        if c == '*':
            self.i += 1
            return 0, None
        if c == '+':
            self.i += 1
            return 1, None
        if c == '?':
            self.i += 1
            return 0, 1
        if c == '{':
            b = self._brace()
            if b is not None:
                lo, hi, n = b
                if hi is not None and lo > hi:
                    raise RegexError(f'min repeat greater than max repeat at {self.i}')
                self.i += n
                return lo, hi
        return None

    def atom(self):
        c = self.peek()
        if c == '(':
            start = self.i
            self.i += 1
            index = None
            if self.p.startswith('?:', self.i):
                self.i += 2
            elif self.peek() == '?':
                raise RegexError(f'unsupported group syntax at {start}')
            else:
                self.ngroups += 1
                index = self.ngroups
            node = self.alt()
            if self.peek() != ')':
                raise RegexError(f'missing ), unterminated subpattern at {start}')
            self.i += 1
            return ('group', index, node)
        if c == ')':
            raise RegexError(f'unbalanced parenthesis at {self.i}')
        if c == '[':
            return self.char_class()
        if c == '.':
            self.i += 1
            return ('any',)
        if c == '^':
            self.i += 1
            return ('assert', '^')
        if c == '$':
            self.i += 1
            return ('assert', '$')
        if c == '\\':
            return self.escape(in_class=False)
        self.i += 1
        return ('char', c)

    def escape(self, in_class):
        start = self.i
        self.i += 1
        if self.i >= len(self.p):
            raise RegexError(f'bad escape (end of pattern) at {start}')
        e = self.p[self.i]
        self.i += 1
        if e in CLASS_ESC:
            s, neg = CLASS_ESC[e]
            return ('set', (), (s,), (), neg) if not in_class else ('classesc', s, neg)
        if e in CHAR_ESC:
            return ('char', CHAR_ESC[e])
        if in_class and e == 'b':
            return ('char', '\b')
        if e in ASSERT_ESC and not in_class:
            return ('assert', e)
        if e.isascii() and e.isalnum():
            raise RegexError(f'bad escape \\{e} at {start}')
        return ('char', e)

    def char_class(self):
        start = self.i
        self.i += 1
        neg = False
        if self.peek() == '^':
            neg = True
            self.i += 1
        ranges, sets, negsets = [], [], []
        first = True
        while True:
            c = self.peek()
            if c is None:
                raise RegexError(f'unterminated character set at {start}')
            if c == ']' and not first:
                self.i += 1
                break
            first = False
            lo = self._class_item()
            if lo[0] == 'classesc':
                (negsets if lo[2] else sets).append(lo[1])
                continue
            if self.peek() == '-' and self.i + 1 < len(self.p) and self.p[self.i + 1] != ']':
                self.i += 1
                hi = self._class_item()
                if hi[0] == 'classesc':
                    raise RegexError(f'bad character range at {start}')
                if ord(hi[1]) < ord(lo[1]):
                    raise RegexError(f'bad character range {lo[1]}-{hi[1]} at {start}')
                ranges.append((lo[1], hi[1]))
            else:
                ranges.append((lo[1], lo[1]))
        return ('set', tuple(ranges), tuple(sets), tuple(negsets), neg)

    def _class_item(self):
        c = self.peek()
        if c == '\\':
            return self.escape(in_class=True)
        self.i += 1
        return ('char', c)


# ---- compiler: tree -> program ---------------------------------------------------------------
# instructions: ('char', c) ('any',) ('set', fn) ('split', a, b) ('jmp', a) ('save', k) ('assert', kind) ('match',)

def _set_fn(node):
    _, ranges, pos_sets, neg_sets, neg = node

    def fn(ch):
        r = any(lo <= ch <= hi for lo, hi in ranges) or any(ch in s for s in pos_sets) or \
            any(ch not in s for s in neg_sets)
        return r != neg
    return fn


class _Compiler:
    def __init__(self):
        self.prog = []

    def emit(self, ins):
        self.prog.append(ins)
        return len(self.prog) - 1

    def gen(self, n):
        kind = n[0]
        if kind == 'empty':
            return
        if kind == 'char':
            self.emit(('char', n[1]))
        elif kind == 'any':
            self.emit(('any',))
        elif kind == 'set':
            self.emit(('set', _set_fn(n)))
        elif kind == 'assert':
            self.emit(('assert', n[1]))
        elif kind == 'cat':
            for x in n[1]:
                self.gen(x)
        elif kind == 'alt':
            jumps = []
            branches = n[1]
            for b in branches[:-1]:
                split = self.emit(None)
                self.gen(b)
                jumps.append(self.emit(None))
                self.prog[split] = ('split', split + 1, len(self.prog))
            self.gen(branches[-1])
            for j in jumps:
                self.prog[j] = ('jmp', len(self.prog))
        elif kind == 'group':
            if n[1] is None:
                self.gen(n[2])
            else:
                self.emit(('save', 2 * n[1]))
                self.gen(n[2])
                self.emit(('save', 2 * n[1] + 1))
        elif kind == 'repeat':
            _, body, lo, hi, greedy = n
            for _ in range(lo):
                self.gen(body)
            if hi is None:
                start = self.emit(None)
                self.gen(body)
                self.emit(('jmp', start))
                self.prog[start] = ('split', start + 1, len(self.prog)) if greedy else ('split', len(self.prog), start + 1)
            else:
                self._optional_chain(body, hi - lo, greedy)

    def _optional_chain(self, body, k, greedy):
        """(body(body(...)?)?)? with k bodies, the way a counted repeat keeps going one more time."""
        splits = []
        for _ in range(k):
            splits.append(self.emit(None))
            self.gen(body)
        end = len(self.prog)
        for s in splits:
            self.prog[s] = ('split', s + 1, end) if greedy else ('split', end, s + 1)


class Pattern:
    def __init__(self, pattern):
        parser = _Parser(pattern)
        tree = parser.parse()
        self.groups = parser.ngroups
        c = _Compiler()
        c.emit(('save', 0))
        c.gen(tree)
        c.emit(('save', 1))
        c.emit(('match',))
        self.prog = c.prog

    def _run(self, text, pos, anchored, full, forbid_empty_at):
        prog, n = self.prog, len(text)
        nslots = 2 * self.groups + 2

        def at_assert(kind, i):
            if kind == '^' or kind == 'A':
                return i == 0
            if kind == '$':
                return i == n or (i == n - 1 and text[i] == '\n')
            if kind == 'Z':
                return i == n
            before = i > 0 and text[i - 1] in WORD
            after = i < n and text[i] in WORD
            return (before != after) if kind == 'b' else (before == after)

        def add(lst, seen, pc0, caps0, i):
            stack = [(pc0, caps0)]
            while stack:
                pc, caps = stack.pop()
                if pc in seen:
                    continue
                seen.add(pc)
                ins = prog[pc]
                op = ins[0]
                if op == 'jmp':
                    stack.append((ins[1], caps))
                elif op == 'split':
                    stack.append((ins[2], caps))
                    stack.append((ins[1], caps))
                elif op == 'save':
                    k = ins[1]
                    stack.append((pc + 1, caps[:k] + (i,) + caps[k + 1:]))
                elif op == 'assert':
                    if at_assert(ins[1], i):
                        stack.append((pc + 1, caps))
                else:
                    lst.append((pc, caps))

        empty = (None,) * nslots
        clist, matched = [], None
        i = pos
        seen = set()
        add(clist, seen, 0, empty, i)
        while True:
            nlist, nseen = [], set()
            for pc, caps in clist:
                ins = prog[pc]
                op = ins[0]
                if op == 'match':
                    if full and i != n:
                        continue
                    if forbid_empty_at is not None and caps[0] == i == forbid_empty_at:
                        continue
                    matched = caps
                    break  # lower-priority threads lose
                if i < n:
                    ch = text[i]
                    if (op == 'char' and ch == ins[1]) or (op == 'any' and ch != '\n') or (op == 'set' and ins[1](ch)):
                        add(nlist, nseen, pc + 1, caps, i + 1)
            if i >= n:
                break
            i += 1
            if matched is None and not anchored:
                add(nlist, nseen, 0, empty, i)
            clist = nlist
            if not clist and (matched is not None or anchored):
                break
        if matched is None:
            return None
        spans = tuple((matched[2 * g], matched[2 * g + 1]) if matched[2 * g + 1] is not None else None
                      for g in range(1, self.groups + 1))
        return (matched[0], matched[1]), spans

    def search(self, text, pos=0, forbid_empty_at=None):
        return self._run(text, pos, False, False, forbid_empty_at)

    def fullmatch(self, text):
        return self._run(text, 0, True, True, None)


def compile(pattern):
    return Pattern(pattern)


def search(pattern, text):
    return Pattern(pattern).search(text)


def fullmatch(pattern, text):
    return Pattern(pattern).fullmatch(text)


def find_all(pattern, text):
    p = Pattern(pattern)
    out, pos, forbid = [], 0, None
    while pos <= len(text):
        m = p.search(text, pos, forbid)
        if m is None:
            break
        out.append(m)
        (s, e), _ = m
        pos, forbid = e, (e if s == e else None)
    return out
