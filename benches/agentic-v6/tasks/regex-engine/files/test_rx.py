import unittest
from rx import RegexError, find_all, fullmatch, search


class RxTest(unittest.TestCase):
    def test_search(self):
        self.assertEqual(search('b+', 'abbc'), ((1, 3), ()))
        self.assertIsNone(search('x', 'abc'))

    def test_groups(self):
        self.assertEqual(search('(a)(x)?b', 'cab'), ((1, 3), ((1, 2), None)))

    def test_fullmatch_and_find_all(self):
        self.assertEqual(fullmatch('a|ab', 'ab'), ((0, 2), ()))
        self.assertEqual(find_all('a*?', 'aa'), [((0, 0), ()), ((0, 1), ()), ((1, 1), ()), ((1, 2), ()), ((2, 2), ())])

    def test_error(self):
        with self.assertRaises(RegexError):
            search('(a', 'a')


if __name__ == '__main__':
    unittest.main()
