"""Hidden tests for toml-errors: values against tomllib, exact error kinds and positions, acceptance fuzzing."""
import math
import random
import tomllib as _oracle

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

for _name in ('tomllib', 'tomli', 'toml', 'tomlkit', 'tomli_w', 'pytoml', 'qtoml', 'rtoml'):
    sys.modules[_name] = None   # the solution must not use a TOML library
from toml_subset import TOMLError, loads


def same(a, b):
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


SEEDS = [
'''# config
title = "TOML \\"x\\" \\u00e9"
lit = 'C:\\path'
[owner]
name = "Tom"
age = 42
"quoted key" = 1
a.b.c = true
[database]
ports = [ 8000, 8001,
  8002, # comment
]
data = [ ["gamma", "delta"], [1, 2] ]
temp = { cpu = 79.5, case = -72.0e-1 }
[servers.alpha]
ip = "10.0.0.1"
[servers.beta]
ip = "10.0.0.2"
[[products]]
name = "Hammer"
sku = 738594937
[[products]]
[[products]]
name = "Nail"
color = "gray"
[[products.parts]]
x = 0xFF
''',
'''[[fruits]]
name = "apple"
[fruits.physical]
color = "red"
[[fruits.varieties]]
name = "red delicious"
[[fruits.varieties]]
name = "granny smith"
[[fruits]]
name = "banana"
[[fruits.varieties]]
name = "plantain"
''',
'''s1 = """
Roses are red
Violets are blue"""
s2 = """\\
  The quick brown \\
  fox."""
s3 = \'\'\'
raw \\n text\'\'\'
s4 = """Here are two quotation marks: "". Simple enough."""
s5 = """a"""""
s6 = \'\'\'b\'\'\'\'\'
int1 = +99
int2 = -17
int3 = 1_000
hex = 0xDEADBEEF
oct = 0o755
bin = 0b1101_0110
f1 = 3.1415
f2 = -0.01
f3 = 5e+22
f4 = 1e06
f5 = -2E-2
f6 = 6.626e-34
f7 = 224_617.445_991
sf1 = inf
sf2 = -inf
sf3 = nan
b = false
''',
'''fruit.apple.color = "red"
fruit.apple.taste.sweet = true
fruit.orange = 2
[fruit.apple.texture]
smooth = true
[dog."tater.man"]
type.name = "pug"
[x.y.z.w]
[x]
a = {}
b = { a.b = 1, a.c = 2 }
c = [ { x = 1 }, { y = 2 } ]
"" = "empty key"
\'lit key\' = 1
3 = "x"
-_- = 1
''',
]

