"""Formula tokens. A token is (kind, text, start) where kind is one of:
num, str, bool, ref, err, name, op, lparen, rparen, comma, colon.
The text of every token is kept exactly as written, so formulas can be rewritten token by token."""
import re


class FormulaError(Exception):
    """A formula that cannot be read (bad token or bad syntax)."""


TOKEN_RE = re.compile(r'''
    (?P<ws>\s+)
  | (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<err>\#REF!)
  | (?P<ref>\$?[A-Za-z]{1,3}\$?[0-9]+(?![A-Za-z0-9_(]))
  | (?P<name>[A-Za-z_][A-Za-z0-9_.]*)
  | (?P<op><>|<=|>=|[-+*/^&=<>%])
  | (?P<lparen>\()
  | (?P<rparen>\))
  | (?P<comma>,)
  | (?P<colon>:)
''', re.VERBOSE)


def tokenize(src):
    pos, out = 0, []
    while pos < len(src):
        m = TOKEN_RE.match(src, pos)
        if not m:
            raise FormulaError(f'unexpected character {src[pos]!r} at {pos}')
        kind = m.lastgroup
        text = m.group()
        if kind == 'name' and text.upper() in ('TRUE', 'FALSE'):
            kind = 'bool'
        if kind != 'ws':
            out.append((kind, text, pos))
        pos = m.end()
    return out


def untokenize(src, tokens, replace, glue=()):
    """src with some tokens replaced: `replace` maps a token index to its new text. For token indexes in
    `glue`, the whitespace before the token is dropped too (used to remove the rest of a range)."""
    out, pos = [], 0
    for i, (kind, text, start) in enumerate(tokens):
        if i in replace:
            if i not in glue:
                out.append(src[pos:start])
            out.append(replace[i])
            pos = start + len(text)
    out.append(src[pos:])
    return ''.join(out)
