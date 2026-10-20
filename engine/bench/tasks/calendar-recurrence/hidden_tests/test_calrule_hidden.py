import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from calrule.agenda import agenda, instances
from calrule.cli import main
from calrule.errors import CalendarError, RuleError, UnknownZone
from calrule.expand import occurrences
from calrule.ics import read_calendar
from calrule.localtime import from_utc, localize, to_utc
from calrule.rule import format_rule, parse_rule

DATA = Path(__file__).resolve().parent / "data"
UTC = timezone.utc
NY, BER, SYD = "America/New_York", "Europe/Berlin", "Australia/Sydney"


def dt(*a):
    return datetime(*a)


def z(*a):
    """An aware UTC datetime."""
    return datetime(*a, tzinfo=UTC)


def occ(rule, start, zone="UTC", **kw):
    r = parse_rule(rule) if isinstance(rule, str) else rule
    kw.setdefault("limit", 50)
    return [o.isoformat() for o in occurrences(r, start, zone, **kw)]


def days(rule, start, zone="UTC", **kw):
    return [o[:10] for o in occ(rule, start, zone, **kw)]


def cal(*events):
    body = "".join(f"BEGIN:VEVENT\n{e.strip()}\nEND:VEVENT\n" for e in events)
    return read_calendar(f"BEGIN:VCALENDAR\nVERSION:2.0\n{body}END:VCALENDAR\n")


class LocalTime(unittest.TestCase):
    def test_a_gap_is_read_with_the_offset_before_it(self):
        self.assertEqual(to_utc(dt(2025, 3, 9, 2, 30), NY), dt(2025, 3, 9, 7, 30))
        self.assertEqual(localize(dt(2025, 3, 9, 2, 30), NY).isoformat(), "2025-03-09T03:30:00-04:00")
        self.assertEqual(localize(dt(2025, 3, 30, 2, 30), BER).isoformat(), "2025-03-30T03:30:00+02:00")

    def test_an_overlap_is_the_first_instant(self):
        self.assertEqual(to_utc(dt(2025, 11, 2, 1, 30), NY), dt(2025, 11, 2, 5, 30))
        self.assertEqual(localize(dt(2025, 11, 2, 1, 30), NY).isoformat(), "2025-11-02T01:30:00-04:00")
        self.assertEqual(to_utc(dt(2025, 4, 6, 2, 30), SYD), dt(2025, 4, 5, 15, 30))
        self.assertEqual(to_utc(dt(2025, 10, 26, 2, 30), BER), dt(2025, 10, 26, 0, 30))

    def test_ordinary_times_and_fixed_offsets(self):
        self.assertEqual(to_utc(dt(2025, 7, 1, 12), NY), dt(2025, 7, 1, 16))
        self.assertEqual(to_utc(dt(2025, 7, 1, 12), SYD), dt(2025, 7, 1, 2))
        k = from_utc(dt(2025, 1, 1), "Asia/Kolkata")
        self.assertEqual(k.isoformat(), "2025-01-01T05:30:00+05:30")
        self.assertEqual(k.utcoffset(), timedelta(hours=5, minutes=30))

    def test_unknown_zone(self):
        with self.assertRaises(UnknownZone):
            to_utc(dt(2025, 1, 1), "Mars/Olympus")


