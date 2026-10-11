import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from reports import exporters
from reports.model import Column, Report

WORKSPACE = Path(os.environ["SWARMBENCH_WORKSPACE"])


def report(rows=None, title="Q3 <Sales> & more"):
    return Report(
        title=title,
        columns=[Column("Product"), Column("Units", "int"), Column("Revenue", "money"), Column("First | sale", "date")],
        rows=[
            ["Widget", 3, Decimal("59.97"), date(2026, 9, 1)],
            ['Lamp, "XL"', 10, Decimal("272.5"), None],
            ["Line\nbreak | pipe", None, Decimal("0.125"), date(2026, 7, 4)],
        ] if rows is None else rows,
        generated_at=datetime(2026, 10, 1, 8, 30, 5),
    )


class CsvTest(unittest.TestCase):
    def test_full_output(self):
        expected = (
            "Product,Units,Revenue,First | sale\r\n"
            "Widget,3,59.97,2026-09-01\r\n"
            '"Lamp, ""XL""",10,272.50,\r\n'
            '"Line\nbreak | pipe",,0.13,2026-07-04\r\n'
        )
        self.assertEqual(exporters.export(report(), "csv"), expected)

    def test_no_rows(self):
        self.assertEqual(exporters.export(report([]), "csv"), "Product,Units,Revenue,First | sale\r\n")

    def test_carriage_return_is_quoted(self):
        r = Report("t", [Column("a")], [["x\ry"]], datetime(2026, 1, 1))
        self.assertEqual(exporters.export(r, "csv"), 'a\r\n"x\ry"\r\n')


class MarkdownTest(unittest.TestCase):
    def test_full_output(self):
        expected = (
            "# Q3 <Sales> & more\n"
            "\n"
            "| Product | Units | Revenue | First \\| sale |\n"
            "| --- | ---: | ---: | --- |\n"
            "| Widget | 3 | 59.97 | 2026-09-01 |\n"
            '| Lamp, "XL" | 10 | 272.50 |  |\n'
            "| Line<br>break \\| pipe |  | 0.13 | 2026-07-04 |\n"
        )
        self.assertEqual(exporters.export(report(), "markdown"), expected)

    def test_no_rows(self):
        expected = (
            "# Q3 <Sales> & more\n\n"
            "| Product | Units | Revenue | First \\| sale |\n"
            "| --- | ---: | ---: | --- |\n"
            "\n_No rows._\n"
        )
        self.assertEqual(exporters.export(report([]), "markdown"), expected)

    def test_md_alias(self):
        self.assertEqual(exporters.export(report(), "md"), exporters.export(report(), "markdown"))

    def test_crlf_in_cell(self):
        r = Report("t", [Column("a")], [["x\r\ny"]], datetime(2026, 1, 1))
        self.assertIn("| x<br>y |", exporters.export(r, "markdown"))


class HtmlTest(unittest.TestCase):
    def test_full_output(self):
        expected = (
            "<table>\n"
            "<caption>Q3 &lt;Sales&gt; &amp; more</caption>\n"
            "<thead>\n"
            '<tr><th>Product</th><th class="num">Units</th><th class="num">Revenue</th><th>First | sale</th></tr>\n'
            "</thead>\n"
            "<tbody>\n"
            '<tr><td>Widget</td><td class="num">3</td><td class="num">59.97</td><td>2026-09-01</td></tr>\n'
            '<tr><td>Lamp, &quot;XL&quot;</td><td class="num">10</td><td class="num">272.50</td><td></td></tr>\n'
            '<tr><td>Line<br>break | pipe</td><td class="num"></td><td class="num">0.13</td><td>2026-07-04</td></tr>\n'
            "</tbody>\n"
            "</table>\n"
        )
        self.assertEqual(exporters.export(report(), "html"), expected)

    def test_no_rows(self):
        out = exporters.export(report([]), "html")
        self.assertIn("</thead>\n<tbody>\n</tbody>\n</table>\n", out)

    def test_script_is_escaped(self):
        r = Report("t", [Column("a")], [["<script>alert('x')</script>"]], datetime(2026, 1, 1))
        out = exporters.export(r, "html")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;", out)


class JsonTest(unittest.TestCase):
    def test_structure(self):
        data = json.loads(exporters.export(report(), "json"))
        self.assertEqual(data["title"], "Q3 <Sales> & more")
        self.assertEqual(data["generated_at"], "2026-10-01T08:30:05")
        self.assertEqual(data["columns"][1], {"name": "Units", "type": "int"})
        self.assertEqual(data["rows"][0], {"Product": "Widget", "Units": 3, "Revenue": "59.97", "First | sale": "2026-09-01"})
        self.assertEqual(data["rows"][1]["First | sale"], None)
        self.assertEqual(data["rows"][2]["Units"], None)
        self.assertEqual(data["rows"][2]["Revenue"], "0.13")
        self.assertEqual(list(data["rows"][0]), ["Product", "Units", "Revenue", "First | sale"])

    def test_exact_text(self):
        r = Report("Café", [Column("n", "int")], [[1]], datetime(2026, 1, 2, 3, 4))
        expected = json.dumps({"title": "Café", "generated_at": "2026-01-02T03:04:00",
                               "columns": [{"name": "n", "type": "int"}], "rows": [{"n": 1}]},
                              indent=2, ensure_ascii=False) + "\n"
        self.assertEqual(exporters.export(r, "json"), expected)

    def test_round_trip(self):
        original = report(rows=[["Widget", 3, Decimal("59.97"), date(2026, 9, 1)], ["X", None, None, None]])
        self.assertEqual(Report.from_dict(json.loads(exporters.export(original, "json"))), original)


class RegistryTest(unittest.TestCase):
    def test_available(self):
        self.assertEqual(exporters.available(), ["csv", "html", "json", "markdown", "text"])

    def test_unknown(self):
        with self.assertRaises(ValueError) as ctx:
            exporters.export(report(), "pdf")
        self.assertIn("unknown format: pdf", str(ctx.exception))

    def test_text_still_works(self):
        self.assertTrue(exporters.export(report(), "text").startswith("Q3 <Sales> & more\n"))


class CliTest(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "reports", *args], cwd=WORKSPACE,
                              capture_output=True, timeout=60)

    def test_formats_command(self):
        proc = self.run_cli("formats")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.decode().split(), ["csv", "html", "json", "markdown", "text"])

    def test_export_each_format_to_file(self):
        data = json.loads(exporters.export(report(), "json"))
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "r.json"
            src.write_text(json.dumps(data), encoding="utf-8")
            loaded = Report.from_dict(data)
            for fmt in ("csv", "markdown", "md", "html", "json"):
                out = Path(tmp) / f"out.{fmt}"
                proc = self.run_cli("export", "--format", fmt, str(src), "-o", str(out))
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(out.read_bytes().decode("utf-8"), exporters.export(loaded, fmt), fmt)

    def test_unknown_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "r.json"
            src.write_text(exporters.export(report(), "json"), encoding="utf-8")
            proc = self.run_cli("export", "--format", "pdf", str(src))
            self.assertEqual(proc.returncode, 2)
            self.assertIn("error: unknown format: pdf", proc.stderr.decode())
