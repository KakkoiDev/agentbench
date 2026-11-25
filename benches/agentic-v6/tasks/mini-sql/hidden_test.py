"""Hidden tests for mini-sql: exact results of hand-written queries, errors, SQLite differential fuzzing, speed."""
import random
import sqlite3 as _sqlite3

# ---- grading helpers (same in every agentic-v6 hidden test file) ----
import os, re as _re_mod, signal, subprocess, sys, textwrap, unittest


class Limited(unittest.TestCase):
    """Each test gets LIMIT seconds; a solution that hangs fails that test only, not the whole suite."""
    LIMIT = 30

    def setUp(self):
        def boom(*_):
            raise TimeoutError(f'test took over {self.LIMIT} s')
        signal.signal(signal.SIGALRM, boom)
        signal.alarm(self.LIMIT)

    def tearDown(self):
        signal.alarm(0)

    def timed(self, setup, work, budget, check=''):
        """Run setup (untimed), then work (timed), then check in a fresh interpreter in this folder.
        Fails if work takes over budget seconds; the process is killed at budget + 20 s."""
        prog = ('import sys, time\nsys.path.insert(0, %r)\n' % os.getcwd() + textwrap.dedent(setup)
                + '\n_t0 = time.perf_counter()\n' + textwrap.dedent(work)
                + '\n_elapsed = time.perf_counter() - _t0\n' + textwrap.dedent(check) + '\nprint("ELAPSED", _elapsed)\n')
        signal.alarm(0)
        try:
            r = subprocess.run([sys.executable, '-I', '-c', prog], capture_output=True, text=True, timeout=budget + 20)
        except subprocess.TimeoutExpired:
            self.fail(f'over {budget} s (stopped after {budget + 20} s)')
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        took = float(_re_mod.search(r'ELAPSED (\S+)', r.stdout)[1])
        sys.stderr.write(f'TIMING {self.id().split(".")[-1]} {took:.3f} {budget}\n')
        self.assertLessEqual(took, budget, f'took {took:.2f} s, the limit is {budget} s')


