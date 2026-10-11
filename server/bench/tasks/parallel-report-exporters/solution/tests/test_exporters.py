import json
import unittest
from datetime import date, datetime
from decimal import Decimal

from reports import exporters
from reports.model import Column, Report


def sample(rows=None):
    return Report(
        title="Sales",
        columns=[Column("Product"), Column("Units", "int"), Column("Revenue", "money"), Column("Day", "date")],
        rows=rows if rows is not None else [["Widget, big", 3, Decimal("59.97"), date(2026, 9, 1)]],
        generated_at=datetime(2026, 10, 1, 8, 30),
    )


class ExporterTest(unittest.TestCase):
    def test_csv_quotes(self):
        self.assertEqual(exporters.export(sample(), "csv"),
                         'Product,Units,Revenue,Day\r\n"Widget, big",3,59.97,2026-09-01\r\n')

    def test_markdown_alignment(self):
        self.assertIn("| --- | ---: | ---: | --- |", exporters.export(sample(), "md"))

    def test_markdown_no_rows(self):
        self.assertTrue(exporters.export(sample([]), "markdown").endswith("\n\n_No rows._\n"))

    def test_html_num_class(self):
        self.assertIn('<td class="num">3</td>', exporters.export(sample(), "html"))

    def test_json_round_trip(self):
        report = sample()
        self.assertEqual(Report.from_dict(json.loads(exporters.export(report, "json"))), report)

    def test_available(self):
        self.assertEqual(exporters.available(), ["csv", "html", "json", "markdown", "text"])


if __name__ == "__main__":
    unittest.main()
