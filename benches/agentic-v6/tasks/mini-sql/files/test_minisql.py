import unittest
from minisql import SQLError, execute

TABLES = {
    'emp': {'columns': ['id', 'name', 'dept', 'salary'],
            'rows': [(1, 'Ann', 10, 5000), (2, 'Bob', 20, 4000), (3, 'Cid', 10, None), (4, 'Dee', None, 3000)]},
    'dept': {'columns': ['id', 'title'], 'rows': [(10, 'Sales'), (20, 'IT'), (30, 'Ops')]},
}


class MiniSqlTest(unittest.TestCase):
    def test_select_where(self):
        self.assertEqual(execute('SELECT name FROM emp WHERE salary > 3500', TABLES), (['name'], [('Ann',), ('Bob',)]))

    def test_join_group(self):
        cols, rows = execute('SELECT d.title, COUNT(*) AS n FROM emp e JOIN dept d ON e.dept = d.id GROUP BY d.title ORDER BY n DESC', TABLES)
        self.assertEqual(cols, ['title', 'n'])
        self.assertEqual(rows, [('Sales', 2), ('IT', 1)])

    def test_null_logic(self):
        self.assertEqual(execute('SELECT id FROM emp WHERE NOT (salary < 4500)', TABLES)[1], [(1,)])

    def test_errors(self):
        with self.assertRaises(SQLError):
            execute('SELECT id FROM emp e JOIN dept d ON e.dept = d.id', TABLES)


if __name__ == '__main__':
    unittest.main()