VALID = {
    'empty': '',
    'only_comments_and_blank_lines': '# a\n\n   # b\n\t\n',
    'no_final_newline': 'a = 1',
    'bare_keys_that_look_like_numbers': '1234 = 1\n-a_b- = 2\n3.14 = "pi"\n',
    'quoted_and_dotted_keys': '"a.b" = 1\na . "b c" . \'d\' = 2\n"z" = 3\n\'\' . x = 4\n',
    'whitespace_everywhere': '  a\t=\t1  # c\n[ t . u ]  # c\n  b = 2\n[[ arr ]]\n',
    'basic_string_escapes': 's = "\\b\\t\\n\\f\\r\\"\\\\ \\u00E9 \\U0001F600 tab:\tend"\n',
    'literal_strings_keep_backslashes': "p = 'C:\\Users\\nobody'\nq = ''\n",
    'multiline_basic_trims_first_newline_only': 's = """\n\nline"""\nt = """x\n"""\n',
    'multiline_basic_line_ending_backslash': 's = """a \\   \n\n   \tb \\\n c"""\nt = """\\\n"""\n',
    'multiline_quotes_inside_and_at_the_end': 'a = """""x"""""\nb = """x""y"""\nc = \'\'\'\'\'x\'\'\'\'\'\nd = """""""\n',
    'multiline_literal_raw': "s = '''\nC:\\x \\u1234\n  two'''\n",
    'integers': 'a = 0\nb = +0\nc = -0\nd = 1_2_3\ne = -9_223_372_036_854_775_808\nf = 0xdead_BEEF\ng = 0o0_7\nh = 0b0_1\ni = 99999999999999999999\n',
    'floats': 'a = 0.0\nb = -0.0\nc = 0e0\nd = 1e1_0\ne = 1_0.0_1e-0_2\nf = +1.5E+3\ng = 3e-005\n',
    'special_floats': 'a = inf\nb = +inf\nc = -inf\nd = nan\ne = +nan\nf = -nan\n',
    'arrays_mixed_nested_multiline': 'a = [\n  1, "two", [3.0, [true]], {x = 1}, # c\n  # only a comment\n\n]\nb = []\nc = [\n]\n',
    'inline_tables': 'a = {}\nb = { x = 1, y.z = "q", w = { v = [1, 2] } }\nc = {x=1,y=2}\n',
    'tables_and_implicit_parents': '[a.b.c]\nx = 1\n[a]\ny = 2\n[a.b]\nz = 3\n',
    'dotted_keys_then_subtable_header': 'a.b.c = 1\n[a.b.d]\ne = 2\n',
    'implicit_table_extended_by_dotted_key': '[a.b.c]\nz = 9\n[a]\nb.x = 1\n',
    'array_of_tables_with_subtables': '[[a]]\nx = 1\n[a.b]\ny = 2\n[[a.c]]\n[[a]]\n[a.b]\ny = 3\n[[a.c]]\nz = 1\n',
    'array_of_tables_new_element_is_fresh': '[[a]]\nb.c = 1\nd = [1]\n[[a]]\nb.c = 2\nd = [2]\n[a.e]\n',
    'nested_array_of_tables_through_implicit': '[[a.b]]\n[a]\nc = 1\n[[a.b]]\n',
    'comments_right_after_values': 'a = true#c\nb = [1]#c\nc = "x"#c\nd = {a=1}#c\n',
    'unicode_text_and_keys': '"é" = "ü"\nk = \'日本\'\n',
}

