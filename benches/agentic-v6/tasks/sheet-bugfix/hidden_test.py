"""Hidden tests for sheet-bugfix: the five reported bugs, and regression tests of everything else."""
import random

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

from sheet import CellError, FormulaError, Workbook

E = CellError


def wb_with(**cells):
    wb = Workbook()
    for k, v in cells.items():
        wb.set(k, v)
    return wb


def val(formula, **cells):
    wb = wb_with(**cells)
    wb.set('ZZ99', formula)
    return wb.get('ZZ99')


# ======================= the reported bugs =======================
class Bug1RecalcOrder(Limited):
    def test_reported_diamond(self):
        wb = wb_with(A1='1', C1='=A1*2', B1='=C1+A1')
        wb.set('A1', '5')
        self.assertEqual((wb.get('C1'), wb.get('B1')), (10, 15))

    def test_deep_diamonds_any_entry_order(self):
        rnd = random.Random(4)
        for trial in range(30):
            names = [f'{c}{r}' for c in 'ABCDE' for r in range(1, 6)]
            rnd.shuffle(names)
            formulas = {}
            for i, n in enumerate(names):
                if i < 3:
                    formulas[n] = str(rnd.randint(1, 9))
                else:
                    a, b = rnd.sample(names[:i], 2)
                    formulas[n] = f'={a}+{b}*2-{rnd.randint(0, 3)}'
            order = list(names)
            rnd.shuffle(order)
            wb = Workbook()
            for n in order:
                wb.set(n, formulas[n])
            for step in range(3):
                k = names[rnd.randrange(3)]
                formulas[k] = str(rnd.randint(1, 9))
                wb.set(k, formulas[k])
            truth = {}
            for n in names:
                f = formulas[n]
                if f.startswith('='):
                    a, rest = f[1:].split('+')
                    b, c = rest.split('*2-')
                    truth[n] = truth[a] + truth[b] * 2 - int(c)
                else:
                    truth[n] = int(f)
            self.assertEqual({n: wb.get(n) for n in names}, truth, trial)

    def test_ranges_recalculate_after_their_members(self):
        wb = wb_with(A1='1', A2='=A1*10', A3='=SUM(A1:A2)', B1='=A3+A2')
        wb.set('A1', '2')
        self.assertEqual((wb.get('A2'), wb.get('A3'), wb.get('B1')), (20, 22, 42))

    def test_after_insert_rows(self):
        wb = wb_with(A1='1', C1='=A1*2', B1='=C1+A1')
        wb.insert_rows(1)
        self.assertEqual(wb.get('B2'), 3)
        wb.set('A2', '4')
        self.assertEqual(wb.get('B2'), 12)


class Bug2MixedReferences(Limited):
    def test_reported_copy(self):
        wb = wb_with(D1='=$A2+B$1')
        wb.copy('D1', 'F3')
        self.assertEqual(wb.formula('F3'), '=$A4+D$1')

    def test_all_four_kinds(self):
        wb = wb_with(C3='=A1 + $A1 + A$1 + $A$1')
        wb.copy('C3', 'E7')
        self.assertEqual(wb.formula('E7'), '=C5 + $A5 + C$1 + $A$1')
        wb.copy('C3', 'B2')
        self.assertEqual(wb.formula('B2'), '=#REF! + #REF! + #REF! + $A$1')

    def test_mixed_ranges_and_values(self):
        wb = wb_with(A1='1', A2='2', B1='10', B2='20', C1='=SUM($A1:A$2)')
        wb.copy('C1', 'D2')
        self.assertEqual(wb.formula('D2'), '=SUM($A2:B$2)')
        self.assertEqual(wb.get('D2'), 2 + 20)


class Bug3RangeCycles(Limited):
    def test_reported_cycle(self):
        wb = wb_with(A2='3', A1='=SUM(A2:A3)', A3='=A1+1')
        self.assertEqual((wb.get('A1'), wb.get('A3'), wb.get('A2')), (E('#CIRC!'), E('#CIRC!'), 3))

    def test_self_range(self):
        wb = wb_with(A2='5', A1='=SUM(A1:A2)', B1='=A1*2', B2='=A2*2')
        self.assertEqual((wb.get('A1'), wb.get('B1'), wb.get('B2')), (E('#CIRC!'), E('#CIRC!'), 10))

    def test_cycle_through_range_and_single_refs(self):
        wb = wb_with(A1='=B1+1', B1='=MAX(C1:C3)', C2='=A1', C1='4', D1='=C1')
        self.assertEqual([wb.get(n) for n in ('A1', 'B1', 'C2', 'C1', 'D1')], [E('#CIRC!')] * 3 + [4, 4])

    def test_breaking_the_cycle(self):
        wb = wb_with(A2='3', A1='=SUM(A2:A3)', A3='=A1+1')
        wb.set('A3', '10')
        self.assertEqual((wb.get('A1'), wb.get('A3')), (13, 10))
        wb.set('A3', '=A1')
        self.assertEqual(wb.get('A1'), E('#CIRC!'))
        wb.set('A1', '=SUM(A2:A2)')
        self.assertEqual((wb.get('A1'), wb.get('A3')), (3, 3))


