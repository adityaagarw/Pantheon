import json
import os
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(os.environ["SWARMBENCH_WORKSPACE"])
EXPECTED = json.loads((Path(__file__).parent / "expected.json").read_text(encoding="utf-8"))


def parse_time(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_findings():
    path = WORKSPACE / "findings.json"
    if not path.exists():
        raise AssertionError("findings.json is missing")
    return json.loads(path.read_text(encoding="utf-8"))


def load_postmortem():
    path = WORKSPACE / "postmortem.md"
    if not path.exists():
        raise AssertionError("postmortem.md is missing")
    return path.read_text(encoding="utf-8")


def sections(text):
    found = {}
    current = None
    for line in text.splitlines():
        match = re.match(r"^#{1,3}\s+(.+?)\s*$", line)
        if match:
            current = match.group(1).strip().lower()
            found[current] = []
        elif current is not None:
            found[current].append(line)
    return found


class FindingsTest(unittest.TestCase):
    def setUp(self):
        self.f = load_findings()

    def check_time(self, key):
        self.assertIn(key, self.f)
        self.assertEqual(parse_time(self.f[key]), parse_time(EXPECTED[key]), key)

    def test_incident_start(self):
        self.check_time("incident_start")

    def test_detected_at(self):
        self.check_time("detected_at")

    def test_mitigated_at(self):
        self.check_time("mitigated_at")

    def test_recovered_at(self):
        self.check_time("recovered_at")

    def test_root_cause_service(self):
        self.assertEqual(str(self.f.get("root_cause_service", "")).strip().lower(), "orders")

    def test_trigger_version(self):
        self.assertEqual(str(self.f.get("trigger_version", "")).strip().lstrip("v"), EXPECTED["trigger_version"])

    def test_root_cause_summary(self):
        text = str(self.f.get("root_cause_summary", "")).lower()
        self.assertTrue("connection" in text and ("leak" in text or "releas" in text or "not returned" in text), text)

    def test_peak_error_rate(self):
        self.assertAlmostEqual(float(self.f["peak_error_rate_percent"]), EXPECTED["peak_error_rate_percent"], delta=0.5)

    def test_failed_requests(self):
        expected = EXPECTED["failed_requests"]
        self.assertLessEqual(abs(int(self.f["failed_requests"]) - expected), max(2, expected * 0.01))

    def test_affected_endpoints(self):
        self.assertEqual(sorted(self.f["affected_endpoints"]), EXPECTED["affected_endpoints"])

    def test_time_to_detect(self):
        self.assertAlmostEqual(float(self.f["time_to_detect_minutes"]), EXPECTED["time_to_detect_minutes"], delta=0.1)

    def test_time_to_mitigate(self):
        self.assertAlmostEqual(float(self.f["time_to_mitigate_minutes"]), EXPECTED["time_to_mitigate_minutes"], delta=0.1)


class PostmortemTest(unittest.TestCase):
    def setUp(self):
        self.text = load_postmortem()
        self.sections = sections(self.text)

    def test_required_sections(self):
        for name in ("summary", "impact", "timeline", "root cause", "resolution", "action items"):
            self.assertTrue(any(key.startswith(name) for key in self.sections), f"missing section: {name}")

    def test_action_items(self):
        key = next((k for k in self.sections if k.startswith("action items")), None)
        self.assertIsNotNone(key)
        items = [line for line in self.sections[key] if re.match(r"^\s*([-*+]|\d+[.)])\s+\S", line)]
        self.assertGreaterEqual(len(items), 3)

    def test_root_cause_section_content(self):
        key = next((k for k in self.sections if k.startswith("root cause")), None)
        self.assertIsNotNone(key)
        body = "\n".join(self.sections[key]).lower()
        self.assertIn("2.4.1", body)
        self.assertIn("orders", body)
        self.assertTrue("pool" in body and "connection" in body)

    def test_timeline_has_key_events(self):
        key = next((k for k in self.sections if k.startswith("timeline")), None)
        self.assertIsNotNone(key)
        body = "\n".join(self.sections[key])
        for clock in ("14:00", "14:02", "14:06", "14:24", "14:27"):
            self.assertIn(clock, body)

    def test_impact_has_numbers(self):
        key = next((k for k in self.sections if k.startswith("impact")), None)
        self.assertIsNotNone(key)
        body = "\n".join(self.sections[key]).replace(",", "")
        numbers = [int(n) for n in re.findall(r"\b\d+\b", body)]
        expected = EXPECTED["failed_requests"]
        self.assertTrue(any(abs(n - expected) <= max(2, expected * 0.01) for n in numbers),
                        "the impact section must give the number of failed requests")