ERRORS = {
    'value_then_garbage': ('a = 1 2\n', 'syntax', 1, 7),
    'missing_value_at_newline': ('a = \n', 'syntax', 1, 5),
    'missing_value_at_end_of_text': ('a =', 'syntax', 1, 4),
    'bad_boolean': ('a = tru\n', 'value', 1, 5),
    'leading_zero': ('a = 01\n', 'value', 1, 5),
    'double_underscore': ('a = 1__0\n', 'value', 1, 5),
    'trailing_underscore': ('a = 1_\n', 'value', 1, 5),
    'leading_underscore': ('a = _1\n', 'value', 1, 5),
    'signed_hex': ('a = +0x1\n', 'value', 1, 5),
    'empty_hex': ('a = 0x\n', 'value', 1, 5),
    'float_without_integer_part': ('a = .5\n', 'value', 1, 5),
    'float_without_fraction_digits': ('a = 1.\n', 'value', 1, 5),
    'dot_before_exponent': ('a = 1.e5\n', 'value', 1, 5),
    'fraction_after_exponent': ('a = 1e5.0\n', 'value', 1, 5),
    'uppercase_hex_prefix': ('a = 0X1F\n', 'value', 1, 5),
    'dates_are_not_supported': ('a = 1979-05-27\n', 'value', 1, 5),
    'token_runs_to_a_stop_character': ('a = truefalse\n', 'value', 1, 5),
    'bracket_inside_token': ('a = 1[\n', 'value', 1, 5),
    'bad_special_float': ('a = -nan\nb = +inf\nc = inf_\n', 'value', 3, 5),
    'array_missing_comma': ('x = 1\n\n  y = [1, 2 3]\n', 'syntax', 3, 13),
    'array_double_comma': ('a = [1,,2]\n', 'syntax', 1, 8),
    'bad_value_in_array': ('a = [1, tru]\n', 'value', 1, 9),
    'multiline_array_missing_comma': ('p = [\n  1,\n  2\n  3\n]\n', 'syntax', 4, 3),
    'second_array_after_value': ('a = 1 # ok\nb = [1, 2] [3]\n', 'syntax', 2, 12),
    'unclosed_array_in_inline_table': ('v = {a = 1, b = {c = [1}}\n', 'syntax', 1, 24),
    'inline_table_trailing_comma': ('a = {b=1,}\n', 'syntax', 1, 10),
    'inline_table_newline': ('a = {b=1\n', 'syntax', 1, 9),
    'inline_table_duplicate': ('a = {b=1, b=2}\n', 'duplicate', 1, 11),
    'inline_duplicate_before_bad_value': ('a = { b = 1, b = tru }\n', 'duplicate', 1, 14),
    'inline_table_frozen_inside': ('a = { x = {y=1}, x.z = 2 }\n', 'duplicate', 1, 18),
    'empty_key_part': ('a.=1\n', 'syntax', 1, 3),
    'missing_key': ('= 1\n', 'syntax', 1, 1),
    'missing_equals': ('a 1\n', 'syntax', 1, 3),
    'key_at_end_of_text': ('a', 'syntax', 1, 2),
    'bad_key_character': ('$a = 1\n', 'syntax', 1, 1),
    'non_ascii_bare_key': ('é = 1\n', 'syntax', 1, 1),
    'header_with_space_in_key': ('[a b]\n', 'syntax', 1, 4),
    'text_after_header': ('[a]x\n', 'syntax', 1, 4),
    'empty_header': ('[ ]\n', 'syntax', 1, 3),
    'array_header_brackets_apart': ('[[a] ]\n', 'syntax', 1, 5),
    'extra_closing_bracket': ('[a]]\n', 'syntax', 1, 4),
    'unterminated_string_at_newline': ('a = "abc\n', 'string', 1, 5),
    'unterminated_string_at_end': ('a = "abc', 'string', 1, 5),
    'unterminated_literal_string': ("a = 'abc\n", 'string', 1, 5),
    'unterminated_string_in_array': ('p = [\n  1, # c\n  "x\n]\n', 'string', 3, 3),
    'unterminated_multiline': ('a = """abc\n', 'string', 1, 5),
    'control_character_in_string': ('a = "tab\tok\x01"\n', 'string', 1, 12),
    'unknown_escape': ('a = "a\\qb"\n', 'escape', 1, 7),
    'short_unicode_escape': ('a = "\\u12G4"\n', 'escape', 1, 6),
    'surrogate_escape': ('a = "\\uD800"\n', 'escape', 1, 6),
    'escape_above_10ffff': ('k = "\\U00110000"\n', 'escape', 1, 6),
    'escape_in_quoted_key': ('"é" = "\\x"\n', 'escape', 1, 8),
    'backslash_space_not_before_newline': ('s = """\nok\\  x"""\n', 'escape', 2, 3),
    'too_many_closing_quotes': ('a = """a""""""\n', 'syntax', 1, 14),
    'too_many_closing_apostrophes': ("s = '''abc''''''\n", 'syntax', 1, 16),
    'text_after_string_counts_unicode_columns': ('a = "é" b\n', 'syntax', 1, 9),
    'key_twice': ('a = 1\na = 2\n', 'duplicate', 2, 1),
    'duplicate_before_bad_value': ('a = 1\na = tru\n', 'duplicate', 2, 1),
    'duplicate_before_missing_equals': ('a = 1\na 1\n', 'duplicate', 2, 1),
    'indented_duplicate_in_table': ('# top\n[t]\n  key = "v"\n  key = "w"\n', 'duplicate', 4, 3),
    'dotted_key_through_a_value': ('a = 1\na.b = 2\n', 'duplicate', 2, 1),
    'value_over_dotted_table': ('a.b = 1\na = 2\n', 'duplicate', 2, 1),
    'value_over_deeper_dotted_table': ('a.b.c = 1\na.b = 2\n', 'duplicate', 2, 1),
    'header_for_dotted_table': ('a.b = 1\n[a]\n', 'duplicate', 2, 1),
    'header_twice': ('[a]\nb = 1\n[a]\n', 'duplicate', 3, 1),
    'header_twice_before_trailing_text': ('[a]\n[a] x\n', 'duplicate', 2, 1),
    'implicit_then_explicit_twice': ('[a.b]\n[a]\n[a]\n', 'duplicate', 3, 1),
    'header_for_table_made_by_dotted_keys_in_section': ('[a]\nb.c = 1\n[a.b]\n', 'duplicate', 3, 1),
    'dotted_key_into_header_table': ('[a.b]\nc = 1\n[a]\nb.d = 2\n', 'duplicate', 4, 1),
    'dotted_key_into_inline_table': ('a = {b = 1}\na.c = 2\n', 'duplicate', 2, 1),
    'header_into_inline_table': ('a = {b = 1}\n[a.c]\n', 'duplicate', 2, 1),
    'array_of_tables_over_static_array': ('a = []\n[[a]]\n', 'duplicate', 2, 1),
    'array_of_tables_over_table': ('[a]\n[[a]]\n', 'duplicate', 2, 1),
    'table_over_array_of_tables': ('[[a]]\n[a]\n', 'duplicate', 2, 1),
    'header_into_static_array_of_inline_tables': ('a = [{b=1}]\n[a.c]\n', 'duplicate', 2, 1),
    'header_for_nested_array_of_tables': ('[[a.b]]\n[a]\n[a.b]\n', 'duplicate', 3, 1),
}