class Rules(unittest.TestCase):
    def test_parts_in_any_order_and_case(self):
        r = parse_rule("RRULE:freq=monthly;byday=2TU,-1fr;count=6;interval=2")
        self.assertEqual((r.freq, r.interval, r.count, r.until), ("MONTHLY", 2, 6, None))
        self.assertEqual(r.byday, ((2, 1), (-1, 4)))
        self.assertEqual((r.bymonthday, r.bymonth, r.bysetpos, r.wkst), ((), (), (), 0))
        r = parse_rule("FREQ=YEARLY;BYMONTH=3,1;BYMONTHDAY=-1,+15;BYSETPOS=-1;WKST=SU;BYDAY=MO")
        self.assertEqual((r.bymonth, r.bymonthday, r.bysetpos, r.wkst, r.byday),
                         ((3, 1), (-1, 15), (-1,), 6, ((None, 0),)))

    def test_until_a_date_or_a_utc_time(self):
        self.assertEqual(parse_rule("FREQ=DAILY;UNTIL=20250331").until, date(2025, 3, 31))
        u = parse_rule("FREQ=DAILY;UNTIL=20250331T235959Z").until
        self.assertEqual(u, z(2025, 3, 31, 23, 59, 59))
        self.assertIsNotNone(u.tzinfo)

    def test_errors_name_the_part(self):
        cases = {
            "INTERVAL=2": "FREQ", "FREQ=HOURLY": "FREQ", "FREQ=DAILY;COUNT=0": "COUNT",
            "FREQ=DAILY;FOO=1": "FOO", "FREQ=DAILY;COUNT=2;COUNT=3": "COUNT",
            "FREQ=DAILY;UNTIL=20250101T000000": "UNTIL", "FREQ=WEEKLY;BYDAY=2MO": "BYDAY",
            "FREQ=MONTHLY;BYDAY=0MO": "BYDAY", "FREQ=YEARLY;BYDAY=54MO": "BYDAY",
            "FREQ=DAILY;BYDAY=-1FR": "BYDAY", "FREQ=WEEKLY;BYMONTHDAY=1": "BYMONTHDAY",
            "FREQ=MONTHLY;BYSETPOS=1": "BYSETPOS", "FREQ=MONTHLY;BYMONTHDAY=32": "BYMONTHDAY",
            "FREQ=MONTHLY;BYMONTHDAY=0": "BYMONTHDAY", "FREQ=YEARLY;BYMONTH=13": "BYMONTH",
            "FREQ=DAILY;WKST=XX": "WKST", "FREQ=DAILY;INTERVAL=two": "INTERVAL",
            "FREQ=DAILY;BYDAY=MO,XY": "BYDAY",
        }
        for text, part in cases.items():
            with self.subTest(text=text):
                with self.assertRaises(RuleError) as cm:
                    parse_rule(text)
                self.assertTrue(str(cm.exception).startswith(part), str(cm.exception))
                self.assertIsInstance(cm.exception, ValueError)

    def test_count_and_until_together(self):
        with self.assertRaises(RuleError) as cm:
            parse_rule("FREQ=DAILY;COUNT=2;UNTIL=20250101")
        self.assertTrue(str(cm.exception).startswith(("COUNT", "UNTIL")))

    def test_format_in_canonical_order(self):
        self.assertEqual(format_rule(parse_rule("WKST=SU;BYDAY=+3WE,-1FR;freq=monthly;interval=1;count=4")),
                         "FREQ=MONTHLY;COUNT=4;BYDAY=3WE,-1FR;WKST=SU")
        self.assertEqual(format_rule(parse_rule(
            "FREQ=YEARLY;UNTIL=20300101T000000Z;BYMONTH=3,1;BYMONTHDAY=-1;BYSETPOS=-1;INTERVAL=2;WKST=MO")),
            "FREQ=YEARLY;INTERVAL=2;UNTIL=20300101T000000Z;BYMONTH=3,1;BYMONTHDAY=-1;BYSETPOS=-1")
        self.assertEqual(format_rule(parse_rule("FREQ=DAILY;UNTIL=20250331")), "FREQ=DAILY;UNTIL=20250331")


