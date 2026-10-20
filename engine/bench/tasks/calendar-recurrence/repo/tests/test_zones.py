import unittest
from datetime import datetime, timedelta

from calrule import zones
from calrule.errors import UnknownZone


class Zones(unittest.TestCase):
    def test_new_york_changes(self):
        when = zones.transitions("America/New_York", 2025)
        self.assertEqual([t[0] for t in when], [datetime(2025, 3, 9, 7), datetime(2025, 11, 2, 6)])
        self.assertEqual(zones.offset_at("America/New_York", datetime(2025, 7, 1)), timedelta(hours=-4))

    def test_sydney_is_in_daylight_time_in_january(self):
        self.assertEqual(zones.offset_at("Australia/Sydney", datetime(2025, 1, 15)), timedelta(hours=11))
        self.assertEqual(zones.offset_at("Australia/Sydney", datetime(2025, 6, 15)), timedelta(hours=10))

    def test_unknown_zone(self):
        with self.assertRaises(UnknownZone):
            zones.offset_at("Mars/Olympus", datetime(2025, 1, 1))