def _error_test(doc, kind, line, col):
    def test(self):
        with self.assertRaises(TOMLError, msg=repr(doc)) as cm:
            loads(doc)
        e = cm.exception
        self.assertEqual((e.kind, e.line, e.col), (kind, line, col), repr(doc))
    return test


def _valid_test(doc):
    def test(self):
        self.assertTrue(same(loads(doc), _oracle.loads(doc)), (doc, loads(doc), _oracle.loads(doc)))
    return test


class Valid(Limited):
    def test_reference_documents(self):
        for doc in SEEDS:
            self.assertTrue(same(loads(doc), _oracle.loads(doc)), doc)

    def test_types_are_plain_python(self):
        d = loads('a = 1\nb = 1.0\nc = [ {x = true} ]\n[t]\n')
        self.assertEqual([type(d[k]) for k in 'abct'], [int, float, list, dict])
        self.assertIs(type(d['c'][0]), dict)


class Errors(Limited):
    pass


# one test per valid document and per group of related invalid ones: partial credit with readable names
for _name, _doc in VALID.items():
    setattr(Valid, 'test_' + _name, _valid_test(_doc))

_ERR_GROUPS = {
    'numbers_and_tokens': ['bad_boolean', 'leading_zero', 'double_underscore', 'trailing_underscore', 'leading_underscore', 'signed_hex',
                           'empty_hex', 'float_without_integer_part', 'float_without_fraction_digits', 'dot_before_exponent',
                           'fraction_after_exponent', 'uppercase_hex_prefix', 'dates_are_not_supported', 'token_runs_to_a_stop_character',
                           'bracket_inside_token', 'bad_special_float'],
    'missing_or_extra_after_values': ['value_then_garbage', 'missing_value_at_newline', 'missing_value_at_end_of_text', 'second_array_after_value'],
    'arrays': ['array_missing_comma', 'array_double_comma', 'bad_value_in_array', 'multiline_array_missing_comma', 'unclosed_array_in_inline_table'],
    'inline_tables': ['inline_table_trailing_comma', 'inline_table_newline', 'inline_table_duplicate', 'inline_duplicate_before_bad_value',
                      'inline_table_frozen_inside'],
    'keys': ['empty_key_part', 'missing_key', 'missing_equals', 'key_at_end_of_text', 'bad_key_character', 'non_ascii_bare_key'],
    'headers': ['header_with_space_in_key', 'text_after_header', 'empty_header', 'array_header_brackets_apart', 'extra_closing_bracket'],
    'unterminated_strings': ['unterminated_string_at_newline', 'unterminated_string_at_end', 'unterminated_literal_string',
                             'unterminated_string_in_array', 'unterminated_multiline', 'control_character_in_string'],
    'escapes': ['unknown_escape', 'short_unicode_escape', 'surrogate_escape', 'escape_above_10ffff', 'escape_in_quoted_key',
                'backslash_space_not_before_newline'],
    'string_endings_and_columns': ['too_many_closing_quotes', 'too_many_closing_apostrophes', 'text_after_string_counts_unicode_columns'],
    'duplicate_keys': ['key_twice', 'duplicate_before_bad_value', 'duplicate_before_missing_equals', 'indented_duplicate_in_table'],
    'values_versus_dotted_tables': ['dotted_key_through_a_value', 'value_over_dotted_table', 'value_over_deeper_dotted_table'],
    'headers_defined_twice': ['header_for_dotted_table', 'header_twice', 'header_twice_before_trailing_text', 'implicit_then_explicit_twice',
                              'header_for_table_made_by_dotted_keys_in_section'],
    'dotted_keys_into_defined_tables': ['dotted_key_into_header_table', 'dotted_key_into_inline_table', 'header_into_inline_table'],
    'arrays_of_tables_conflicts': ['array_of_tables_over_static_array', 'array_of_tables_over_table', 'table_over_array_of_tables',
                                   'header_into_static_array_of_inline_tables', 'header_for_nested_array_of_tables'],
}
assert sorted(sum(_ERR_GROUPS.values(), [])) == sorted(ERRORS), set(ERRORS) ^ set(sum(_ERR_GROUPS.values(), []))