class Expand(unittest.TestCase):
    def test_wall_clock_time_across_a_change(self):
        self.assertEqual(occ("FREQ=DAILY;COUNT=4", dt(2025, 3, 7, 9), NY), [
            "2025-03-07T09:00:00-05:00", "2025-03-08T09:00:00-05:00",
            "2025-03-09T09:00:00-04:00", "2025-03-10T09:00:00-04:00"])

    def test_a_time_in_the_gap_moves_forward_that_day_only(self):
        self.assertEqual(occ("FREQ=DAILY;COUNT=3", dt(2025, 3, 8, 2, 30), NY), [
            "2025-03-08T02:30:00-05:00", "2025-03-09T03:30:00-04:00", "2025-03-10T02:30:00-04:00"])

    def test_an_overlap_time_is_its_first_instant(self):
        self.assertEqual(occ("FREQ=WEEKLY;COUNT=2", dt(2025, 10, 26, 1, 30), NY), [
            "2025-10-26T01:30:00-04:00", "2025-11-02T01:30:00-04:00"])

    def test_the_start_comes_first_and_counts(self):
        self.assertEqual(days("FREQ=WEEKLY;BYDAY=MO;COUNT=3", dt(2025, 1, 1, 10)),
                         ["2025-01-01", "2025-01-06", "2025-01-13"])

    def test_month_days_are_skipped_not_moved(self):
        self.assertEqual(days("FREQ=MONTHLY;COUNT=5", dt(2025, 1, 31)),
                         ["2025-01-31", "2025-03-31", "2025-05-31", "2025-07-31", "2025-08-31"])
        self.assertEqual(days("FREQ=MONTHLY;BYMONTHDAY=30;COUNT=3", dt(2025, 1, 30)),
                         ["2025-01-30", "2025-03-30", "2025-04-30"])

    def test_the_last_day_of_the_month(self):
        self.assertEqual(days("FREQ=MONTHLY;BYMONTHDAY=-1;COUNT=4", dt(2024, 1, 31)),
                         ["2024-01-31", "2024-02-29", "2024-03-31", "2024-04-30"])

    def test_nth_and_last_weekdays_of_a_month(self):
        self.assertEqual(days("FREQ=MONTHLY;BYDAY=2TU,-1FR;COUNT=5", dt(2025, 1, 14, 18)),
                         ["2025-01-14", "2025-01-31", "2025-02-11", "2025-02-28", "2025-03-11"])

    def test_bysetpos_last_working_day(self):
        self.assertEqual(days("FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1;COUNT=4", dt(2025, 1, 31)),
                         ["2025-01-31", "2025-02-28", "2025-03-31", "2025-04-30"])

    def test_bysetpos_out_of_range_and_twice(self):
        self.assertEqual(days("FREQ=MONTHLY;BYMONTHDAY=1,2;BYSETPOS=1,5,-2;COUNT=3", dt(2025, 1, 1)),
                         ["2025-01-01", "2025-02-01", "2025-03-01"])

    def test_weeks_begin_on_wkst(self):
        rule = "FREQ=WEEKLY;INTERVAL=2;COUNT=4;BYDAY=TU,SU"
        self.assertEqual(days(rule + ";WKST=MO", dt(1997, 8, 5, 9)),
                         ["1997-08-05", "1997-08-10", "1997-08-19", "1997-08-24"])
        self.assertEqual(days(rule + ";WKST=SU", dt(1997, 8, 5, 9)),
                         ["1997-08-05", "1997-08-17", "1997-08-19", "1997-08-31"])

    def test_weekly_interval_on_the_start_weekday(self):
        self.assertEqual(days("FREQ=WEEKLY;INTERVAL=3;COUNT=3", dt(2025, 1, 1)),
                         ["2025-01-01", "2025-01-22", "2025-02-12"])

    def test_monthly_interval_with_an_ordinal(self):
        self.assertEqual(days("FREQ=MONTHLY;INTERVAL=2;BYDAY=1MO;COUNT=3", dt(2025, 1, 6)),
                         ["2025-01-06", "2025-03-03", "2025-05-05"])

    def test_dates_before_the_start_are_dropped(self):
        self.assertEqual(days("FREQ=MONTHLY;BYMONTHDAY=1,15;COUNT=3", dt(2025, 1, 10)),
                         ["2025-01-10", "2025-01-15", "2025-02-01"])

    def test_monthly_limited_to_months(self):
        self.assertEqual(days("FREQ=MONTHLY;BYMONTH=2,8;BYMONTHDAY=-1;COUNT=3", dt(2025, 2, 28)),
                         ["2025-02-28", "2025-08-31", "2026-02-28"])

    def test_both_monthday_and_weekday_must_match(self):
        self.assertEqual(days("FREQ=MONTHLY;BYDAY=FR;BYMONTHDAY=13;COUNT=3", dt(2025, 1, 1)),
                         ["2025-01-01", "2025-06-13", "2026-02-13"])

    def test_yearly_29_february(self):
        self.assertEqual(days("FREQ=YEARLY;COUNT=3", dt(2024, 2, 29)),
                         ["2024-02-29", "2028-02-29", "2032-02-29"])

    def test_yearly_ordinals_count_in_the_year_without_bymonth(self):
        self.assertEqual(days("FREQ=YEARLY;BYDAY=20MO;COUNT=3", dt(1997, 5, 19, 9)),
                         ["1997-05-19", "1998-05-18", "1999-05-17"])
        self.assertEqual(days("FREQ=YEARLY;BYDAY=-1SU;COUNT=2", dt(2025, 12, 28)),
                         ["2025-12-28", "2026-12-27"])

    def test_yearly_ordinals_count_in_the_month_with_bymonth(self):
        self.assertEqual(days("FREQ=YEARLY;BYMONTH=11;BYDAY=4TH;COUNT=3", dt(2025, 11, 27)),
                         ["2025-11-27", "2026-11-26", "2027-11-25"])

    def test_yearly_bymonth_alone_uses_the_start_day(self):
        self.assertEqual(days("FREQ=YEARLY;BYMONTH=7,1;COUNT=4", dt(2025, 1, 15)),
                         ["2025-01-15", "2025-07-15", "2026-01-15", "2026-07-15"])

    def test_yearly_monthday_in_every_month(self):
        self.assertEqual(days("FREQ=YEARLY;BYMONTHDAY=-1;COUNT=3", dt(2025, 1, 31)),
                         ["2025-01-31", "2025-02-28", "2025-03-31"])

    def test_daily_filters(self):
        self.assertEqual(days("FREQ=DAILY;BYDAY=SA,SU;BYMONTH=3;COUNT=5", dt(2025, 3, 29)),
                         ["2025-03-29", "2025-03-30", "2026-03-01", "2026-03-07", "2026-03-08"])

    def test_exdates_still_count(self):
        self.assertEqual(days("FREQ=DAILY;COUNT=5", dt(2025, 1, 1, 10), exdates=[z(2025, 1, 3, 10)]),
                         ["2025-01-01", "2025-01-02", "2025-01-04", "2025-01-05"])

    def test_an_exdate_matches_the_instant_in_any_zone(self):
        self.assertEqual(days("FREQ=DAILY;COUNT=3", dt(2025, 1, 6, 10), BER, exdates=[z(2025, 1, 7, 9)]),
                         ["2025-01-06", "2025-01-08"])

    def test_rdates_join_in_order_once_and_dont_count(self):
        got = occ("FREQ=WEEKLY;COUNT=2", dt(2025, 1, 6, 9),
                  rdates=[z(2025, 2, 1, 9), z(2025, 1, 13, 9), z(2025, 1, 8, 12)])
        self.assertEqual(got, ["2025-01-06T09:00:00+00:00", "2025-01-08T12:00:00+00:00",
                               "2025-01-13T09:00:00+00:00", "2025-02-01T09:00:00+00:00"])

    def test_an_exdate_removes_an_rdate(self):
        self.assertEqual(days(None, dt(2025, 1, 1), rdates=[z(2025, 1, 2), z(2025, 1, 3)],
                              exdates=[z(2025, 1, 2)]), ["2025-01-01", "2025-01-03"])

    def test_until_a_date_is_the_local_date(self):
        self.assertEqual(days("FREQ=DAILY;UNTIL=20250103", dt(2025, 1, 1, 23, 30), NY),
                         ["2025-01-01", "2025-01-02", "2025-01-03"])

    def test_until_a_time_is_the_instant(self):
        self.assertEqual(days("FREQ=DAILY;UNTIL=20250103T043000Z", dt(2025, 1, 1, 23, 30), NY),
                         ["2025-01-01", "2025-01-02"])

    def test_after_and_limit(self):
        self.assertEqual(days("FREQ=DAILY", dt(2025, 1, 1, 9), after=z(2025, 1, 3, 9), limit=2),
                         ["2025-01-04", "2025-01-05"])

    def test_a_rule_that_never_matches_again_ends(self):
        began = time.monotonic()
        self.assertEqual(days("FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=30", dt(2025, 1, 1)), ["2025-01-01"])
        self.assertEqual(days("FREQ=DAILY;BYMONTH=2;BYMONTHDAY=30", dt(2025, 1, 1)), ["2025-01-01"])
        self.assertLess(time.monotonic() - began, 20)


