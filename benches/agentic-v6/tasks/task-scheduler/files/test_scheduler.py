import unittest
from scheduler import Scheduler


class SchedulerTest(unittest.TestCase):
    def test_dependencies_and_results(self):
        order = []
        s = Scheduler(workers=1)
        s.add('b', lambda ctx: order.append('b') or 2, deps=['a'])
        s.add('a', lambda ctx: order.append('a') or 1)
        self.assertEqual(s.run(), {'b': ('ok', 2), 'a': ('ok', 1)})
        self.assertEqual(order, ['a', 'b'])

    def test_failure_skips_dependents(self):
        def boom(ctx):
            raise KeyError('x')
        s = Scheduler(workers=2)
        s.add('a', boom)
        s.add('b', lambda ctx: 1, deps=['a'])
        self.assertEqual(s.run(), {'a': ('error', "KeyError('x')"), 'b': ('skipped', 'a')})


if __name__ == '__main__':
    unittest.main()