class Bug4Round(Limited):
    def test_reported_values(self):
        self.assertEqual([val(f) for f in ('=ROUND(2.5)', '=ROUND(-2.5)', '=ROUND(2.675, 2)', '=ROUND(1250, -2)')], [3, -3, 2.68, 1300])

    def test_types_and_more_cases(self):
        cases = [('=ROUND(0.5)', 1, int), ('=ROUND(1.5)', 2, int), ('=ROUND(-0.5)', -1, int), ('=ROUND(1.005, 2)', 1.01, float),
                 ('=ROUND(-1.005, 2)', -1.01, float), ('=ROUND(1234.5678, -1)', 1230, int), ('=ROUND(-1250, -2)', -1300, int),
                 ('=ROUND(7, 1)', 7.0, float), ('=ROUND(0.125, 2)', 0.13, float), ('=ROUND(A1, 1)', 2.4, float),
                 ('=ROUND(2.4, 0)', 2, int), ('=ROUND(2.25, 1)', 2.3, float), ('=ROUND(149, -2)', 100, int), ('=ROUND(150.5, -2)', 200, int)]
        for f, exp, typ in cases:
            got = val(f, A1='2.35')
            self.assertEqual((got, type(got)), (exp, typ), f)


class Bug5DeleteRows(Limited):
    def sheet(self):
        wb = Workbook()
        for i in range(1, 8):
            wb.set(f'A{i}', str(i))
        return wb

    def test_reported_reference(self):
        wb = self.sheet()
        wb.set('B2', '=A3+A6')
        wb.delete_rows(3, 2)
        self.assertEqual((wb.formula('B2'), wb.get('B2')), ('=#REF!+A4', E('#REF!')))

    def test_row_right_after_the_block(self):
        wb = self.sheet()
        wb.set('B1', '=A5*10')
        wb.set('B9', '=$A$5+A7+A2')
        wb.delete_rows(3, 2)
        self.assertEqual((wb.formula('B1'), wb.get('B1')), ('=A3*10', 50))
        self.assertEqual((wb.formula('B7'), wb.get('B7')), ('=$A$3+A5+A2', 5 + 7 + 2))

    def test_ranges_shrink(self):
        wb = self.sheet()
        wb.set('B1', '=SUM(A2:A6)')
        wb.set('C1', '=SUM(A3:A7)')
        wb.set('D1', '=SUM(A1:A4)')
        wb.set('E1', '=SUM(A3:A4)')
        wb.set('F1', '=SUM(A6:A2) + COUNT( A3 : A3 )')
        wb.delete_rows(3, 2)
        self.assertEqual([wb.formula(c) for c in ('B1', 'C1', 'D1', 'E1', 'F1')],
                         ['=SUM(A2:A4)', '=SUM(A3:A5)', '=SUM(A1:A2)', '=SUM(#REF!)', '=SUM(A4:A2) + COUNT( #REF! )'])
        self.assertEqual([wb.get(c) for c in ('B1', 'C1', 'D1', 'E1', 'F1')], [13, 18, 3, E('#REF!'), 13])   # COUNT skips the error

    def test_delete_one_row_and_cells_move(self):
        wb = self.sheet()
        wb.set('C7', '=A6+A7')
        wb.delete_rows(1)
        self.assertEqual(wb.contents()['C6'], '=A5+A6')
        self.assertEqual((wb.get('A1'), wb.get('C6')), (2, 13))
        self.assertEqual(wb.get('A7'), None)


# ======================= regression tests (pass before and after the fix) =======================
class Grammar(Limited):
    def test_precedence(self):
        cases = {'=1+2*3': 7, '=(1+2)*3': 9, '=2^3^2': 64.0, '=-2^2': 4.0, '=2*-3': -6, '=10-4-3': 3, '=8/4/2': 1.0,
                 '=50%': 0.5, '=200%*3': 6.0, '=1+2&3': '33', '=1+1=2': True, '="a"&"b"="AB"': True, '=--3': 3, '=+4': 4,
                 '=1.5e2': 150.0, '=.5+.5': 1.0, '=2*3^2': 18.0}
        for f, exp in cases.items():
            got = val(f)
            self.assertEqual((got, type(got)), (exp, type(exp)), f)

    def test_strings_and_booleans(self):
        self.assertEqual(val('="say ""hi"""'), 'say "hi"')
        self.assertEqual(val('=TRUE'), True)
        self.assertEqual(val('=true&false'), 'TRUEFALSE')
        self.assertEqual(val('=LEN("abc")'), 3)

    def test_case_insensitive_names_and_refs(self):
        self.assertEqual(val('=sum(a1:A2)+Abs(-b1)', A1='1', A2='2', B1='3'), 6)

    def test_bad_formulas_raise_and_keep_the_cell(self):
        wb = wb_with(A1='5')
        for bad in ('=1+', '=(1', '=SUM(1', '=1 2', '=A1:', '=@', '=', '=1,2', '=$1', '=SUM(A1:)'):
            with self.assertRaises(FormulaError, msg=bad):
                wb.set('A1', bad)
            self.assertEqual((wb.formula('A1'), wb.get('A1')), ('5', 5))


