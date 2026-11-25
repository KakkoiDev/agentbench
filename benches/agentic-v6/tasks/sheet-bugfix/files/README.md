# sheet

A small spreadsheet engine used by our reporting tools. Pure Python 3 standard library.

```python
from sheet import Workbook
wb = Workbook()
wb.set('A1', '10')
wb.set('A2', '=A1*2 + SUM(B1:B3)')
wb.get('A2')        # 20
wb.formula('A2')    # '=A1*2 + SUM(B1:B3)'
wb.copy('A2', 'C5') # relative references move with the copy, $-absolute parts stay
wb.insert_rows(2)   # formulas are rewritten so they keep pointing at the same cells
wb.delete_rows(3, 2)
```

Modules: `refs` (A1 references and how they move), `tokenizer`, `parser` (formula grammar and precedence),
`values` (value types, errors, coercions, comparisons), `functions` (built-ins), `evaluator`, `graph`
(dependencies and recalculation order), `workbook` (the public API). Each module's docstrings describe its
rules; the behaviour they describe is relied upon by other teams, so keep it.

Run the tests with `python3 -m unittest`.
