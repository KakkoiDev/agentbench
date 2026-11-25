"""Hidden tests for regex-engine: hand-picked cases, differential tests against re, speed limits."""
import random
import re

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

from rx import RegexError, compile as rx_compile, find_all, fullmatch, search

sys.setrecursionlimit(1000)   # the default; a solution that raises it still has to live with deep texts


def conv(m):
    if m is None:
        return None
    return m.span(), tuple(m.span(g) if m.span(g) != (-1, -1) else None for g in range(1, len(m.groups()) + 1))


ATOMS = ['[ab]', '[^a]', '[a-c]', r'\d', r'\w', r'\s', r'\W', r'[\w ]', '[^ab\n]', r'[\d_]', r'\S']


def gen(rnd, depth):
    """A random pattern; repeated sub-patterns never match the empty string."""
    r = rnd.random()
    if depth <= 0 or r < 0.35:
        k = rnd.random()
        if k < 0.45:
            return rnd.choice('aab')
        if k < 0.55:
            return '.'
        if k < 0.75:
            return rnd.choice(ATOMS)
        if k < 0.85:
            return rnd.choice(['^', '$', r'\b', r'\B'])
        return rnd.choice('abc')
    if r < 0.55:
        return ''.join(gen(rnd, depth - 1) for _ in range(rnd.randint(2, 3)))
    if r < 0.7:
        return '(?:' + '|'.join(gen(rnd, depth - 1) for _ in range(rnd.randint(2, 3))) + ')'
    if r < 0.85:
        return ('(' if rnd.random() < 0.7 else '(?:') + gen(rnd, depth - 1) + ')'
    p = gen(rnd, depth - 1)
    if re._parser.parse(p).getwidth()[0] == 0:
        return p if not p.endswith(('*', '+', '?', '}')) else '(?:' + p + ')'
    if not (len(p) == 1 or p in ATOMS):
        p = '(?:' + p + ')' if rnd.random() < 0.5 else '(' + p + ')'
    q = rnd.choice(['*', '+', '?', '{2}', '{1,3}', '{0,2}', '{2,}', '{,2}'])
    if rnd.random() < 0.3:
        q += '?'
    return p + q


def rtext(rnd, n=12):
    return ''.join(rnd.choice('aabbc d1_\n') for _ in range(rnd.randint(0, n)))