def banned_source(path, names):
    """Names from `names` that the solution file imports or mentions."""
    import ast
    src = open(path).read()
    found = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            found |= {a.name.split('.')[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split('.')[0])
    hits = {n for n in names if n in found}
    hits |= {s for s in ('__import__', 'importlib', 'sys.modules') if s in src}
    return sorted(hits)
# ---- end of helpers ----

sys.modules['sqlite3'] = None    # the solution must not use SQLite
sys.modules['_sqlite3'] = None
from minisql import SQLError, execute

T = {
    'emp': {'columns': ['id', 'name', 'dept', 'salary', 'boss'],
            'rows': [(1, 'Ann', 10, 5000, None), (2, 'Bob', 20, 4000, 1), (3, 'Cid', 10, None, 1), (4, 'Dee', None, 3000, 2),
                     (5, 'Eve', 20, 4000, 2), (6, 'Fay', 30, 2500.5, 4), (7, 'ann', 10, 1000, 1)]},
    'dept': {'columns': ['id', 'title', 'floor'], 'rows': [(10, 'Sales', 1), (20, 'IT', None), (30, 'Ops', 2), (40, 'Lab', 1)]},
    'empty': {'columns': ['x', 'y'], 'rows': []},
    'nums': {'columns': ['a', 'b'], 'rows': [(7, 2), (-7, 2), (7, -2), (-7, -2), (7, 0), (7.5, 2), (None, 1), (1, 3.0)]},
    'kw': {'columns': ['order', 'my col', 'x'], 'rows': [(2, 'p', 1), (1, 'q', 2)]},
}
CASES = {
    'column_names': 'SELECT id, e.name, salary * 2, salary*2 AS double, count(*) , name||\'!\'  FROM emp e GROUP BY id, e.name, salary, name ORDER BY id LIMIT 2',
    'star_and_table_star': 'SELECT *, d.* FROM emp e JOIN dept d ON e.dept = d.id WHERE e.id < 3',
    'inner_join_order': 'SELECT e.name, d.title FROM dept d JOIN emp e ON e.dept = d.id',
    'left_join_padding': 'SELECT d.title, e.name FROM dept d LEFT JOIN emp e ON e.dept = d.id AND e.salary >= 4000',
    'left_outer_join_keyword': 'SELECT d.title, e.name FROM dept d LEFT OUTER JOIN emp e ON e.dept = d.id WHERE e.name IS NULL',
    'self_join': 'SELECT e.name, b.name AS boss FROM emp e LEFT JOIN emp b ON e.boss = b.id ORDER BY e.id',
    'cross_join': 'SELECT d.id, x.id FROM dept d CROSS JOIN dept x WHERE d.id < x.id AND x.id <= 30',
    'non_equi_join': 'SELECT e.name, d.title FROM emp e JOIN dept d ON e.dept < d.id AND d.floor = 1 ORDER BY 1, 2',
    'three_tables': 'SELECT e.name, d.title, b.name FROM emp e JOIN dept d ON d.id = e.dept JOIN emp b ON b.id = e.boss AND b.dept = d.id',
    'group_order_first_appearance': 'SELECT dept, COUNT(*), SUM(salary), AVG(salary), MIN(name), MAX(salary) FROM emp GROUP BY dept',
    'group_by_expression': 'SELECT salary > 3000, COUNT(*) FROM emp GROUP BY salary > 3000',
    'bare_column_from_first_row': 'SELECT dept, name, COUNT(*) FROM emp GROUP BY dept HAVING COUNT(*) > 1',
    'having_without_selecting_aggregate': 'SELECT dept FROM emp GROUP BY dept HAVING SUM(salary) > 4000 OR dept IS NULL',
    'aggregate_on_empty_table': 'SELECT COUNT(*), COUNT(x), SUM(x), AVG(x), MIN(y), MAX(y) FROM empty',
    'group_by_on_empty_table': 'SELECT x, COUNT(*) FROM empty GROUP BY x',
    'aggregates_skip_nulls': 'SELECT COUNT(*), COUNT(salary), COUNT(dept), COUNT(DISTINCT dept), SUM(DISTINCT salary), AVG(DISTINCT salary) FROM emp',
    'sum_int_vs_float': 'SELECT SUM(id), SUM(salary), SUM(b) FROM emp CROSS JOIN nums WHERE emp.id = 1',
    'order_by_alias_position_expression': 'SELECT name AS n, salary FROM emp ORDER BY dept DESC, 2, n',
    'order_by_nulls': 'SELECT id, salary FROM emp ORDER BY salary',
    'order_by_desc_nulls': 'SELECT id, salary FROM emp ORDER BY salary DESC',
    'order_by_nulls_first_last': 'SELECT id, dept FROM emp ORDER BY dept DESC NULLS FIRST, id DESC',
    'order_by_nulls_last_asc': 'SELECT id, dept FROM emp ORDER BY dept ASC NULLS LAST, id',
    'order_is_stable': 'SELECT name, dept FROM emp ORDER BY dept',
    'order_by_alias_beats_column': 'SELECT name AS dept, dept AS name FROM emp ORDER BY dept LIMIT 3',
    'order_by_aggregate': 'SELECT dept, COUNT(*) AS n FROM emp GROUP BY dept ORDER BY COUNT(*) DESC, dept',
    'order_by_hidden_column': 'SELECT name FROM emp ORDER BY salary DESC NULLS LAST, id',
    'distinct_keeps_first': 'SELECT DISTINCT dept, boss FROM emp',
    'distinct_order_by_output': 'SELECT DISTINCT salary FROM emp ORDER BY salary DESC',
    'limit_offset': 'SELECT id FROM emp ORDER BY id LIMIT 3 OFFSET 2',
    'limit_zero_and_big_offset': 'SELECT id FROM emp LIMIT 0',
    'offset_past_end': 'SELECT id FROM emp LIMIT 5 OFFSET 10',
    'integer_division_and_modulo': 'SELECT a, b, a / b, a % b FROM nums',
    'arithmetic_types': 'SELECT 1 + 2, 1 + 2.0, 7 / 2, 7.0 / 2, 2 * -3, -(4 - 6), 1 - - 1, 2 + 3 * 4 - 6 / 4 FROM dept WHERE id = 10',
    'concat': "SELECT name || '-' || id, salary || '', NULL || 'x', 1.0 || 2 FROM emp WHERE id IN (1, 3, 6)",
    'three_valued_logic': 'SELECT NULL AND FALSE, NULL AND TRUE, NULL OR TRUE, NULL OR FALSE, NOT NULL, NULL = NULL, NULL IS NULL, 1 IS NOT NULL FROM dept WHERE id = 10',
    'in_lists_with_null': 'SELECT 1 IN (1, NULL), 2 IN (1, NULL), 2 NOT IN (1, NULL), 2 NOT IN (1, 3), NULL IN (1), 1 IN (1.0) FROM dept WHERE id = 10',
    'where_drops_null': 'SELECT id FROM emp WHERE salary <> 4000',
    'not_in_with_null_matches_nothing': 'SELECT id FROM emp WHERE dept NOT IN (20, NULL)',
    'between': 'SELECT id FROM emp WHERE salary BETWEEN 2500.5 AND 4000 AND id NOT BETWEEN 5 AND 6',
    'like': "SELECT name FROM emp WHERE name LIKE '_n%' OR name LIKE '%e' OR name LIKE 'B__'",
    'like_is_case_sensitive': "SELECT name FROM emp WHERE name LIKE 'a%'",
    'like_specials': "SELECT 'a%b' LIKE 'a%', 'ab' LIKE 'a_b', '' LIKE '%', 'x' LIKE '', 'a_c' LIKE 'a_c', '%' LIKE '_' FROM dept WHERE id = 10",
    'case_forms': "SELECT name, CASE WHEN salary >= 4000 THEN 'high' WHEN salary IS NULL THEN '?' ELSE 'low' END, CASE dept WHEN 10 THEN 'S' WHEN 20 THEN 'I' END FROM emp",
    'functions': "SELECT UPPER(name), LOWER(name), LENGTH(name), ABS(-salary), COALESCE(dept, boss, -1), NULLIF(dept, 10) FROM emp WHERE id IN (1, 4, 6)",
    'keywords_any_case_and_quoted_names': 'select "order", "my col" AS "the col" From kw Where x >= 1 order by "order"',
    'semicolon_and_whitespace': ' SELECT\tid\nFROM emp\nWHERE id = 2 ; ',
    'string_escapes': "SELECT 'it''s', '', 'a''''b' FROM dept WHERE id = 10",
    'float_literals': 'SELECT 0.5 + 1, 10.25 * 2 FROM dept WHERE id = 10',
    'comparison_of_strings': "SELECT name FROM emp WHERE name < 'B' OR name >= 'ann' ORDER BY name",
    'where_on_left_join_null_side': 'SELECT d.title FROM dept d LEFT JOIN emp e ON e.dept = d.id WHERE e.id IS NULL',
    'having_without_group_by': 'SELECT COUNT(*) FROM emp HAVING COUNT(*) > 100',
    'count_distinct_nulls_not_counted': 'SELECT COUNT(DISTINCT boss), COUNT(boss) FROM emp',
    'min_max_strings': 'SELECT MIN(name), MAX(name), MIN(title) FROM emp CROSS JOIN dept',
}
ERROR_CASES = {
    'unknown_table': 'SELECT * FROM nope',
    'unknown_column': 'SELECT nope FROM emp',
    'unknown_qualified_column': 'SELECT e.nope FROM emp e',
    'unknown_alias': 'SELECT x.id FROM emp e',
    'table_name_hidden_by_alias': 'SELECT emp.id FROM emp e',
    'ambiguous_column': 'SELECT id FROM emp e JOIN dept d ON e.dept = d.id',
    'ambiguous_in_where': 'SELECT e.id FROM emp e CROSS JOIN dept d WHERE id = 1',
    'duplicate_alias': 'SELECT * FROM emp e JOIN dept e ON 1 = 1',
    'later_table_in_on': 'SELECT * FROM emp e JOIN dept d ON e.dept = x.id JOIN emp x ON x.id = e.id',
    'aggregate_in_where': 'SELECT id FROM emp WHERE COUNT(*) > 1',
    'aggregate_in_group_by': 'SELECT COUNT(*) FROM emp GROUP BY COUNT(*)',
    'aggregate_in_on': 'SELECT * FROM emp e JOIN dept d ON COUNT(*) = 1',
    'nested_aggregate': 'SELECT SUM(COUNT(*)) FROM emp',
    'unknown_function': 'SELECT FOO(id) FROM emp',
    'wrong_argument_count': 'SELECT NULLIF(id) FROM emp',
    'order_by_position_out_of_range': 'SELECT id FROM emp ORDER BY 2',
    'order_by_position_zero': 'SELECT id FROM emp ORDER BY 0',
    'distinct_order_by_expression': 'SELECT DISTINCT dept FROM emp ORDER BY salary',
    'ambiguous_order_by_alias': 'SELECT id AS k, name AS k FROM emp ORDER BY k',
    'chained_comparison': 'SELECT 1 = 1 = 1 FROM emp',
    'string_arithmetic': "SELECT name + 1 FROM emp",
    'number_string_comparison': "SELECT id FROM emp WHERE name = 1",
    'like_on_number': 'SELECT id FROM emp WHERE id LIKE 1',
    'syntax_missing_from': 'SELECT id',
    'syntax_trailing_garbage': 'SELECT id FROM emp emp2 emp3',
    'syntax_bad_token': 'SELECT id FROM emp WHERE id = #',
    'syntax_unterminated_string': "SELECT 'abc FROM emp",
    'syntax_keyword_as_name': 'SELECT order FROM kw',
    'syntax_join_without_on': 'SELECT * FROM emp JOIN dept',
    'limit_not_integer': 'SELECT id FROM emp LIMIT 1.5',
    'mixed_types_in_order_by': "SELECT id FROM emp ORDER BY CASE WHEN id = 1 THEN 'a' ELSE id END",
}

EXPECTED = {'column_names': (['id', 'name', 'salary * 2', 'double', 'count(*)', "name||'!'"],
                  [(1, 'Ann', 10000, 10000, 1, 'Ann!'), (2, 'Bob', 8000, 8000, 1, 'Bob!')]),
 'star_and_table_star': (['id', 'name', 'dept', 'salary', 'boss', 'id', 'title', 'floor', 'id', 'title', 'floor'],
                         [(1, 'Ann', 10, 5000, None, 10, 'Sales', 1, 10, 'Sales', 1),
                          (2, 'Bob', 20, 4000, 1, 20, 'IT', None, 20, 'IT', None)]),
 'inner_join_order': (['name', 'title'],
                      [('Ann', 'Sales'), ('Cid', 'Sales'), ('ann', 'Sales'), ('Bob', 'IT'), ('Eve', 'IT'), ('Fay', 'Ops')]),
 'left_join_padding': (['title', 'name'], [('Sales', 'Ann'), ('IT', 'Bob'), ('IT', 'Eve'), ('Ops', None), ('Lab', None)]),
 'left_outer_join_keyword': (['title', 'name'], [('Lab', None)]),
 'self_join': (['name', 'boss'],
               [('Ann', None), ('Bob', 'Ann'), ('Cid', 'Ann'), ('Dee', 'Bob'), ('Eve', 'Bob'), ('Fay', 'Dee'), ('ann', 'Ann')]),
 'cross_join': (['id', 'id'], [(10, 20), (10, 30), (20, 30)]),
 'non_equi_join': (['name', 'title'],
                   [('Ann', 'Lab'), ('Bob', 'Lab'), ('Cid', 'Lab'), ('Eve', 'Lab'), ('Fay', 'Lab'), ('ann', 'Lab')]),
 'three_tables': (['name', 'title', 'name'], [('Cid', 'Sales', 'Ann'), ('Eve', 'IT', 'Bob'), ('ann', 'Sales', 'Ann')]),
 'group_order_first_appearance': (['dept', 'COUNT(*)', 'SUM(salary)', 'AVG(salary)', 'MIN(name)', 'MAX(salary)'],
                                  [(10, 3, 6000, 3000.0, 'Ann', 5000),
                                   (20, 2, 8000, 4000.0, 'Bob', 4000),
                                   (None, 1, 3000, 3000.0, 'Dee', 3000),
                                   (30, 1, 2500.5, 2500.5, 'Fay', 2500.5)]),
 'group_by_expression': (['salary > 3000', 'COUNT(*)'], [(True, 3), (None, 1), (False, 3)]),
 'bare_column_from_first_row': (['dept', 'name', 'COUNT(*)'], [(10, 'Ann', 3), (20, 'Bob', 2)]),
 'having_without_selecting_aggregate': (['dept'], [(10,), (20,), (None,)]),
 'aggregate_on_empty_table': (['COUNT(*)', 'COUNT(x)', 'SUM(x)', 'AVG(x)', 'MIN(y)', 'MAX(y)'], [(0, 0, None, None, None, None)]),
 'group_by_on_empty_table': (['x', 'COUNT(*)'], []),
 'aggregates_skip_nulls': (['COUNT(*)',
                            'COUNT(salary)',
                            'COUNT(dept)',
                            'COUNT(DISTINCT dept)',
                            'SUM(DISTINCT salary)',
                            'AVG(DISTINCT salary)'],
                           [(7, 6, 6, 3, 15500.5, 3100.1)]),
 'sum_int_vs_float': (['SUM(id)', 'SUM(salary)', 'SUM(b)'], [(8, 40000, 6.0)]),
 'order_by_alias_position_expression': (['n', 'salary'],
                                        [('Fay', 2500.5),
                                         ('Bob', 4000),
                                         ('Eve', 4000),
                                         ('Cid', None),
                                         ('ann', 1000),
                                         ('Ann', 5000),
                                         ('Dee', 3000)]),
 'order_by_nulls': (['id', 'salary'], [(3, None), (7, 1000), (6, 2500.5), (4, 3000), (2, 4000), (5, 4000), (1, 5000)]),
 'order_by_desc_nulls': (['id', 'salary'], [(1, 5000), (2, 4000), (5, 4000), (4, 3000), (6, 2500.5), (7, 1000), (3, None)]),
 'order_by_nulls_first_last': (['id', 'dept'], [(4, None), (6, 30), (5, 20), (2, 20), (7, 10), (3, 10), (1, 10)]),
 'order_by_nulls_last_asc': (['id', 'dept'], [(1, 10), (3, 10), (7, 10), (2, 20), (5, 20), (6, 30), (4, None)]),
 'order_is_stable': (['name', 'dept'],
                     [('Dee', None), ('Ann', 10), ('Cid', 10), ('ann', 10), ('Bob', 20), ('Eve', 20), ('Fay', 30)]),
 'order_by_alias_beats_column': (['dept', 'name'], [('Ann', 10), ('Bob', 20), ('Cid', 10)]),
 'order_by_aggregate': (['dept', 'n'], [(10, 3), (20, 2), (None, 1), (30, 1)]),
 'order_by_hidden_column': (['name'], [('Ann',), ('Bob',), ('Eve',), ('Dee',), ('Fay',), ('ann',), ('Cid',)]),
 'distinct_keeps_first': (['dept', 'boss'], [(10, None), (20, 1), (10, 1), (None, 2), (20, 2), (30, 4)]),
 'distinct_order_by_output': (['salary'], [(5000,), (4000,), (3000,), (2500.5,), (1000,), (None,)]),
 'limit_offset': (['id'], [(3,), (4,), (5,)]),
 'limit_zero_and_big_offset': (['id'], []),
 'offset_past_end': (['id'], []),
 'integer_division_and_modulo': (['a', 'b', 'a / b', 'a % b'],
                                 [(7, 2, 3, 1),
                                  (-7, 2, -3, -1),
                                  (7, -2, -3, 1),
                                  (-7, -2, 3, -1),
                                  (7, 0, None, None),
                                  (7.5, 2, 3.75, 1.5),
                                  (None, 1, None, None),
                                  (1, 3.0, 0.3333333333333333, 1.0)]),
 'arithmetic_types': (['1 + 2', '1 + 2.0', '7 / 2', '7.0 / 2', '2 * -3', '-(4 - 6)', '1 - - 1', '2 + 3 * 4 - 6 / 4'],
                      [(3, 3.0, 3, 3.5, -6, 2, 2, 13)]),
 'concat': (["name || '-' || id", "salary || ''", "NULL || 'x'", '1.0 || 2'],
            [('Ann-1', '5000', None, '1.02'), ('Cid-3', None, None, '1.02'), ('Fay-6', '2500.5', None, '1.02')]),
 'three_valued_logic': (['NULL AND FALSE',
                         'NULL AND TRUE',
                         'NULL OR TRUE',
                         'NULL OR FALSE',
                         'NOT NULL',
                         'NULL = NULL',
                         'NULL IS NULL',
                         '1 IS NOT NULL'],
                        [(False, None, True, None, None, None, True, True)]),
 'in_lists_with_null': (['1 IN (1, NULL)',
                         '2 IN (1, NULL)',
                         '2 NOT IN (1, NULL)',
                         '2 NOT IN (1, 3)',
                         'NULL IN (1)',
                         '1 IN (1.0)'],
                        [(True, None, None, True, None, True)]),
 'where_drops_null': (['id'], [(1,), (4,), (6,), (7,)]),
 'not_in_with_null_matches_nothing': (['id'], []),
 'between': (['id'], [(2,), (4,)]),
 'like': (['name'], [('Ann',), ('Bob',), ('Dee',), ('Eve',), ('ann',)]),
 'like_is_case_sensitive': (['name'], [('ann',)]),
 'like_specials': (["'a%b' LIKE 'a%'", "'ab' LIKE 'a_b'", "'' LIKE '%'", "'x' LIKE ''", "'a_c' LIKE 'a_c'", "'%' LIKE '_'"],
                   [(True, False, True, False, True, True)]),
 'case_forms': (['name',
                 "CASE WHEN salary >= 4000 THEN 'high' WHEN salary IS NULL THEN '?' ELSE 'low' END",
                 "CASE dept WHEN 10 THEN 'S' WHEN 20 THEN 'I' END"],
                [('Ann', 'high', 'S'),
                 ('Bob', 'high', 'I'),
                 ('Cid', '?', 'S'),
                 ('Dee', 'low', None),
                 ('Eve', 'high', 'I'),
                 ('Fay', 'low', None),
                 ('ann', 'low', 'S')]),
 'functions': (['UPPER(name)', 'LOWER(name)', 'LENGTH(name)', 'ABS(-salary)', 'COALESCE(dept, boss, -1)', 'NULLIF(dept, 10)'],
               [('ANN', 'ann', 3, 5000, 10, None), ('DEE', 'dee', 3, 3000, 2, None), ('FAY', 'fay', 3, 2500.5, 30, 30)]),
 'keywords_any_case_and_quoted_names': (['order', 'the col'], [(1, 'q'), (2, 'p')]),
 'semicolon_and_whitespace': (['id'], [(2,)]),
 'string_escapes': (["'it''s'", "''", "'a''''b'"], [("it's", '', "a''b")]),
 'float_literals': (['0.5 + 1', '10.25 * 2'], [(1.5, 20.5)]),
 'comparison_of_strings': (['name'], [('Ann',), ('ann',)]),
 'where_on_left_join_null_side': (['title'], [('Lab',)]),
 'having_without_group_by': (['COUNT(*)'], []),
 'count_distinct_nulls_not_counted': (['COUNT(DISTINCT boss)', 'COUNT(boss)'], [(3, 6)]),
 'min_max_strings': (['MIN(name)', 'MAX(name)', 'MIN(title)'], [('Ann', 'ann', 'IT')])}


def typed(result):
    cols, rows = result
    return list(cols), [tuple((type(v).__name__, v) for v in r) for r in rows]


GROUPS = {
    'output_column_names': ['column_names', 'keywords_any_case_and_quoted_names'],
    'star_expansion': ['star_and_table_star'],
    'inner_join_row_order': ['inner_join_order', 'three_tables'],
    'left_join': ['left_join_padding', 'left_outer_join_keyword', 'where_on_left_join_null_side'],
    'self_and_cross_joins': ['self_join', 'cross_join', 'non_equi_join'],
    'group_by_first_appearance_and_aggregates': ['group_order_first_appearance', 'group_by_expression'],
    'bare_columns_and_having': ['bare_column_from_first_row', 'having_without_selecting_aggregate', 'having_without_group_by'],
    'aggregates_on_empty_input': ['aggregate_on_empty_table', 'group_by_on_empty_table'],
    'aggregates_and_nulls': ['aggregates_skip_nulls', 'count_distinct_nulls_not_counted', 'sum_int_vs_float', 'min_max_strings'],
    'order_by_terms': ['order_by_alias_position_expression', 'order_by_alias_beats_column', 'order_by_hidden_column'],
    'order_by_nulls': ['order_by_nulls', 'order_by_desc_nulls', 'order_by_nulls_first_last', 'order_by_nulls_last_asc'],
    'order_by_is_stable_and_aggregates': ['order_is_stable', 'order_by_aggregate'],
    'distinct': ['distinct_keeps_first', 'distinct_order_by_output'],
    'limit_offset': ['limit_offset', 'limit_zero_and_big_offset', 'offset_past_end'],
    'integer_division_and_modulo': ['integer_division_and_modulo', 'arithmetic_types', 'float_literals'],
    'concatenation_and_strings': ['concat', 'string_escapes', 'semicolon_and_whitespace', 'comparison_of_strings'],
    'three_valued_logic': ['three_valued_logic', 'where_drops_null'],
    'in_and_between': ['in_lists_with_null', 'not_in_with_null_matches_nothing', 'between'],
    'like': ['like', 'like_is_case_sensitive', 'like_specials'],
    'case_and_functions': ['case_forms', 'functions'],
}
assert sorted(sum(GROUPS.values(), [])) == sorted(CASES), set(CASES) ^ set(sum(GROUPS.values(), []))

ERROR_GROUPS = {
    'unknown_names': ['unknown_table', 'unknown_column', 'unknown_qualified_column', 'unknown_alias', 'table_name_hidden_by_alias',
                      'later_table_in_on', 'unknown_function', 'wrong_argument_count'],
    'ambiguous_names': ['ambiguous_column', 'ambiguous_in_where', 'duplicate_alias', 'ambiguous_order_by_alias'],
    'aggregate_misuse': ['aggregate_in_where', 'aggregate_in_group_by', 'aggregate_in_on', 'nested_aggregate'],
    'order_by_errors': ['order_by_position_out_of_range', 'order_by_position_zero', 'distinct_order_by_expression', 'mixed_types_in_order_by'],
    'type_errors': ['string_arithmetic', 'number_string_comparison', 'like_on_number', 'chained_comparison'],
    'syntax_errors': ['syntax_missing_from', 'syntax_trailing_garbage', 'syntax_bad_token', 'syntax_unterminated_string',
                      'syntax_keyword_as_name', 'syntax_join_without_on', 'limit_not_integer'],
}
assert sorted(sum(ERROR_GROUPS.values(), [])) == sorted(ERROR_CASES)


class Queries(Limited):
    pass


def _cases_test(names):
    def test(self):
        for n in names:
            self.assertEqual(typed(execute(CASES[n], T)), typed(EXPECTED[n]), n + ': ' + CASES[n])
    return test


def _errors_test(names):
    def test(self):
        for n in names:
            with self.assertRaises(SQLError, msg=n + ': ' + ERROR_CASES[n]):
                execute(ERROR_CASES[n], T)
    return test


for _g, _names in GROUPS.items():
    setattr(Queries, 'test_' + _g, _cases_test(_names))
for _g, _names in ERROR_GROUPS.items():
    setattr(Queries, 'test_errors_' + _g, _errors_test(_names))


# ---- differential tests against SQLite (multisets of rows: SQLite does not promise our row order) ----
INT_COLS = ['e.id', 'e.dept', 'e.boss', 'd.did', 'p.pid', 'p.owner', 'p.dept', 'p.hours']
NUM_COLS = INT_COLS + ['e.sal', 'd.budget']
STR_COLS = ['e.name', 'd.dname']


def make_tables(rnd):
    def val(kind):
        if rnd.random() < 0.15:
            return None
        if kind == 'i':
            return rnd.randint(-3, 6)
        if kind == 'f':
            return rnd.choice([0.5, 1.5, -2.25, 3.0, 2.5])
        return rnd.choice(['a', 'b', 'ab', 'ba', 'abc', '', 'x_y', 'B'])
    t = {'emp': {'columns': ['id', 'dept', 'sal', 'name', 'boss'], 'kinds': 'iifsi'},
         'dept': {'columns': ['did', 'dname', 'budget'], 'kinds': 'isf'},
         'proj': {'columns': ['pid', 'owner', 'dept', 'hours'], 'kinds': 'iiii'}}
    for d in t.values():
        d['rows'] = [tuple(val(k) for k in d['kinds']) for _ in range(rnd.randint(0, 9))]
    return t


def num(rnd, d=2):
    r = rnd.random()
    if d <= 0 or r < 0.4:
        return rnd.choice(NUM_COLS)
    if r < 0.55:
        return str(rnd.randint(-2, 4))
    if r < 0.65:
        return 'NULL'
    if r < 0.85:
        return f'({num(rnd, d - 1)} {rnd.choice(["+", "-", "*", "/"])} {num(rnd, d - 1)})'
    if r < 0.9:
        return f'({rnd.choice(INT_COLS)} % {rnd.choice(["2", "3", "-2", "0", "e.id"])})'
    if r < 0.95:
        return f'COALESCE({num(rnd, d - 1)}, {num(rnd, d - 1)})'
    return f'ABS({num(rnd, d - 1)})'


def text(rnd, d=2):
    r = rnd.random()
    if d <= 0 or r < 0.5:
        return rnd.choice(STR_COLS)
    if r < 0.65:
        return rnd.choice(["'a'", "'ab'", "''", "'b'"])
    if r < 0.8:
        return f'({text(rnd, d - 1)} || {text(rnd, d - 1)})'
    if r < 0.9:
        return f'UPPER({text(rnd, d - 1)})'
    return f'LOWER({text(rnd, d - 1)})'


def cond(rnd, d=2):
    r = rnd.random()
    if d <= 0 or r < 0.3:
        return f'{num(rnd, 1)} {rnd.choice(["=", "<>", "<", "<=", ">", ">=", "!="])} {num(rnd, 1)}'
    if r < 0.4:
        return f'{text(rnd, 1)} {rnd.choice(["=", "<", ">="])} {text(rnd, 1)}'
    if r < 0.5:
        return f'{rnd.choice(NUM_COLS + STR_COLS)} IS {rnd.choice(["", "NOT "])}NULL'
    if r < 0.6:
        items = ', '.join(rnd.choice(['1', '2', 'NULL', 'e.id', '-1']) for _ in range(rnd.randint(1, 3)))
        return f'{num(rnd, 1)} {rnd.choice(["", "NOT "])}IN ({items})'
    if r < 0.67:
        return f'{num(rnd, 1)} {rnd.choice(["", "NOT "])}BETWEEN {num(rnd, 0)} AND {num(rnd, 0)}'
    if r < 0.74:
        pat = rnd.choice(['%a%', 'a_', '_', '%', 'a%b', '%b', 'x_%'])
        return f"{text(rnd, 1)} {rnd.choice(['', 'NOT '])}LIKE '{pat}'"
    if r < 0.84:
        return f'({cond(rnd, d - 1)} {rnd.choice(["AND", "OR"])} {cond(rnd, d - 1)})'
    if r < 0.9:
        return f'NOT ({cond(rnd, d - 1)})'
    return f'(CASE WHEN {cond(rnd, d - 1)} THEN {num(rnd, 0)} ELSE {num(rnd, 0)} END) > 0'


FROMS = ['emp e', 'emp e JOIN dept d ON e.dept = d.did', 'emp e LEFT JOIN dept d ON e.dept = d.did',
         'emp e LEFT JOIN dept d ON e.dept = d.did AND d.budget > 1', 'emp e JOIN proj p ON p.owner = e.id JOIN dept d ON d.did = p.dept',
         'emp e LEFT JOIN proj p ON p.owner = e.id LEFT JOIN dept d ON d.did = e.dept', 'emp e CROSS JOIN dept d',
         'emp e JOIN dept d ON e.dept = d.did OR d.did IS NULL', 'emp e LEFT JOIN proj p ON e.id = p.owner AND p.hours > e.dept']


def query(rnd):
    import re as _re
    f = rnd.choice(FROMS)
    have = {'e'} | ({'d'} if ' d ' in f + ' ' else set()) | ({'p'} if ' p ' in f + ' ' else set())
    while True:
        where = cond(rnd) if rnd.random() < 0.7 else None
        if rnd.random() < 0.5:
            sel = ', '.join(rnd.choice([num(rnd), text(rnd), cond(rnd, 1)]) for _ in range(rnd.randint(1, 3)))
            q = f'SELECT {"DISTINCT " if rnd.random() < 0.2 else ""}{sel} FROM {f}' + (f' WHERE {where}' if where else '')
        else:
            g = rnd.choice(['e.dept', 'e.name', 'e.boss', 'e.dept, e.boss'] + (['d.dname'] if 'd' in have else [])
                           + (['p.hours % 3'] if 'p' in have else []))
            aggs = ', '.join(rnd.choice(['COUNT(*)', f'COUNT({rnd.choice(NUM_COLS)})', f'SUM({rnd.choice(INT_COLS)})',
                                         f'MIN({rnd.choice(NUM_COLS + STR_COLS)})', f'MAX({num(rnd, 1)})',
                                         f'COUNT(DISTINCT {rnd.choice(NUM_COLS)})', f'SUM(DISTINCT {rnd.choice(INT_COLS)})',
                                         f'AVG({rnd.choice(INT_COLS)})']) for _ in range(rnd.randint(1, 3)))
            q = f'SELECT {g}, {aggs} FROM {f}' + (f' WHERE {where}' if where else '') + f' GROUP BY {g}'
            if rnd.random() < 0.4:
                q += f' HAVING COUNT(*) > {rnd.randint(0, 2)}'
        if all(x in have for x in _re.findall(r'\b([edp])\.\w+', q)):
            return q


def norm(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, float):
        return round(v, 9)
    return v


def sort_key(row):
    return tuple((0, '') if v is None else (1, v) if isinstance(v, (int, float)) else (2, v) for v in row)


class AgainstSQLite(Limited):
    LIMIT = 90

    def run_seed(self, seed, n=500):
        rnd = random.Random(seed)
        for _ in range(n):
            t, q = make_tables(rnd), None
            q = query(rnd)
            con = _sqlite3.connect(':memory:')
            con.execute('PRAGMA case_sensitive_like = ON')
            for name, d in t.items():
                con.execute(f'CREATE TABLE {name} ({", ".join(d["columns"])})')
                con.executemany(f'INSERT INTO {name} VALUES ({",".join("?" * len(d["columns"]))})', d['rows'])
            exp = sorted([tuple(norm(v) for v in r) for r in con.execute(q).fetchall()], key=sort_key)
            con.close()
            got = sorted([tuple(norm(v) for v in r) for r in execute(q, t)[1]], key=sort_key)
            self.assertEqual(got, exp, (q, {k: v['rows'] for k, v in t.items()}))

    def test_random_queries_1(self):
        self.run_seed(1)

    def test_random_queries_2(self):
        self.run_seed(2)

    def test_random_queries_3(self):
        self.run_seed(3)


class Speed(Limited):
    LIMIT = 120
    SETUP = '''
import random
from minisql import execute
rnd = random.Random(1)
n = 40000
ks = list(range(n))
rnd.shuffle(ks)
T = {'a': {'columns': ['k', 'v', 'g'], 'rows': [(i, i % 97, i % 10) for i in range(n)]},
     'b': {'columns': ['k', 'w'], 'rows': [(k, k % 13) for k in ks]}}
big = {'big': {'columns': ['g', 'x', 's'], 'rows': [(rnd.randint(0, 999), rnd.randint(-100, 100), rnd.choice(['p', 'q', 'r'])) for _ in range(200000)]}}
'''

    def test_equi_join_40000_by_40000(self):
        self.timed(self.SETUP, "r = execute('SELECT COUNT(*), SUM(a.v + b.w) FROM a JOIN b ON a.k = b.k AND b.w % 2 = 0', T)", 6,
                   "assert r == (['COUNT(*)', 'SUM(a.v + b.w)'], [(21538, 1162423)]), r")

    def test_left_join_on_expression_then_group(self):
        self.timed(self.SETUP, "r = execute('SELECT a.g, COUNT(b.k) FROM a LEFT JOIN b ON b.k = a.k + 20000 GROUP BY a.g ORDER BY a.g DESC', T)", 6,
                   "assert r[1] == [(g, 2000) for g in range(9, -1, -1)], r")

    def test_group_by_200000_rows(self):
        self.timed(self.SETUP, "r = execute('SELECT g, COUNT(*), SUM(x), MAX(s) FROM big WHERE x <> 0 GROUP BY g ORDER BY 2 DESC, g LIMIT 3', big)", 6,
                   "assert r[1] == [(190, 244, -1133, 'r'), (25, 243, -1738, 'r'), (855, 238, -834, 'r')], r")

    def test_sort_200000_rows(self):
        self.timed(self.SETUP, "r = execute('SELECT g, x FROM big ORDER BY x DESC, g LIMIT 3', big)", 6,
                   "assert r[1] == [(0, 100), (0, 100), (1, 100)], r")


class Rules(Limited):
    def test_no_sqlite(self):
        self.assertEqual(banned_source('minisql.py', {'sqlite3', '_sqlite3'}), [])


if __name__ == '__main__':
    unittest.main()
