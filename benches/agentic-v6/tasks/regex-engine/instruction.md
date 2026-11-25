Write a regular expression engine in `rx.py` (Python 3 standard library only). It must find exactly the same matches as Python's `re` module (no flags) for the syntax below, and it must never take exponential time: matching must stay roughly proportional to len(pattern) x len(text), whatever the pattern. **Do not use `re`, `sre_*`, `_sre`, `fnmatch`, `glob`, `regex`, `importlib`, `__import__` or `sys.modules` anywhere in rx.py** (the grader checks the source), and do not use recursion whose depth grows with the text (texts of 100,000+ characters are used).

## API

- `search(pattern, text)` -> the leftmost match anywhere in `text`, or `None`.
- `fullmatch(pattern, text)` -> a match that covers the whole text, or `None` (the same match `re.fullmatch` picks).
- `find_all(pattern, text)` -> a list of all matches, the same matches, in the same order, as `re.finditer`.
- `compile(pattern)` -> an object whose `search(text)` and `fullmatch(text)` methods behave like the functions above (so a pattern can be compiled once and reused). Invalid patterns raise `RegexError` (a subclass of `Exception` defined in rx.py) from `compile`, `search`, `fullmatch` and `find_all`.

A match is the tuple `((start, end), groups)`: `groups` is a tuple with one entry per capturing group, numbered by the position of their opening parenthesis like `re`, each entry being `(start, end)` or `None` when the group did not take part in the match. With Python's `re`: `(m.span(), tuple(m.span(g) if m.span(g) != (-1, -1) else None for g in range(1, len(m.groups()) + 1)))`.

## Syntax

- Any character that is not one of `\ . ^ $ * + ? ( ) [ ] { } |` matches itself. `{` that does not start a valid counted repeat (below) and `}` are literal characters too, e.g. `a{x}`, `a{1,2` and `{` are literals.
- `.` matches any character except a newline `\n`.
- Escapes: `\d` = `[0-9]`, `\w` = `[A-Za-z0-9_]`, `\s` = `[ \t\n\r\f\v]` (ASCII only, all texts are ASCII), `\D \W \S` their complements; `\n \t \r \f \v`; a backslash followed by any character that is not an ASCII letter or digit matches that character (`\. \\ \* \[ \- \ ` ...). Any other escape (`\q`, `\1`, `\x41`, a backslash at the end of the pattern...) is a `RegexError`.
- Character classes `[...]` and negated classes `[^...]`: single characters, ranges `a-z`, the escapes above (also `\d \w \s \D \W \S` inside a class, so `[^\W]` is a word character), `\]` and `\\`. A `]` right after `[` or `[^` is a literal `]` (`[]a]`), a `-` first or last is literal (`[-a]`, `[a-]`). A range whose end is lower than its start (`[z-a]`) or whose end is a class escape (`[a-\d]`) is an error; so is an unterminated class (`[a`, `[]`, `[^]`). A negated class matches `\n` unless it lists it.
- Groups: `(...)` capturing, `(?:...)` non-capturing; any other `(?` syntax is an error. Unbalanced parentheses are errors.
- Alternation `|` (lowest precedence; empty alternatives are allowed, e.g. `a|`).
- Repeats: `*`, `+`, `?`, `{n}`, `{n,}`, `{n,m}`, `{,m}` (= `{0,m}`), `{,}` (= `{0,}`); each followed by an optional `?` that makes it lazy. `{n,m}` with n > m is an error. A repeat with nothing before it (at the start of the pattern or a group, right after `|`) is an error, as is a repeat of an anchor (`^*`, `\b+`) and a repeat directly after a repeat other than the single lazy `?` (`a**`, `a+*`, `a{2}{3}`, `a*?+`).
- Anchors (match the empty string): `^` and `\A` at the start of the text; `$` at the end of the text **or just before a newline that is the last character of the text** (as in `re`); `\Z` only at the very end; `\b` between a word character and a non-word character (or at the start/end of the text next to a word character); `\B` wherever `\b` does not match.

## Which match (the same as `re`)

- `search` returns the match that starts leftmost; among matches starting there, the one a backtracking matcher finds first: alternatives are tried left to right, greedy repeats try one more iteration before stopping, lazy repeats try stopping before one more iteration. So `a|ab` on `ab` matches `a`; `fullmatch('a|ab', 'ab')` matches `ab`.
- A group inside a repeat reports its span from the last iteration in which it matched; a group that matched in an earlier iteration keeps that span if later iterations did not go through it, e.g. `(?:(a)|b)+` on `ab` gives group 1 = (0, 1).
- `find_all` scans from position 0; after a match `(s, e)`, the next search starts at `e`; if that match was empty (`s == e`), the next match must not be the empty match at `e` (a non-empty match starting at `e`, or any match starting later, is fine). So `find_all('a*?', 'aa')` gives the spans (0,0), (0,1), (1,1), (1,2), (2,2).
- The tests never repeat a sub-pattern that can match the empty string (such as `(a*)*` or `(a|)+`), where Python's own behaviour has quirks.

Make sure `python3 -m unittest` passes (and test far more than it does: the grading tests compare your engine with `re` on thousands of random patterns and texts, and time pathological patterns such as `(a|aa)*b` on 30,000 `a`s).
