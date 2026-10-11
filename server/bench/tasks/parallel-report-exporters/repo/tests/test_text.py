import unittest
from datetime import date, datetime
from decimal import Decimal

from reports import exporters
from reports.model import Column, Report


def sample():
    return Report(
        title="Sales",
        columns=[Column("Product"), Column("Units", "int"), Column("Revenue", "money"), Column("Day", "date")],
        rows=[["Widget", 3, Decimal("59.97"), date(2026, 9, 1)], ["Lamp", 10, Decimal("5"), None]],
        generated_at=datetime(2026, 10, 1, 8, 30),
    )


class TextExporterTest(unittest.TestCase):
    def test_text(self):
        expected = (
            "Sales\n"
            "=====\n"
            "Product  Units  Revenue  Day\n"
            "-------  -----  -------  ----------\n"
            "Widget       3    59.97  2026-09-01\n"
            "Lamp        10     5.00\n"
        )
        self.assertEqual(exporters.export(sample(), "text"), expected)

    def test_unknown_format(self):
        with self.assertRaises(ValueError):
            exporters.export(sample(), "pdf")


if __name__ == "__main__":
    unittest.main()