class Values(Limited):
    def test_literals(self):
        wb = wb_with(A1='42', A2='3.5', A3='hello', A4='TRUE', A5="'=1+1", A6=' 7 ', A7='1e3', A8='-0.25', A9="'42")
        self.assertEqual([wb.get(f'A{i}') for i in range(1, 10)], [42, 3.5, 'hello', True, '=1+1', 7, 1000.0, -0.25, '42'])
        self.assertEqual(wb.formula('A5'), "'=1+1")

    def test_empty_cells(self):
        wb = wb_with(A1='=B1', A2='=B1+1', A3='=B1&"x"', A4='=SUM(B1:B5)', A5='=COUNT(B1:B5)')
        self.assertEqual([wb.get(f'A{i}') for i in range(1, 6)], [0, 1, 'x', 0, 0])
        self.assertIsNone(wb.get('B1'))
        wb.set('A1', '')
        self.assertEqual((wb.get('A1'), wb.formula('A1')), (None, ''))

    def test_coercions(self):
        wb = wb_with(A1='5', A2="'7", A3='abc', A4='TRUE')
        self.assertEqual(val('=A1+A2', A1='5', A2="'7"), 12)
        self.assertEqual(val('=A3+1', A3='abc'), E('#VALUE!'))
        self.assertEqual(val('=A4+1', A4='TRUE'), 2)
        self.assertEqual(val('=1.0&"|"&2.5&"|"&(1=1)'), '1|2.5|TRUE')
        self.assertEqual(wb.get('A2'), '7')

    def test_comparisons(self):
        cases = {'="abc"="ABC"': True, '="a"<"B"': True, '=1<"a"': True, '="z"<TRUE': True, '=2>=2': True,
                 '=1<>1': False, '=B9=0': True, '=B9=""': True, '=FALSE<TRUE': True}
        for f, exp in cases.items():
            self.assertEqual(val(f), exp, f)

    def test_errors_propagate_leftmost_first(self):
        self.assertEqual(val('=1/0+FOO(1)'), E('#DIV/0!'))
        self.assertEqual(val('=FOO(1)+1/0'), E('#NAME?'))
        self.assertEqual(val('=SUM(A1:A3)', A1='1', A2='=1/0', A3='=FOO()'), E('#DIV/0!'))
        self.assertEqual(val('=A1:A2+1'), E('#VALUE!'))
        self.assertEqual(val('=#REF!+1'), E('#REF!'))
        self.assertEqual(val('=(-8)^0.5'), E('#NUM!'))
        self.assertEqual(val('=0^-1'), E('#DIV/0!'))
        self.assertEqual(str(val('=1/0')), '#DIV/0!')


class Functions(Limited):
    def test_aggregates(self):
        cells = dict(A1='1', A2='2.5', A3='text', A4='TRUE', A5='', B1='=A1*4')
        self.assertEqual(val('=SUM(A1:A5, B1, "3")', **cells), 1 + 2.5 + 4 + 3)
        self.assertEqual(val('=AVERAGE(A1:A5)', **cells), 1.75)
        self.assertEqual(val('=MIN(A1:A5)', **cells), 1)
        self.assertEqual(val('=MAX(A1:B5)', **cells), 4)
        self.assertEqual(val('=COUNT(A1:A5, 7, "8", TRUE)', **cells), 3)
        self.assertEqual(val('=AVERAGE(A3:A5)', **cells), E('#DIV/0!'))
        self.assertEqual(val('=MAX(A3:A5)', **cells), 0)
        self.assertEqual(val('=SUM("x")'), E('#VALUE!'))
        self.assertEqual(val('=SUM(TRUE, 1)'), 2)

    def test_logic(self):
        self.assertEqual(val('=IF(1, "y", "n")'), 'y')
        self.assertEqual(val('=IF(0, "y")'), False)
        self.assertEqual(val('=IF(TRUE, 1, 1/0)'), 1)
        self.assertEqual(val('=IF("x", 1, 2)'), E('#VALUE!'))
        self.assertEqual(val('=IFERROR(1/0, "safe")'), 'safe')
        self.assertEqual(val('=IFERROR(5, 1/0)'), 5)
        self.assertEqual(val('=AND(1, TRUE, "true")'), True)
        self.assertEqual(val('=OR(0, FALSE)'), False)
        self.assertEqual(val('=NOT(B1)'), True)
        self.assertEqual(val('=AND(1, 1/0)'), E('#DIV/0!'))

    def test_other_functions_and_arity(self):
        self.assertEqual(val('=CONCAT("a", A1:B1, 2, TRUE)', A1='x', B1='1.50'), 'ax1.52TRUE')
        self.assertEqual(val('=ABS(-2.5)'), 2.5)
        self.assertEqual(val('=ROUND(3.7)'), 4)
        self.assertEqual(val('=ABS(1, 2)'), E('#VALUE!'))
        self.assertEqual(val('=IF(1)'), E('#VALUE!'))
        self.assertEqual(val('=NOPE()'), E('#NAME?'))


