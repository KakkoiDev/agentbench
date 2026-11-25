import unittest
from sheet import CellError, FormulaError, Workbook


class SheetTest(unittest.TestCase):
    def test_values_and_formulas(self):
        wb = Workbook()
        wb.set('A1', '10')
        wb.set('A2', '=A1*2 + SUM(B1:B3)')
        wb.set('B2', '5')
        self.assertEqual(wb.get('A2'), 25)
        wb.set('A1', '1')
        self.assertEqual(wb.get('A2'), 7)

    def test_errors(self):
        wb = Workbook()
        wb.set('A1', '=1/0')
        wb.set('A2', '=A1+1')
        self.assertEqual(wb.get('A2'), CellError('#DIV/0!'))
        with self.assertRaises(FormulaError):
            wb.set('A3', '=1+')

    def test_copy_relative(self):
        wb = Workbook()
        wb.set('A1', '=B1+$C$1')
        wb.copy('A1', 'A2')
        self.assertEqual(wb.formula('A2'), '=B2+$C$1')

    def test_insert_rows(self):
        wb = Workbook()
        wb.set('A1', '=A3')
        wb.set('A3', '7')
        wb.insert_rows(2)
        self.assertEqual((wb.formula('A1'), wb.get('A1')), ('=A4', 7))


if __name__ == '__main__':
    unittest.main()
