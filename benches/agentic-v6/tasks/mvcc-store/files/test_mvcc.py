import unittest
from mvcc import Conflict, Store, TxnClosed


class MvccTest(unittest.TestCase):
    def test_snapshot_isolation(self):
        s = Store()
        a = s.begin()
        a.put('x', 1)
        self.assertEqual(a.commit(), 1)
        r = s.begin()
        w = s.begin()
        w.put('x', 2)
        w.commit()
        self.assertEqual(r.get('x'), 1)
        self.assertEqual(s.get('x'), 2)

    def test_first_committer_wins(self):
        s = Store()
        a, b = s.begin(), s.begin()
        a.put('k', 'a')
        b.put('k', 'b')
        a.commit()
        with self.assertRaises(Conflict) as cm:
            b.commit()
        self.assertEqual(cm.exception.key, 'k')
        with self.assertRaises(TxnClosed):
            b.get('k')


if __name__ == '__main__':
    unittest.main()