def _group_test(names):
    def test(self):
        for n in names:   # no subTest: the grader counts one result per test method
            _error_test(*ERRORS[n])(self)
    return test


for _g, _names in _ERR_GROUPS.items():
    setattr(Errors, 'test_' + _g, _group_test(_names))


CHARS = list('[]{}=,."\'#\n \\abc1_+-0x') + ['"""', "'''", '[[', ']]', '\n[a]\n', '\n[[a]]\n', '.a', 'a=1\n', 'true', 'inf', '\\u12']


def mutate(rnd, s):
    k = rnd.random()
    i = rnd.randrange(len(s) + 1)
    if k < 0.3 and s:
        j = min(len(s), i + rnd.randint(1, 3))
        return s[:i] + s[j:]
    if k < 0.6:
        return s[:i] + rnd.choice(CHARS) + s[i:]
    lines = s.split('\n')
    a = rnd.randrange(len(lines))
    if k < 0.8:
        lines.insert(rnd.randrange(len(lines) + 1), lines[a])
    else:
        b = rnd.randrange(len(lines))
        lines[a], lines[b] = lines[b], lines[a]
    return '\n'.join(lines)


class Fuzz(Limited):
    LIMIT = 90

    def run_seed(self, seed, n=1500):
        rnd = random.Random(seed)
        for _ in range(n):
            s = rnd.choice(SEEDS)
            for _ in range(rnd.randint(1, 3)):
                s = mutate(rnd, s)
            try:
                exp = _oracle.loads(s)
            except _oracle.TOMLDecodeError:
                exp = None
            if exp is not None and 'date' in repr(exp):
                continue
            if exp is None:
                with self.assertRaises(TOMLError, msg=repr(s)):
                    loads(s)
            else:
                try:
                    got = loads(s)
                except TOMLError as e:
                    self.fail(f'{s!r} is valid TOML but raised {e}')
                self.assertTrue(same(got, exp), (s, got, exp))

    def test_accepts_exactly_what_tomllib_accepts_1(self):
        self.run_seed(11)

    def test_accepts_exactly_what_tomllib_accepts_2(self):
        self.run_seed(12)

    def test_accepts_exactly_what_tomllib_accepts_3(self):
        self.run_seed(13)


class Rules(Limited):
    def test_no_toml_library(self):
        self.assertEqual(banned_source('toml_subset.py', {'tomllib', 'tomli', 'toml', 'tomlkit', 'pip'}), [])


if __name__ == '__main__':
    unittest.main()
