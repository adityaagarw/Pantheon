import unittest
from datetime import datetime

from calrule.agenda import agenda
from calrule.expand import occurrences
from calrule.ics import read_calendar
from calrule.localtime import localize
from calrule.rule import format_rule, parse_rule


class Calrule(unittest.TestCase):
    def test_gap(self):
        self.assertEqual(localize(datetime(2025, 3, 9, 2, 30), "America/New_York").isoformat(),
                         "2025-03-09T03:30:00-04:00")

    def test_rule_round_trip(self):
        self.assertEqual(format_rule(parse_rule("byday=-1fr;freq=monthly")), "FREQ=MONTHLY;BYDAY=-1FR")

    def test_last_working_day(self):
        got = occurrences(parse_rule("FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1;COUNT=3"),
                          datetime(2025, 1, 31), "UTC")
        self.assertEqual([o.date().isoformat() for o in got], ["2025-01-31", "2025-02-28", "2025-03-31"])

    def test_sample_agenda(self):
        with open("data/team.ics", encoding="utf-8") as f:
            events = read_calendar(f.read())
        z = "Europe/Berlin"
        text = agenda(events, localize(datetime(2025, 3, 17), z), localize(datetime(2025, 3, 19), z), z)
        self.assertIn("  10:00-11:30  Sprint planning (moved)", text)
