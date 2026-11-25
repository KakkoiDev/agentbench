Write `minisql.py`: a SQL `SELECT` engine over in-memory tables, in Python 3 standard library only. **Do not use `sqlite3`** (the grader blocks it).

## API

`execute(sql, tables) -> (column_names, rows)`. `tables` maps a table name to `{'columns': [names], 'rows': [tuples]}`; values are `None` (NULL), `int`, `float` or `str`. The result is a list of output column names and a list of tuples. Any error (syntax, unknown table/column/function, ambiguous column, misuse of aggregates, type errors) raises `SQLError` (an `Exception` subclass defined in minisql.py).

## Syntax

```
SELECT [DISTINCT] item, ... FROM table [[AS] alias]
    { [INNER] JOIN table [[AS] alias] ON cond | LEFT [OUTER] JOIN table [[AS] alias] ON cond | CROSS JOIN table [[AS] alias] }
    [WHERE cond] [GROUP BY expr, ...] [HAVING cond]
    [ORDER BY expr [ASC|DESC] [NULLS FIRST|NULLS LAST], ...] [LIMIT n [OFFSET m]] [;]
item: *  |  name.*  |  expr [[AS] alias]
```

- Keywords and function names are case-insensitive; table, alias and column names are case-sensitive. Names are `[A-Za-z_][A-Za-z0-9_]*` (not a keyword) or double-quoted (`"order"`, `"my col"`). Keywords: SELECT DISTINCT FROM WHERE GROUP BY HAVING ORDER ASC DESC NULLS FIRST LAST LIMIT OFFSET AS JOIN INNER LEFT OUTER CROSS ON AND OR NOT IS NULL IN BETWEEN LIKE CASE WHEN THEN ELSE END TRUE FALSE.
- Literals: integers `42`, floats `1.5` (digits on both sides of the dot), strings `'it''s'` (a doubled quote is a quote), `NULL`, `TRUE`, `FALSE`. `LIMIT`/`OFFSET` take integer literals.
- Expressions, from lowest to highest precedence: `OR`; `AND`; `NOT`; comparisons `= <> != < <= > >=`, `IS [NOT] NULL`, `[NOT] IN (e, ...)`, `[NOT] BETWEEN a AND b`, `[NOT] LIKE p` (one comparison per level: `a = b = c` is an error); `||`; binary `+ -`; `* / %`; unary `-`/`+`; then literals, column references `col` or `alias.col`, function calls, `CASE`, parentheses. Binary operators of equal precedence group left to right.
- `CASE WHEN c THEN v ... [ELSE e] END` (the first WHEN whose condition is TRUE) and `CASE x WHEN v THEN r ... [ELSE e] END` (the first WHEN where `x = v` is TRUE); no match and no ELSE gives NULL.
- Functions: `COALESCE(a, ...)` (first non-NULL), `NULLIF(a, b)` (NULL if `a = b` is TRUE, else a), `UPPER(s)`, `LOWER(s)`, `LENGTH(s)`, `ABS(x)` (NULL argument -> NULL); aggregates `COUNT(*)`, `COUNT(e)`, `SUM(e)`, `AVG(e)`, `MIN(e)`, `MAX(e)`, each also with `DISTINCT` inside (`COUNT(DISTINCT e)`). Unknown functions and wrong argument counts are errors.

## Values and NULL

