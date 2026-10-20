import unittest
from pathlib import Path

from sla.report import build

DATA = Path(__file__).resolve().parent.parent / "data"


class ReportTest(unittest.TestCase):
    def test_builds(self):
        report = build(DATA)
        self.assertIn("weeks", report)
        self.assertIn("breaches", report)


if __name__ == "__main__":
    unittest.main()
