import unittest
from book import Book


class BookTest(unittest.TestCase):
    def test_rest_and_trade(self):
        b = Book()
        self.assertEqual(b.submit({'id': 's1', 'side': 'sell', 'type': 'limit', 'qty': 5, 'price': '100.5'}),
                         [('accepted', 's1'), ('rested', 's1', '100.50', 5)])
        self.assertEqual(b.submit({'id': 'b1', 'side': 'buy', 'type': 'limit', 'qty': 8, 'price': '101'}),
                         [('accepted', 'b1'), ('trade', 'b1', 's1', '100.50', 5), ('rested', 'b1', '101.00', 3)])
        self.assertEqual(b.depth('buy'), [('101.00', 3, 1)])
        self.assertEqual(b.last_price, '100.50')

    def test_invalid(self):
        b = Book()
        self.assertEqual(b.submit({'id': 'x', 'side': 'buy', 'type': 'limit', 'qty': 0, 'price': '1'}), [('rejected', 'x', 'invalid')])


if __name__ == '__main__':
    unittest.main()