class Basics(Limited):
    def same(self, pattern, text):
        self.assertEqual(search(pattern, text), conv(re.search(pattern, text)), (pattern, text))

    def test_literals_and_dot(self):
        self.same('abc', 'xxabcx')
        self.same('a.c', 'a-c')
        self.same('a.c', 'a\nc')
        self.same('.', '\n\nx')
        self.same('', 'abc')

    def test_shorthand_classes_are_ascii(self):
        for p in [r'\d+', r'\w+', r'\s+', r'\D+', r'\W+', r'\S+']:
            self.same(p, 'ab_9 \t\x0b\x0c!x')

    def test_escaped_metacharacters(self):
        for p, t in [(r'\.', 'a.'), (r'\*\+\?', 'x*+?'), (r'\\', 'a\\b'), (r'\(\)', '()'), (r'\[\]', '[]'),
                     (r'\{\}', '{}'), (r'\|', 'a|b'), (r'\^\$', '^$'), (r'\-', '-'), ('\\ ', 'a b'), (r'\n\t', 'x\n\t')]:
            self.same(p, t)

    def test_classes(self):
        for p, t in [('[abc]+', 'xxcabz'), ('[^abc]+', 'abxyz'), ('[a-c]+', 'dabce'), ('[]a]+', 'x]a]'), ('[^]a]+', ']]xy'),
                     ('[-a]+', 'b-a-'), ('[a-]+', 'b-a-'), (r'[\d.]+', 'x3.14y'), (r'[^\W]+', '  ab_1 '), (r'[\]\\]+', 'a]\\b'),
                     ('[^a]', '\n'), (r'[\s,]+', 'a , \tb'), ('[a-cx-z]+', 'wabyzq'), (r'[\w-]+', ' foo-bar_1 ')]:
            self.same(p, t)

    def test_alternation_is_leftmost_first(self):
        self.same('a|ab', 'ab')
        self.same('ab|a', 'ab')
        self.same('x|a|ab', 'zab')
        self.same('(a|ab)(c|bcd)', 'abcd')
        self.same('a|', 'b')
        self.same('|a', 'a')

    def test_greedy_and_lazy(self):
        for p in ['a+', 'a+?', 'a*b', 'a*?b', 'a??', 'a?', '<.+>', '<.+?>', '(a+?)(a*)', '(a*?)(a+)$', 'x(a{2,}?)', 'x(a{1,3}?)a']:
            self.same(p, 'xaaab <i>x</i> aaaa')

    def test_counted_repeats(self):
        for p, t in [('a{3}', 'aaaa'), ('a{2,}', 'aaaaa'), ('a{2,3}', 'aaaaa'), ('a{,2}', 'aaa'), ('a{,}', 'aaa'),
                     ('(ab){2}', 'abababab'), ('(?:a|b){3,4}', 'abba'), ('a{0}b', 'ab'), ('(a){0}b', 'ab'), ('(a){2,3}?', 'aaa')]:
            self.same(p, t)

    def test_brace_literals(self):
        for p, t in [('a{x}', 'a{x}'), ('a{1,2', 'a{1,2'), ('{', 'a{'), ('a{', 'a{'), ('}', '}'), ('a{1,x}', 'a{1,x}'), ('x{ 2}', 'x{ 2}')]:
            self.same(p, t)

    def test_groups(self):
        for p, t in [('(a)(b)?(c)', 'ac'), ('((a)(b))', 'ab'), ('(?:(a)|(b))+', 'ab'), ('(?:(a)|b)+', 'ab'), ('(a|b)*c', 'abac'),
                     ('(a)|b', 'b'), ('(?:x(y)?)+', 'xyx'), ('((a)|(b))*', 'aba'), ('(a(b(c)))', 'abc'), ('()', 'x'), ('(?:)', 'x')]:
            self.same(p, t)

    def test_anchors(self):
        for p, t in [('^a', 'ba'), ('^a', 'ab'), ('a$', 'ba'), ('a$', 'a\n'), ('a$', 'a\n\n'), ('$', 'ab\n'), ('$', '\n'),
                     (r'a\Z', 'a\n'), (r'a\Z', 'a'), (r'\Aa', 'ba'), (r'^$', ''), (r'^$', '\n'), ('x$|y', 'x\ny')]:
            self.same(p, t)

    def test_word_boundaries(self):
        for p, t in [(r'\bab', 'cab ab'), (r'ab\b', 'abc ab'), (r'\Bb', 'ab b'), (r'\b', '  '), (r'\B', '  '), (r'\b', ''),
                     (r'\w+\b', 'hi there'), (r'\b_\b', 'a _ b'), (r'\b1', 'x 12')]:
            self.same(p, t)

    def test_fullmatch_takes_a_lower_priority_alternative_that_reaches_the_end(self):
        for p, t in [('a|ab', 'ab'), ('a*?', 'aaa'), ('(a+?)(a*?)', 'aaa'), ('x$', 'x\n'), ('x\n?$', 'x\n'), ('', '')]:
            self.assertEqual(fullmatch(p, t), conv(re.fullmatch(p, t)), (p, t))

    def test_find_all_empty_match_rules(self):
        for p, t in [('a*?', 'aa'), ('a|', 'ab'), (r'\b|a', 'a b'), ('a*', 'baaa'), ('', 'abc'), ('x*', ''), (r'\B', 'ab'),
                     ('(a)|b', 'abba'), ('$', 'a\n'), ('a??', 'aab')]:
            self.assertEqual(find_all(p, t), [conv(m) for m in re.finditer(p, t)], (p, t))

    def test_compile_once_use_many_times(self):
        c = rx_compile(r'(\d+)-(\d+)')
        self.assertEqual(c.search('tel 12-345'), conv(re.search(r'(\d+)-(\d+)', 'tel 12-345')))
        self.assertEqual(c.search('none'), None)
        self.assertEqual(c.fullmatch('1-2'), ((0, 3), ((0, 1), (2, 3))))

    def test_errors(self):
        for p in ['(', ')', 'a)', '(a', '((a)', '[a', '[]', '[^]', '[z-a]', 'a\\', '*a', 'a|*', '(*)', 'a**', 'a{2,1}', r'\q',
                  '(?=a)', '(?P<n>a)', '+', '?', 'a{2}{3}', 'a*?+', r'\1', r'[a-\d]', '^*', r'\b+', 'a+*', 'x{1}?*', '{2}', 'x{3,2}?']:
            with self.assertRaises(RegexError, msg=p):
                search(p, 'abc')
            with self.assertRaises(RegexError, msg=p):
                rx_compile(p)

    def test_long_texts_need_no_deep_recursion(self):
        t = 'ab' * 60000 + 'c'
        self.assertEqual(fullmatch(r'(?:ab)*c', t), ((0, len(t)), ()))
        self.assertEqual(search(r'(a|b)+c', t), ((0, len(t)), ((len(t) - 2, len(t) - 1),)))
        self.assertEqual(len(find_all('b', t)), 60000)


