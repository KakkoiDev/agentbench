"""A parser for a subset of TOML 1.0 that reports the first error with its kind, line and column."""
import re


class TOMLError(Exception):
    def __init__(self, kind, line, col):
        super().__init__(f'{kind} error at line {line}, column {col}')
        self.kind, self.line, self.col = kind, line, col


BARE = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-')
WS = ' \t'
TOKEN_STOP = set(' \t\n,]}#')
_D = r'[0-9](?:_?[0-9])*'
DEC = re.compile(r'[+-]?(?:0|[1-9](?:_?[0-9])*)\Z')
HEX = re.compile(r'0x[0-9A-Fa-f](?:_?[0-9A-Fa-f])*\Z')
OCT = re.compile(r'0o[0-7](?:_?[0-7])*\Z')
BIN = re.compile(r'0b[01](?:_?[01])*\Z')
FLOAT = re.compile(r'[+-]?(?:0|[1-9](?:_?[0-9])*)(?:\.' + _D + r'(?:[eE][+-]?' + _D + r')?|[eE][+-]?' + _D + r')\Z')
SPECIAL = re.compile(r'[+-]?(?:inf|nan)\Z')
ESCAPES = {'b': '\b', 't': '\t', 'n': '\n', 'f': '\f', 'r': '\r', '"': '"', '\\': '\\'}


def _bad_char(c):
    o = ord(c)
    return (o < 0x20 and c != '\t') or o == 0x7f


