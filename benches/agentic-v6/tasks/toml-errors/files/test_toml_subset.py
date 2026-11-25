import unittest
from toml_subset import TOMLError, loads


class TomlTest(unittest.TestCase):
    def test_simple_document(self):
        doc = 'title = "x"\n[owner]\nname = "Tom"\nports = [8000, 8001]\n'
        self.assertEqual(loads(doc), {'title': 'x', 'owner': {'name': 'Tom', 'ports': [8000, 8001]}})

    def test_error_position(self):
        with self.assertRaises(TOMLError) as cm:
            loads('a = 1\nb = [1, 2 3]\n')
        self.assertEqual((cm.exception.kind, cm.exception.line, cm.exception.col), ('syntax', 2, 11))

    def test_duplicate(self):
        with self.assertRaises(TOMLError) as cm:
            loads('[a]\nx = 1\n[a]\n')
        self.assertEqual((cm.exception.kind, cm.exception.line, cm.exception.col), ('duplicate', 3, 1))


if __name__ == '__main__':
    unittest.main()