class CopyInsert(Limited):
    def test_copy_relative_and_absolute(self):
        wb = wb_with(A1='1', B1='2', A2='=A1+B1*$A$1')
        wb.copy('A2', 'B3')
        self.assertEqual(wb.formula('B3'), '=B2+C2*$A$1')
        wb.copy('A1', 'D4')
        self.assertEqual(wb.formula('D4'), '1')
        wb.copy('Q9', 'A1')
        self.assertEqual((wb.formula('A1'), wb.get('A1')), ('', None))

    def test_copy_off_the_sheet(self):
        wb = wb_with(B2='=A1*2+SUM(A1:B2)')
        wb.copy('B2', 'A1')
        self.assertEqual(wb.formula('A1'), '=#REF!*2+SUM(#REF!)')
        self.assertEqual(wb.get('A1'), E('#REF!'))

    def test_copy_keeps_spacing_and_case_of_other_tokens(self):
        wb = wb_with(C1='=  sum( a1 , $B$2 )+ "A1"')
        wb.copy('C1', 'C2')
        self.assertEqual(wb.formula('C2'), '=  sum( A2 , $B$2 )+ "A1"')

    def test_insert_rows(self):
        wb = wb_with(A1='1', A2='2', A3='3', B1='=SUM(A1:A3)', B5='=$A$3+A2', C2='=A1')
        wb.insert_rows(2, 2)
        self.assertEqual(wb.contents(), {'A1': '1', 'B1': '=SUM(A1:A5)', 'A4': '2', 'C4': '=A1', 'A5': '3', 'B7': '=$A$5+A4'})
        self.assertEqual((wb.get('B1'), wb.get('B7'), wb.get('C4')), (6, 5, 1))
        wb.insert_rows(10)
        self.assertEqual(wb.formula('B7'), '=$A$5+A4')

    def test_delete_rows_above_and_below_only(self):
        wb = wb_with(A1='1', A2='=A1*2', A9='9', B9='=A9+A1')
        wb.delete_rows(5, 2)
        self.assertEqual((wb.formula('A2'), wb.formula('B7'), wb.get('B7')), ('=A1*2', '=A7+A1', 10))

    def test_bad_row_arguments(self):
        wb = Workbook()
        for call in (lambda: wb.insert_rows(0), lambda: wb.delete_rows(1, 0), lambda: wb.get('A0'), lambda: wb.set('$A$1', '1'),
                     lambda: wb.get('1A')):
            with self.assertRaises(ValueError):
                call()


class CyclesAndChains(Limited):
    LIMIT = 60

    def test_cycles_through_single_references(self):
        wb = wb_with(A1='=B1+1', B1='=A1', C1='=A1*2', D1='5')
        self.assertEqual([wb.get(n) for n in ('A1', 'B1', 'C1', 'D1')], [E('#CIRC!')] * 3 + [5])
        wb.set('B1', '1')
        self.assertEqual([wb.get(n) for n in ('A1', 'B1', 'C1')], [2, 1, 4])
        wb.set('E1', '=E1')
        self.assertEqual(wb.get('E1'), E('#CIRC!'))

    def test_long_chain(self):
        wb = Workbook()
        wb.set('A1', '1')
        for i in range(2, 4001):
            wb.set(f'A{i}', f'=A{i - 1}+1')
        self.assertEqual(wb.get('A4000'), 4000)
        wb.set('A1', '10')
        self.assertEqual(wb.get('A4000'), 4009)
        wb.set('B1', '=SUM(A1:A4000)')
        self.assertEqual(wb.get('B1'), sum(range(10, 4010)))
        wb.insert_rows(1)
        self.assertEqual((wb.get('A4001'), wb.formula('A4001')), (4009, '=A4000+1'))


if __name__ == '__main__':
    unittest.main()