class Differential(Limited):
    LIMIT = 60

    def run_seed(self, seed, kind, n=400):
        rnd = random.Random(seed)
        for _ in range(n):
            p, t = gen(rnd, 4), rtext(rnd)
            if not t and r'\B' in p:   # \B in an empty text changed in Python 3.14; not tested
                t = ' '
            if kind == 'search':
                self.assertEqual(search(p, t), conv(re.search(p, t)), (p, t))
            elif kind == 'fullmatch':
                self.assertEqual(fullmatch(p, t), conv(re.fullmatch(p, t)), (p, t))
            else:
                self.assertEqual(find_all(p, t), [conv(m) for m in re.finditer(p, t)], (p, t))

    def test_random_search_1(self):
        self.run_seed(101, 'search')

    def test_random_search_2(self):
        self.run_seed(102, 'search')

    def test_random_search_3(self):
        self.run_seed(103, 'search')

    def test_random_search_4(self):
        self.run_seed(104, 'search')

    def test_random_fullmatch_1(self):
        self.run_seed(201, 'fullmatch')

    def test_random_fullmatch_2(self):
        self.run_seed(202, 'fullmatch')

    def test_random_find_all_1(self):
        self.run_seed(301, 'find_all')

    def test_random_find_all_2(self):
        self.run_seed(302, 'find_all')

    def test_random_longer_texts(self):
        rnd = random.Random(401)
        for _ in range(150):
            p, t = gen(rnd, 3), rtext(rnd, 60) or 'a'
            self.assertEqual(search(p, t), conv(re.search(p, t)), (p, t))
            self.assertEqual(find_all(p, t), [conv(m) for m in re.finditer(p, t)], (p, t))


class Speed(Limited):
    LIMIT = 120
    SETUP = 'from rx import search, fullmatch, find_all'

    def test_alternation_star_on_30000_chars(self):
        self.timed(self.SETUP, "r = search('(a|aa)*b', 'a' * 30000)", 5, 'assert r is None, r')

    def test_nested_plus_on_5000_chars(self):
        self.timed(self.SETUP, "r = search('(x+x+)+y', 'x' * 5000)", 5, 'assert r is None, r')

    def test_optional_chain(self):
        self.timed(self.SETUP, "r = [fullmatch('(?:a?){%d}a{%d}' % (n, n), 'a' * n) for n in (20, 25, 30)]", 5,
                   "assert r == [((0, 20), ()), ((0, 25), ()), ((0, 30), ())], r")

    def test_words_then_end(self):
        self.timed(self.SETUP, r"r = search(r'^(\w+\s?)+$', 'word ' * 3000 + '!')", 5, 'assert r is None, r')

    def test_find_all_on_60000_chars(self):
        self.timed(self.SETUP, r"r = find_all(r'(\w+)@(\w+)\.com', 'foo bar@baz.com qux ' * 3000)", 5,
                   "assert len(r) == 3000 and r[1] == ((24, 35), ((24, 27), (28, 31))), r[:2]")

    def test_wide_counted_repeat(self):
        self.timed(self.SETUP, r"r = search(r'(?:a|b){200}c', 'ab' * 5000)", 8, 'assert r is None, r')


class Rules(Limited):
    def test_no_regex_library(self):
        self.assertEqual(banned_source('rx.py', {'re', 'sre_compile', 'sre_parse', 'sre_constants', '_sre', 'fnmatch', 'glob',
                                                  'importlib', 'regex'}), [])


if __name__ == '__main__':
    unittest.main()