class Parser:
    def __init__(self, text):
        self.s, self.i = text, 0
        self.root = {}
        self.explicit = set()     # paths (tuples of keys) of tables that a header can no longer define
        self.pending = set()      # tables made by dotted keys in the current section
        self.frozen = set()       # paths of inline tables / static arrays
        self.aot = set()          # paths of arrays of tables made by [[ ]]

    # ---- positions and errors
    def pos(self, i):
        line = self.s.count('\n', 0, i) + 1
        col = i - (self.s.rfind('\n', 0, i) + 1) + 1
        return line, col

    def err(self, kind, i):
        raise TOMLError(kind, *self.pos(i))

    def peek(self, k=0):
        j = self.i + k
        return self.s[j] if j < len(self.s) else ''

    def skip_ws(self):
        while self.peek() and self.peek() in WS:
            self.i += 1

    def skip_ws_nl_comments(self):
        while True:
            c = self.peek()
            if c and c in WS + '\n':
                self.i += 1
            elif c == '#':
                self.skip_comment()
            else:
                return

    def skip_comment(self):
        while self.peek() and self.peek() != '\n':
            self.i += 1

    def end_of_line(self):
        self.skip_ws()
        if self.peek() == '#':
            self.skip_comment()
        c = self.peek()
        if c == '\n':
            self.i += 1
        elif c:
            self.err('syntax', self.i)

    # ---- document
    def parse(self):
        header = ()
        while True:
            self.skip_ws()
            c = self.peek()
            if not c:
                return self.root
            if c == '\n':
                self.i += 1
            elif c == '#':
                self.skip_comment()
            elif c == '[':
                header = self.table_header()
                self.end_of_line()
            else:
                self.key_value(header)
                self.end_of_line()

    # ---- keys
    def key(self):
        """A dotted key; stops at the first character after it that is not whitespace (left unread)."""
        parts = []
        while True:
            self.skip_ws()
            c = self.peek()
            if c == '"':
                parts.append(self.basic_string())
            elif c == "'":
                parts.append(self.literal_string())
            elif c and c in BARE:
                j = self.i
                while self.peek() and self.peek() in BARE:
                    self.i += 1
                parts.append(self.s[j:self.i])
            else:
                self.err('syntax', self.i)
            self.skip_ws()
            if self.peek() == '.':
                self.i += 1
                continue
            return tuple(parts)

    # ---- tables
    def nest(self, path, start):
        """The table at path, created implicitly where missing; arrays of tables give their last table."""
        cur = self.root
        for k in path:
            if k not in cur:
                cur[k] = {}
            cur = cur[k]
            if isinstance(cur, list):
                if not cur or not isinstance(cur[-1], dict):
                    self.err('duplicate', start)
                cur = cur[-1]
            elif not isinstance(cur, dict):
                self.err('duplicate', start)
        return cur

    def is_frozen(self, path):
        return any(path[:n] in self.frozen for n in range(1, len(path) + 1))

    def table_header(self):
        start = self.i
        self.i += 1
        is_array = self.peek() == '['
        if is_array:
            self.i += 1
        path = self.key()
        if self.peek() != ']':
            self.err('syntax', self.i)
        self.i += 1
        if is_array:
            if self.peek() != ']':
                self.err('syntax', self.i)
            self.i += 1
        self.explicit |= self.pending
        self.pending = set()
        if self.is_frozen(path):
            self.err('duplicate', start)
        if is_array:
            parent = self.nest(path[:-1], start)
            last = path[-1]
            if last in parent:
                if path not in self.aot or not isinstance(parent[last], list):
                    self.err('duplicate', start)
            else:
                parent[last] = []
                self.aot.add(path)
            parent[last].append({})
            # a new table: forget what was defined inside the previous one
            def inside(p):
                return len(p) > len(path) and p[:len(path)] == path
            self.explicit = {p for p in self.explicit if not inside(p)}
            self.frozen = {p for p in self.frozen if not inside(p)}
            self.aot = {p for p in self.aot if not inside(p)}
            self.explicit.add(path)
        else:
            if path in self.explicit:
                self.err('duplicate', start)
            parent = self.nest(path[:-1], start)
            last = path[-1]
            if last in parent and not isinstance(parent[last], dict):
                self.err('duplicate', start)
            parent.setdefault(last, {})
            self.explicit.add(path)
        return path

    def key_value(self, header):
        start = self.i
        key = self.key()
        full = header + key
        for n in range(len(header) + 1, len(full)):
            if full[:n] in self.explicit:
                self.err('duplicate', start)
        if self.is_frozen(full[:-1]) or self.is_frozen(full):
            self.err('duplicate', start)
        parent = self.nest(full[:-1], start)
        if key[-1] in parent:
            self.err('duplicate', start)
        for n in range(len(header) + 1, len(full)):
            self.pending.add(full[:n])
        if self.peek() != '=':
            self.err('syntax', self.i)
        self.i += 1
        self.skip_ws()
        value = self.value()
        parent[key[-1]] = value
        if isinstance(value, (dict, list)):
            self.frozen.add(full)

    # ---- values
    def value(self):
        c = self.peek()
        if c == '"':
            if self.s.startswith('"""', self.i):
                return self.ml_basic_string()
            return self.basic_string()
        if c == "'":
            if self.s.startswith("'''", self.i):
                return self.ml_literal_string()
            return self.literal_string()
        if c == '[':
            return self.array()
        if c == '{':
            return self.inline_table()
        if not c or c in TOKEN_STOP:
            self.err('syntax', self.i)
        j = self.i
        while self.peek() and self.peek() not in TOKEN_STOP:
            self.i += 1
        tok = self.s[j:self.i]
        if tok == 'true':
            return True
        if tok == 'false':
            return False
        if DEC.match(tok):
            return int(tok.replace('_', ''))
        if HEX.match(tok):
            return int(tok[2:].replace('_', ''), 16)
        if OCT.match(tok):
            return int(tok[2:].replace('_', ''), 8)
        if BIN.match(tok):
            return int(tok[2:].replace('_', ''), 2)
        if FLOAT.match(tok) or SPECIAL.match(tok):
            return float(tok.replace('_', ''))
        self.err('value', j)

    def array(self):
        self.i += 1
        out = []
        while True:
            self.skip_ws_nl_comments()
            if self.peek() == ']':
                self.i += 1
                return out
            out.append(self.value())
            self.skip_ws_nl_comments()
            c = self.peek()
            if c == ',':
                self.i += 1
            elif c == ']':
                self.i += 1
                return out
            else:
                self.err('syntax', self.i)

    def inline_table(self):
        self.i += 1
        out = {}
        frozen = set()
        self.skip_ws()
        if self.peek() == '}':
            self.i += 1
            return out
        while True:
            self.skip_ws()
            start = self.i
            key = self.key()
            if any(key[:n] in frozen for n in range(1, len(key) + 1)):
                self.err('duplicate', start)
            cur = out
            for k in key[:-1]:
                cur = cur.setdefault(k, {})
                if not isinstance(cur, dict):
                    self.err('duplicate', start)
            if key[-1] in cur:
                self.err('duplicate', start)
            if self.peek() != '=':
                self.err('syntax', self.i)
            self.i += 1
            self.skip_ws()
            v = self.value()
            cur[key[-1]] = v
            if isinstance(v, (dict, list)):
                frozen.add(key)
            self.skip_ws()
            c = self.peek()
            if c == '}':
                self.i += 1
                return out
            if c != ',':
                self.err('syntax', self.i)
            self.i += 1

    # ---- strings
    def escape(self):
        start = self.i
        self.i += 1
        c = self.peek()
        if c in ESCAPES and c:
            self.i += 1
            return ESCAPES[c]
        if c in ('u', 'U'):
            n = 4 if c == 'u' else 8
            digits = self.s[self.i + 1:self.i + 1 + n]
            if len(digits) != n or any(d not in '0123456789abcdefABCDEF' for d in digits):
                self.err('escape', start)
            cp = int(digits, 16)
            if cp > 0x10FFFF or 0xD800 <= cp <= 0xDFFF:
                self.err('escape', start)
            self.i += 1 + n
            return chr(cp)
        self.err('escape', start)

    def basic_string(self):
        start = self.i
        self.i += 1
        out = []
        while True:
            c = self.peek()
            if not c or c == '\n':
                self.err('string', start)
            if c == '"':
                self.i += 1
                return ''.join(out)
            if c == '\\':
                out.append(self.escape())
            elif _bad_char(c):
                self.err('string', self.i)
            else:
                out.append(c)
                self.i += 1

    def literal_string(self):
        start = self.i
        self.i += 1
        j = self.i
        while True:
            c = self.peek()
            if not c or c == '\n':
                self.err('string', start)
            if c == "'":
                self.i += 1
                return self.s[j:self.i - 1]
            if _bad_char(c):
                self.err('string', self.i)
            self.i += 1

    def _ml_close(self, q):
        """At q*3: the string ends here; up to two more quotes belong to the content."""
        extra = 0
        while extra < 2 and self.s.startswith(q * 4, self.i + extra):
            extra += 1
        self.i += 3 + extra
        return q * extra

    def ml_basic_string(self):
        start = self.i
        self.i += 3
        if self.peek() == '\n':
            self.i += 1
        out = []
        while True:
            c = self.peek()
            if not c:
                self.err('string', start)
            if self.s.startswith('"""', self.i):
                out.append(self._ml_close('"'))
                return ''.join(out)
            if c == '\\':
                j = self.i + 1
                while j < len(self.s) and self.s[j] in WS:
                    j += 1
                if j < len(self.s) and self.s[j] == '\n':   # line-ending backslash
                    self.i = j
                    while self.peek() and self.peek() in WS + '\n':
                        self.i += 1
                    continue
                if j > self.i + 1:
                    self.err('escape', self.i)
                out.append(self.escape())
            elif c != '\n' and _bad_char(c):
                self.err('string', self.i)
            else:
                out.append(c)
                self.i += 1

    def ml_literal_string(self):
        start = self.i
        self.i += 3
        if self.peek() == '\n':
            self.i += 1
        out = []
        while True:
            c = self.peek()
            if not c:
                self.err('string', start)
            if self.s.startswith("'''", self.i):
                out.append(self._ml_close("'"))
                return ''.join(out)
            if c != '\n' and _bad_char(c):
                self.err('string', self.i)
            out.append(c)
            self.i += 1


def loads(text):
    return Parser(text).parse()
