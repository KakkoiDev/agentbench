Write `toml_subset.py`: a parser for TOML 1.0 without dates and times, with exact error reports. Python 3 standard library only, and **do not use `tomllib` or any other TOML library** (the grader blocks and checks for them).

## API

- `loads(text) -> dict` parses a document (a `str`; lines end with `\n` only, never `\r`).
- Invalid documents raise `TOMLError(kind, line, col)`, an `Exception` subclass defined in `toml_subset.py` with attributes `kind`, `line` and `col`. `line` and `col` are 1-based; `col` counts characters (a tab is one column; `é` is one column). The end of the text is the position just after its last character (column 1 of a new line if the text ends with `\n`).

## The language (TOML 1.0 minus dates/times)

Everything TOML 1.0 allows except offset date-times, local date-times, local dates and local times is accepted, and parsed to the same Python values as `tomllib.loads` (a valid document gives exactly what `tomllib.loads` gives):

- Comments `#` to the end of the line (outside strings). Whitespace is space and tab. Blank lines anywhere.
- Key/value pairs `key = value`, one per line, optionally followed by whitespace and a comment. Keys: bare (`A-Za-z0-9_-`, so `1234` and `-` are bare keys), basic or literal single-line quoted strings (`"a b"`, `'c'`, `""` is a valid empty key), and dotted keys made of those (`a . "b" . c`, whitespace around dots allowed).
- Tables `[a.b]` and arrays of tables `[[a.b]]` (whitespace inside the brackets, around the key, is allowed; the two brackets of `[[`/`]]` must be adjacent).
- Values: basic strings `"..."` with escapes `\b \t \n \f \r \" \\ \uXXXX \UXXXXXXXX` (a Unicode scalar value: not a surrogate, at most 10FFFF); literal strings `'...'` (no escapes); multi-line basic `"""..."""` and multi-line literal `'''...'''` strings (a newline right after the opening delimiter is dropped; in multi-line basic strings a backslash followed by optional spaces/tabs and a newline removes that newline and all spaces, tabs and newlines after it; one or two quote characters may appear anywhere inside, including right before the closing delimiter, so `"""a"""""` is `a""`); integers (decimal with optional sign and no leading zeros, `0x` hex, `0o` octal, `0b` binary without sign, all with single underscores between digits); floats (fraction and/or exponent, the integer part as for decimal integers, `inf`, `nan` with optional sign); `true`/`false`; arrays (any mix of value types, newlines and comments between elements, optional trailing comma); inline tables `{ k = v, ... }` (on one line, no trailing comma, dotted keys allowed).
- Control characters (U+0000-U+001F except tab, and U+007F) are not allowed inside strings (multi-line strings also allow the newline).

The rules on defining things more than once (TOML 1.0, as `tomllib` enforces them):

1. A key cannot be defined twice in the same table (this includes keys defined through dotted keys and keys inside inline tables).
2. A `[table]` header can be used only once per table. A header cannot define a table that was created by dotted keys, by an inline table, or that is an array of tables; it can define a table that an earlier header created only implicitly (`[a.b]` then `[a]` is fine). Tables created by dotted keys may still get sub-tables from headers (`a.b.c = 1` then `[a.b.d]` is fine).
3. Dotted keys cannot add keys to a table that was defined by a header (after `[a.b]`, a section `[a]` cannot contain `b.c = 1`); tables that headers created only implicitly can be extended this way.
4. Inline tables and arrays are complete when written: no key, dotted key or header may add anything to them or to any table inside them (`a = {b = 1}` then `a.c = 2` or `[a.c]` are errors; so is `[[a]]` after `a = []`).
5. `[[a]]` appends a new table to the array of tables `a`; it is an error if `a` already holds anything else (a table, a value, a static array). A header path or a dotted key path that goes through an array of tables refers to its last table, and each new `[[a]]` table starts empty (what was defined inside the previous one does not count for it).
6. A key whose path goes through a value that is not a table is an error (`a = 1` then `a.b = 2`).

## Errors: kind and position

The parser reads the text left to right and reports the first error it meets, at a precise position. The `kind` is one of:

- `"duplicate"`: a violation of rules 1-6. It is detected as soon as the key has been read completely (the dotted key up to the first character after it that is not whitespace or a dot, before the `=` and the value are checked) or, for a header, as soon as its closing `]`/`]]` has been read (before the rest of the line). The position is the first character of the key (the first character of the whole dotted key, a quote if it starts with one) or the first `[` of the header. Inside inline tables the same: the first character of that key.
- `"string"`: a string not closed before the end of its line (single-line strings, also as keys) or before the end of the text (multi-line strings); reported at the opening quote (the first of the three for multi-line strings). Also a forbidden control character inside a string, reported at that character.
- `"escape"`: an invalid escape sequence (unknown escape letter, `\u`/`\U` without exactly 4/8 hex digits, a value that is not a Unicode scalar value, a backslash followed by whitespace that does not lead to a newline in a multi-line basic string, a backslash at the end of a line in a single-line string), reported at the backslash.
- `"value"`: a value that is not a string, array or inline table is the token made of all characters up to the next space, tab, newline, `,`, `]`, `}`, `#` or the end of the text. If that token is not exactly `true`, `false`, a valid integer or a valid float, the error is reported at the token's first character (`a = tru`, `a = 01`, `a = 1__0`, `a = 1979-05-27`, `a = 1[` are all `value` errors at the token start).
- `"syntax"`: anything else, reported at the first character that cannot continue a valid document (the end of the text counts as a character). For example: a missing value (`a = ` then a newline: at the newline; `a = ]`: at `]`; `a = [1,,2]`: at the second comma), a missing `=` (`a 1`: at `1`), anything but whitespace or a comment after a value or a header (`a = 1 2`: at `2`; `[a]x`: at `x`), a missing `,` or `]` in an array (`[1 2]`: at `2`), an inline table that is not closed before the newline (at the newline), a trailing comma in an inline table (at the `}`), an empty key part (`a.=1`: at `=`), a character that cannot start a key (`$a = 1`: at `$`; `= 1`: at `=`), a header not closed properly (`[a b]`: at `b`; `[[a] ]`: at the space).

When the same text has several problems, the one the parser meets first wins, following the order above: a key is read completely and checked for duplicates before its `=` and value are read, and so on. Examples: `a = 1` + `a = tru` -> `duplicate` at line 2, column 1; `a = { b = 1, b = tru }` -> `duplicate` at column 14; `"a\qb"` with an unterminated string afterwards -> `escape` (met first).

Make sure `python3 -m unittest` passes. The grading tests check the parsed values of many valid documents against `tomllib`, the kind, line and column of many invalid ones, and that your parser accepts exactly the documents `tomllib` accepts on thousands of mutated documents.