TEAM = """
UID:standup@x
SUMMARY:Daily
  standup
DTSTART;TZID=Europe/Berlin:20250303T093000
DURATION:PT15M
RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR
EXDATE;TZID=Europe/Berlin:20250305T093000,20250306T093000
EXDATE:20250310T083000Z
BEGIN:VALARM
TRIGGER:-PT10M
DTSTART:nonsense
END:VALARM
"""


class Calendar(unittest.TestCase):
    def test_an_event(self):
        [ev] = cal(TEAM)
        self.assertEqual((ev.uid, ev.summary, ev.zone), ("standup@x", "Daily standup", BER))
        self.assertEqual(ev.start, dt(2025, 3, 3, 9, 30))
        self.assertEqual(ev.duration, timedelta(minutes=15))
        self.assertEqual(ev.rule, parse_rule("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"))
        self.assertEqual([utc for utc in ev.exdates],
                         [z(2025, 3, 5, 8, 30), z(2025, 3, 6, 8, 30), z(2025, 3, 10, 8, 30)])
        self.assertEqual((ev.rdates, ev.overrides), ([], {}))

    def test_folding_crlf_tabs_and_blank_lines(self):
        text = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:a\r\nSUMMARY:Quar\r\n\tterly\r\n  review\r\n\r\n"
                "DTSTART:20250101T090000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
        [ev] = read_calendar(text)
        self.assertEqual((ev.summary, ev.zone, ev.start), ("Quarterly review", "UTC", dt(2025, 1, 1, 9)))
        self.assertIsNone(ev.rule)
        self.assertEqual(ev.duration, timedelta(0))

    def test_text_escapes(self):
        [ev] = cal(r"UID:e" + "\n" + r"SUMMARY:a\, b\; c\\d\nline\Ntwo" + "\nDTSTART:20250101T090000Z")
        self.assertEqual(ev.summary, "a, b; c\\d\nline\ntwo")

    def test_names_in_any_case_and_quoted_parameters(self):
        [ev] = cal('uid:q\ndtstart;X-NOTE="a:b;c";tzid=Europe/Berlin:20250101T090000\nsummary:x')
        self.assertEqual((ev.uid, ev.zone, ev.start, ev.summary), ("q", BER, dt(2025, 1, 1, 9), "x"))

    def test_durations(self):
        for value, want in (("P1W", timedelta(weeks=1)), ("P2D", timedelta(days=2)),
                            ("PT1H30M", timedelta(hours=1, minutes=30)),
                            ("P1DT12H", timedelta(days=1, hours=12)), ("PT45S", timedelta(seconds=45))):
            with self.subTest(value=value):
                [ev] = cal(f"UID:d\nDTSTART:20250101T090000Z\nDURATION:{value}")
                self.assertEqual(ev.duration, want)

    def test_dtend_across_zones_and_a_change_of_clocks(self):
        [ev] = cal("UID:x\nDTSTART;TZID=America/New_York:20250115T090000\n"
                   "DTEND;TZID=Europe/Berlin:20250115T160000")
        self.assertEqual(ev.duration, timedelta(hours=1))
        [ev] = cal("UID:y\nDTSTART;TZID=America/New_York:20250308T120000\n"
                   "DTEND;TZID=America/New_York:20250309T120000")
        self.assertEqual(ev.duration, timedelta(hours=23))

    def test_rdates_over_several_lines(self):
        [ev] = cal("UID:r\nDTSTART;TZID=America/New_York:20250101T090000\n"
                   "RDATE;TZID=America/New_York:20250701T090000,20250801T090000\nRDATE:20250901T130000Z")
        self.assertEqual(ev.rdates, [z(2025, 7, 1, 13), z(2025, 8, 1, 13), z(2025, 9, 1, 13)])

    def test_overrides_wherever_they_are(self):
        events = read_calendar(
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\nUID:w\nRECURRENCE-ID;TZID=Europe/Berlin:20250310T100000\n"
            "DTSTART;TZID=Europe/Berlin:20250311T150000\nEND:VEVENT\n"
            "BEGIN:VEVENT\nUID:w\nSUMMARY:Weekly\nDTSTART;TZID=Europe/Berlin:20250303T100000\n"
            "DURATION:PT1H\nRRULE:FREQ=WEEKLY\nEND:VEVENT\n"
            "BEGIN:VEVENT\nUID:w\nRECURRENCE-ID:20250317T090000Z\nDTSTART:20250317T090000Z\n"
            "STATUS:CANCELLED\nEND:VEVENT\n"
            "END:VCALENDAR\n")
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(set(ev.overrides), {z(2025, 3, 10, 9), z(2025, 3, 17, 9)})
        self.assertIsNone(ev.overrides[z(2025, 3, 17, 9)])
        moved = ev.overrides[z(2025, 3, 10, 9)]
        self.assertEqual((moved.start, moved.zone, moved.summary, moved.duration),
                         (dt(2025, 3, 11, 15), BER, "Weekly", timedelta(hours=1)))

    def errors(self, text):
        with self.assertRaises(CalendarError) as cm:
            read_calendar(text)
        self.assertIsInstance(cm.exception, ValueError)
        return cm.exception

    def test_errors_at_their_lines(self):
        head = "BEGIN:VCALENDAR\nBEGIN:VEVENT\n"  # the event begins on line 2
        e = self.errors(head + "DTSTART:20250101T090000Z\nEND:VEVENT\n")
        self.assertEqual(e.line, 2)
        e = self.errors(head + "UID:a\nSUMMARY:x\nEND:VEVENT\n")
        self.assertEqual(e.line, 2)
        e = self.errors(head + "UID:a\nDTSTART:20250101T090000\nEND:VEVENT\n")
        self.assertEqual(e.line, 4)
        e = self.errors(head + "UID:a\nDTSTART;VALUE=DATE:20250101\nEND:VEVENT\n")
        self.assertEqual(e.line, 4)
        e = self.errors(head + "UID:a\nSUMMARY:x\nDTSTART;TZID=Mars/Olympus:20250101T090000\nEND:VEVENT\n")
        self.assertEqual(e.line, 5)
        self.assertIn("unknown time zone", e.message)
        e = self.errors(head + "UID:a\nDTSTART:20250101T090000Z\nDTEND:20250101T080000Z\nEND:VEVENT\n")
        self.assertEqual(e.line, 5)
        e = self.errors(head + "UID:a\nDTSTART:20250101T090000Z\nDURATION:-PT1H\nEND:VEVENT\n")
        self.assertEqual(e.line, 5)
        e = self.errors(head + "UID:a\nDTSTART:20250101T090000Z\n")
        self.assertEqual(e.line, 2)

    def test_a_folded_rule_error_is_at_its_first_line(self):
        e = self.errors("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:a\nDTSTART:20250101T090000Z\n"
                        "RRULE:FREQ=WEEKLY;\n BYDAY=2MO\nEND:VEVENT\n")
        self.assertEqual(e.line, 5)
        self.assertTrue(e.message.startswith("BYDAY"), e.message)

    def test_an_override_needs_its_event(self):
        e = self.errors("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:a\nDTSTART:20250101T090000Z\nEND:VEVENT\n"
                        "BEGIN:VEVENT\nUID:b\nRECURRENCE-ID:20250101T090000Z\n"
                        "DTSTART:20250102T090000Z\nEND:VEVENT\n")
        self.assertEqual(e.line, 6)


