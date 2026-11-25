import unittest
from datetime import datetime, timezone

from cron import CronError, fires_between, next_fire

UTC = timezone.utc


class CronTest(unittest.TestCase):
    def test_every_quarter_hour(self):
        t = next_fire('*/15 * * * *', datetime(2025, 1, 1, 10, 7, tzinfo=UTC), 'UTC')
        self.assertEqual(str(t), '2025-01-01 10:15:00+00:00')

    def test_spring_forward_gap(self):
        t = next_fire('30 2 * * *', datetime(2025, 3, 9, 5, 0, tzinfo=UTC), 'America/New_York')
        self.assertEqual(str(t), '2025-03-09 03:00:00-04:00')

    def test_fires_between(self):
        got = fires_between('0 9 * * MON-FRI', datetime(2025, 1, 3, tzinfo=UTC), datetime(2025, 1, 8, tzinfo=UTC), 'Europe/Berlin')
        self.assertEqual([str(t) for t in got], ['2025-01-03 09:00:00+01:00', '2025-01-06 09:00:00+01:00', '2025-01-07 09:00:00+01:00'])

    def test_invalid(self):
        with self.assertRaises(CronError):
            next_fire('60 * * * *', datetime(2025, 1, 1, tzinfo=UTC), 'UTC')


if __name__ == '__main__':
    unittest.main()
