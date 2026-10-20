import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

WORKSPACE = Path(os.environ["SWARMBENCH_WORKSPACE"])
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(WORKSPACE))


def t(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class BusinessMinutes(unittest.TestCase):
    def check(self, start, end, region, want):
        from sla.business import minutes

        self.assertAlmostEqual(float(minutes(t(start), t(end), region)), want, places=6)

    def test_over_a_weekend(self):
        self.check("2026-03-06T16:00:00Z", "2026-03-09T08:30:00Z", "EMEA", 60)

    def test_from_a_saturday(self):
        self.check("2026-03-07T15:00:00Z", "2026-03-09T13:30:00Z", "Americas", 30)

    def test_over_a_holiday(self):
        self.check("2026-05-22T21:00:00Z", "2026-05-26T14:00:00Z", "Americas", 120)
        self.check("2026-05-13T17:00:00+01:00", "2026-05-15T09:30:00+01:00", "EMEA", 60)

    def test_two_holidays_in_a_row(self):
        self.check("2026-02-16T09:00:00+08:00", "2026-02-19T10:00:00+08:00", "APAC", 600)

    def test_local_day_differs_from_utc(self):
        self.check("2026-03-10T00:30:00Z", "2026-03-10T02:15:00Z", "APAC", 75)

    def test_fractions_and_order(self):
        self.check("2026-03-10T09:00:00+01:00", "2026-03-10T09:00:30+01:00", "EMEA", 0.5)
        self.check("2026-03-10T12:00:00Z", "2026-03-10T11:00:00Z", "EMEA", 0)


class Rules(unittest.TestCase):
    def test_priority_from_subject(self):
        from sla.rules import priority

        cases = [
            ({"subject": "Download of report is slow"}, "business", "normal"),
            ({"subject": "Shutdown of old workspace", "priority": None}, "business", "normal"),
            ({"subject": "Error-free import? question"}, "business", "high"),
            ({"subject": "can't log in after reset", "priority": ""}, "enterprise", "urgent"),
            ({"subject": "Dashboard is DOWN", "priority": "p1"}, "business", "urgent"),
            ({"subject": "How do I rotate API keys?"}, "business", "low"),
            ({"subject": "Need more seats"}, "business", "normal"),
        ]
        for ticket, plan, want in cases:
            self.assertEqual(priority(ticket, plan), want, ticket)

    def test_starter_has_no_urgent(self):
        from sla.rules import priority

        self.assertEqual(priority({"subject": "outage", "priority": None}, "starter"), "high")
        self.assertEqual(priority({"subject": "hello", "priority": "urgent"}, "starter"), "high")
        self.assertEqual(priority({"subject": "outage", "priority": "low"}, "starter"), "low")

    def test_key(self):
        from sla.rules import key

        self.assertEqual(key("RE: re: Dashboard is down!"), "dashboard is down")
        self.assertEqual(key("FW:  Export failed   with error 500..."), "export failed with error 500")
        self.assertEqual(key("Fwd: How do I rotate API keys? "), "how do i rotate api keys")


class ReportFor:
    data = None

    @classmethod
    def setUpClass(cls):
        from sla.report import build

        cls.error = None
        try:
            cls.got = json.loads(json.dumps(build(cls.data)))
        except Exception as e:  # noqa: BLE001
            cls.got, cls.error = None, e
        cls.want = json.loads((HERE / cls.expected).read_text(encoding="utf-8"))

    def report(self):
        if self.error is not None:
            self.fail(f"build() failed: {self.error!r}")
        return self.got

    def weeks(self, *fields):
        rows = self.report().get("weeks", [])
        return ([[w.get(f) for f in ("region", "week") + fields] for w in rows],
                [[w[f] for f in ("region", "week") + fields] for w in self.want["weeks"]])

    def test_as_of(self):
        self.assertEqual(self.report().get("as_of"), self.want["as_of"])

    def test_weeks(self):
        self.assertEqual(*self.weeks())

    def test_ticket_counts(self):
        self.assertEqual(*self.weeks("tickets", "duplicates"))

    def test_first_response_breach_counts(self):
        self.assertEqual(*self.weeks("first_response_breaches"))

    def test_resolution_breach_counts(self):
        self.assertEqual(*self.weeks("resolution_breaches"))

    def test_medians(self):
        self.assertEqual(*self.weeks("median_first_response_minutes"))

    def test_p90(self):
        self.assertEqual(*self.weeks("p90_resolution_minutes"))

    def breaches(self, kind, fields):
        got = [[b.get(f) for f in fields] for b in self.report().get("breaches", []) if b.get("kind") == kind]
        want = [[b[f] for f in fields] for b in self.want["breaches"] if b["kind"] == kind]
        return got, want

    def test_first_response_breaches(self):
        self.assertEqual(*self.breaches("first_response", ("ticket", "plan", "priority")))

    def test_resolution_breaches(self):
        self.assertEqual(*self.breaches("resolution", ("ticket", "plan", "priority")))

    def test_minutes_over(self):
        self.assertEqual(self.report().get("breaches"), self.want["breaches"])


class March(ReportFor, unittest.TestCase):
    data = WORKSPACE / "data"
    expected = "expected-march.json"


class May(ReportFor, unittest.TestCase):
    data = HERE / "data"
    expected = "expected-may.json"


class Command(unittest.TestCase):
    def test_writes_the_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "PYTHONPATH": str(WORKSPACE)}
            proc = subprocess.run([sys.executable, "-m", "sla", str(HERE / "data")], cwd=tmp,
                                  env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            out = json.loads((Path(tmp) / "out" / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(out, json.loads((HERE / "expected-may.json").read_text(encoding="utf-8")))

    def test_march_report_in_the_work_folder(self):
        out = WORKSPACE / "out" / "report.json"
        self.assertTrue(out.is_file(), "out/report.json is missing")
        self.assertEqual(json.loads(out.read_text(encoding="utf-8")),
                         json.loads((HERE / "expected-march.json").read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