def w(*a):
    return localize(dt(*a), BER)


class Instances(unittest.TestCase):
    def test_overlap_rules_and_order(self):
        events = cal("UID:b\nSUMMARY:Long\nDTSTART:20250310T060000Z\nDURATION:PT3H",
                     "UID:a\nSUMMARY:Ends at start\nDTSTART:20250310T070000Z\nDURATION:PT1H",
                     "UID:c\nSUMMARY:Zero at start\nDTSTART:20250310T080000Z",
                     "UID:d\nSUMMARY:Zero at end\nDTSTART:20250310T100000Z",
                     "UID:a2\nSUMMARY:Same start\nDTSTART:20250310T060000Z\nDURATION:PT4H")
        got = instances(events, z(2025, 3, 10, 8), z(2025, 3, 10, 10))
        self.assertEqual([(i.uid, i.start.isoformat(), i.end.isoformat()) for i in got], [
            ("a2", "2025-03-10T06:00:00+00:00", "2025-03-10T10:00:00+00:00"),
            ("b", "2025-03-10T06:00:00+00:00", "2025-03-10T09:00:00+00:00"),
            ("c", "2025-03-10T08:00:00+00:00", "2025-03-10T08:00:00+00:00")])

    def test_instances_are_in_their_own_zone(self):
        [i] = instances(cal("UID:n\nDTSTART;TZID=America/New_York:20250308T230000\nDURATION:PT14H"),
                        z(2025, 3, 9), z(2025, 3, 10))
        self.assertEqual((i.start.isoformat(), i.end.isoformat()),
                         ("2025-03-08T23:00:00-05:00", "2025-03-09T14:00:00-04:00"))

    def test_overrides_move_and_cancel(self):
        events = read_calendar(
            "BEGIN:VCALENDAR\n"
            "BEGIN:VEVENT\nUID:w\nSUMMARY:Weekly\nDTSTART;TZID=Europe/Berlin:20250303T100000\n"
            "DURATION:PT1H\nRRULE:FREQ=WEEKLY;COUNT=10\nEND:VEVENT\n"
            "BEGIN:VEVENT\nUID:w\nRECURRENCE-ID;TZID=Europe/Berlin:20250310T100000\n"
            "DTSTART;TZID=Europe/Berlin:20250401T080000\nSUMMARY:Moved far\nEND:VEVENT\n"
            "BEGIN:VEVENT\nUID:w\nRECURRENCE-ID;TZID=Europe/Berlin:20250324T100000\n"
            "DTSTART;TZID=Europe/Berlin:20250324T100000\nSTATUS:CANCELLED\nEND:VEVENT\n"
            "END:VCALENDAR\n")
        got = instances(events, w(2025, 3, 8), w(2025, 4, 2))
        self.assertEqual([(i.summary, i.start.isoformat()) for i in got], [
            ("Weekly", "2025-03-17T10:00:00+01:00"), ("Weekly", "2025-03-31T10:00:00+02:00"),
            ("Moved far", "2025-04-01T08:00:00+02:00")])