- Comparisons are between two numbers (ints and floats compare numerically, `1 = 1.0`) or two strings (by code point); comparing a number with a string is an error. Any comparison with NULL is NULL. `AND`/`OR`/`NOT` use three-valued logic (`FALSE AND NULL` is FALSE, `TRUE OR NULL` is TRUE, `NOT NULL` is NULL). Comparison results are Python `True`/`False`/`None`.
- `x IN (...)`: TRUE if x equals a non-NULL item; otherwise NULL if x is NULL or any item is NULL; otherwise FALSE. `NOT IN` is `NOT (x IN (...))`. `x BETWEEN a AND b` is `x >= a AND x <= b`. `IS NULL`/`IS NOT NULL` are never NULL.
- `LIKE`: `%` matches any run of characters, `_` exactly one; case-sensitive; no escape character; both operands must be strings (or NULL -> NULL).
- Arithmetic needs numbers (or NULL -> NULL), else error. `+ - *`: int with int gives int, otherwise float. `/` with two ints is integer division truncating toward zero (`-7 / 2 = -3`), otherwise float division. `%` with two ints has the sign of the left operand (`-7 % 2 = -1`), otherwise `math.fmod`. Division or `%` by zero gives NULL.
- `a || b`: NULL if either is NULL; numbers are turned into text with Python's `str` (`1.0` -> `'1.0'`).
- `WHERE`, `ON` and `HAVING` keep a row/group only when the condition is TRUE.

## Names and columns

- Each table in FROM is known by its alias, or by its name if it has none; two tables with the same name/alias is an error. `alias.col` must name a known table and one of its columns. An unqualified `col` must exist in exactly one table in FROM (else "unknown" or "ambiguous" error). Exception: an `ON` condition sees only the tables joined so far (those before it and the one it joins): a later table's columns are unknown there, and unqualified names are looked up among those tables only.
- `*` expands to all columns of all FROM tables in FROM order; `alias.*` to that table's columns.
- Output column names: the alias when given (`AS x` or just `x`); for a plain column reference, the column name (without the table prefix); otherwise the exact source text of the expression with surrounding spaces removed (`SELECT count(*) , a+ b` gives `count(*)` and `a+ b`).

## Evaluation order and row order (exact order matters in the tests)

1. FROM and joins, left to right. A join produces, for each row so far (in order), the matching rows of the joined table in that table's order; `LEFT JOIN` adds the row padded with NULLs when nothing matches; `CROSS JOIN` pairs every row with every row.
2. WHERE (no aggregates allowed).
3. Grouping. A query is grouped if it has GROUP BY or HAVING, or an aggregate appears in the select list or in ORDER BY. Groups are formed by equal GROUP BY values (NULLs group together) and come out in the order in which each group's first row appeared. Without GROUP BY, a grouped query is one group of all rows - even when there are no rows (`SELECT COUNT(*) FROM empty` gives one row `(0,)`). Aggregates skip NULLs: `COUNT(e)` counts non-NULL values, `SUM` is NULL when there are none (int if all values are ints), `AVG` is a float or NULL, `MIN`/`MAX` are NULL when there are none. In a grouped query, a column reference outside an aggregate takes its value from the first row of the group (NULL for an empty group). Aggregates are not allowed in WHERE, ON or GROUP BY, nor inside another aggregate.
4. HAVING filters groups.
5. The select list is computed; `DISTINCT` removes repeated output rows (NULLs equal), keeping the first.
6. ORDER BY, a stable sort (rows that tie keep their order). Each term is: a positive integer literal -> that output column (out of range is an error); otherwise a bare name that is the name of exactly one output column given by an alias or by a plain column reference -> that output column (two such columns -> error); otherwise an expression over the input row (or over the group, aggregates allowed, in grouped queries). With DISTINCT only the first two kinds are allowed. NULLs sort first with ASC and last with DESC unless NULLS FIRST/LAST says otherwise. Values of a sort column must be comparable (else error).
7. OFFSET skips rows, then LIMIT keeps at most n.

## Speed

An equi-join of two 40,000-row tables (`ON a.k = b.k`, possibly with extra conditions in the ON) and a GROUP BY over 200,000 rows must each take only a few seconds: a nested loop over all pairs is far too slow.

Make sure `python3 -m unittest` passes. The grading tests compare your results with SQLite on thousands of random queries (where both define the same result), check exact row order, column names and errors on hand-written queries, and time large joins.
