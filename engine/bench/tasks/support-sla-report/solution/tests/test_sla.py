import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sla.business import minutes
from sla.report import build, measure
from sla.rules import key, mark_duplicates, priority

UTC = timezone.utc


class Business(unittest.TestCase):
    def test_weekend_and_holiday(self):
        start = datetime(2026, 5, 22, 21, tzinfo=UTC)  # Friday 16:00 in Americas
        end = datetime(2026, 5, 26, 14, tzinfo=UTC)  # Tuesday 09:00, Monday is a holiday
        self.assertEqual(minutes(start, end, "Americas"), 120)

    def test_reversed_is_zero(self):
        now = datetime(2026, 3, 10, 12, tzinfo=UTC)
        self.assertEqual(minutes(now, now - timedelta(hours=1), "EMEA"), 0)


class Rules(unittest.TestCase):
    def test_whole_words(self):
        self.assertEqual(priority({"subject": "Download is slow"}, "business"), "normal")
        self.assertEqual(priority({"subject": "outage"}, "starter"), "high")

    def test_key(self):
        self.assertEqual(key("Re: FWD: Hello  world!"), "hello world")

    def test_duplicates_compare_with_kept_tickets_only(self):
        t0 = datetime(2026, 3, 2, tzinfo=UTC)
        tickets = [{"id": "a", "customer": "c", "subject": "X", "created": t0},
                   {"id": "b", "customer": "c", "subject": "re: x", "created": t0 + timedelta(hours=30)},
                   {"id": "c", "customer": "c", "subject": "x", "created": t0 + timedelta(hours=60)}]
        self.assertEqual(mark_duplicates(tickets), {"b"})


class Measure(unittest.TestCase):
    def test_pending_time_does_not_count(self):
        day = datetime(2026, 3, 10, 10, tzinfo=timezone(timedelta(hours=1)))  # EMEA, Tuesday
        ticket = {"created": day, "events": [
            {"type": "first_response", "at": day + timedelta(minutes=10)},
            {"type": "status", "status": "pending_customer", "at": day + timedelta(hours=1)},
            {"type": "status", "status": "open", "at": day + timedelta(hours=3)},
            {"type": "status", "status": "solved", "at": day + timedelta(hours=4)}]}
        m = measure(ticket, "EMEA", False, day + timedelta(days=5))
        self.assertEqual((m["first"], m["resolution"]), (600, 2 * 3600))


class Report(unittest.TestCase):
    def test_builds(self):
        report = build(Path(__file__).resolve().parent.parent / "data")
        self.assertTrue(report["weeks"])


if __name__ == "__main__":
    unittest.main()