AGENDA = """\
Mon 2025-03-03
  15:00-15:15  Standup

Tue 2025-03-04
  23:30-01:30+1  Release window

Wed 2025-03-05
  12:00  (no title)
  17:30-17:45  Standup (late)

Sun 2025-03-09
  08:30-09:00  Backup
"""


class Agenda(unittest.TestCase):
    def events(self):
        return read_calendar((DATA / "ops.ics").read_text(encoding="utf-8"))

    def test_the_agenda(self):
        self.assertEqual(agenda(self.events(), w(2025, 3, 3), w(2025, 3, 10), BER), AGENDA)

    def test_the_agenda_in_another_zone(self):
        got = agenda(self.events(), localize(dt(2025, 3, 9), NY), localize(dt(2025, 3, 12), NY), NY)
        self.assertEqual(got, "Sun 2025-03-09\n  03:30-04:00  Backup\n\n"
                              "Mon 2025-03-10\n  09:00-09:15  Standup\n\n"
                              "Tue 2025-03-11\n  18:30-20:30  Release window\n")

    def test_nothing(self):
        self.assertEqual(agenda(self.events(), w(2024, 1, 1), w(2024, 1, 2), BER), "No events.\n")


class Cli(unittest.TestCase):
    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_prints_the_agenda(self):
        code, out, err = self.run_main(str(DATA / "ops.ics"), "--from", "2025-03-03", "--to", "2025-03-10",
                                       "--tz", BER)
        self.assertEqual((code, out, err), (0, AGENDA, ""))

    def test_utc_by_default(self):
        code, out, _ = self.run_main(str(DATA / "ops.ics"), "--from", "2025-03-04", "--to", "2025-03-05")
        self.assertEqual((code, out), (0, "Tue 2025-03-04\n  22:30-00:30+1  Release window\n"))

    def test_errors(self):
        path = str(DATA / "ops.ics")
        code, out, err = self.run_main("missing.ics", "--from", "2025-03-03", "--to", "2025-03-10")
        self.assertEqual((code, out, err), (2, "", "error: cannot read missing.ics\n"))
        code, _, err = self.run_main(path, "--from", "2025-03-03", "--to", "2025-03-10", "--tz", "Mars/Olympus")
        self.assertEqual((code, err), (2, "error: unknown time zone Mars/Olympus\n"))
        code, _, err = self.run_main(path, "--from", "2025-03-10", "--to", "2025-03-10")
        self.assertEqual((code, err), (2, "error: --to must be after --from\n"))
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.ics")
            with open(bad, "w", encoding="utf-8") as f:
                f.write("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:a\nEND:VEVENT\n")
            code, _, err = self.run_main(bad, "--from", "2025-03-03", "--to", "2025-03-10")
            self.assertEqual(code, 2)
            self.assertTrue(err.startswith(f"error: {bad}: line 2: "), err)
            self.assertEqual(err.count("\n"), 1)

    def test_module_entry_point(self):
        root = os.environ.get("SWARMBENCH_WORKSPACE", os.getcwd())
        p = subprocess.run([sys.executable, "-m", "calrule", str(DATA / "ops.ics"), "--from", "2025-03-03",
                            "--to", "2025-03-10", "--tz", BER], cwd=root, capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        self.assertEqual((p.returncode, p.stdout), (0, AGENDA))
