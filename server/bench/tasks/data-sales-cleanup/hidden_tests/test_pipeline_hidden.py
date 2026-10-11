import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sales.pipeline import build_summary

DATA = Path(__file__).parent / "data"
WORKSPACE = Path(os.environ["SWARMBENCH_WORKSPACE"])
HEADER = "order_id,order_date,customer,region,product,quantity,unit_price,status\n"


def write_csv(text):
    tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8", newline="")
    tmp.write(HEADER + text)
    tmp.close()
    return tmp.name


class Q3FileTest(unittest.TestCase):
    path = WORKSPACE / "data" / "sales_2026_q3.csv"
    expected_file = DATA / "expected_q3.json"

    @classmethod
    def setUpClass(cls):
        cls.expected = json.loads(cls.expected_file.read_text(encoding="utf-8"))
        cls.actual = build_summary(str(cls.path))

    def check(self, *keys):
        for key in keys:
            self.assertEqual(self.actual.get(key), self.expected[key], key)

    def test_counts(self):
        self.check("rows_read", "duplicates_removed", "valid_rows", "invalid_rows")

    def test_invalid_reasons(self):
        self.check("invalid_reasons")

    def test_status_counts(self):
        self.check("status_counts")

    def test_total_revenue(self):
        self.check("total_revenue")

    def test_revenue_by_region(self):
        self.check("revenue_by_region")

    def test_revenue_by_month(self):
        self.check("revenue_by_month", "revenue_by_region_month")

    def test_top_products(self):
        self.check("top_products")


class HiddenFileTest(Q3FileTest):
    path = DATA / "sales_hidden.csv"
    expected_file = DATA / "expected_hidden.json"


class RuleTest(unittest.TestCase):
    def test_last_duplicate_wins(self):
        path = write_csv(
            "A1,2026-07-01,x,EU,Widget,1,10.00,completed\n"
            "A2,2026-07-01,x,EU,Widget,1,5.00,completed\n"
            "A1,2026-07-02,x,EU,Widget,3,10.00,completed\n"
            ",2026-07-01,x,EU,Widget,1,1.00,completed\n"
            ",2026-07-01,x,EU,Widget,1,1.00,completed\n"
        )
        s = build_summary(path)
        self.assertEqual(s["rows_read"], 5)
        self.assertEqual(s["duplicates_removed"], 1)
        self.assertEqual(s["invalid_reasons"], {"order_id": 2})
        self.assertEqual(s["total_revenue"], "35.00")

    def test_last_duplicate_wins_even_if_invalid(self):
        path = write_csv(
            "A1,2026-07-01,x,EU,Widget,1,10.00,completed\n"
            "A1,2026-07-01,x,Mars,Widget,1,10.00,completed\n"
        )
        s = build_summary(path)
        self.assertEqual(s["invalid_reasons"], {"region": 1})
        self.assertEqual(s["total_revenue"], "0.00")
        self.assertEqual(s["top_products"], [])

    def test_date_formats_and_months(self):
        path = write_csv(
            "D1,2026-7-31,x,EU,P,1,1,completed\n"
            "D2,2026/08/01,x,EU,P,1,1,completed\n"
            "D3,02/09/2026,x,EU,P,1,1,completed\n"
            'D4,"Sep 3, 2026",x,EU,P,1,1,completed\n'
            "D5,09/02/2026,x,EU,P,1,1,completed\n"
            "D6,2026-02-29,x,EU,P,1,1,completed\n"
            "D7,07-15-2026,x,EU,P,1,1,completed\n"
        )
        s = build_summary(path)
        self.assertEqual(s["revenue_by_month"], {"2026-02": "1.00", "2026-07": "1.00", "2026-08": "1.00", "2026-09": "2.00"})
        self.assertEqual(s["invalid_reasons"], {"order_date": 2})

    def test_reason_order_and_values(self):
        path = write_csv(
            "R1,bad,x,Mars,,0,0,shipped\n"
            "R2,2026-07-01,x,Mars,,0,0,shipped\n"
            "R3,2026-07-01,x,  n.  america ,,0,0,shipped\n"
            "R4,2026-07-01,x,LatAm,Lamp,2.0,0,shipped\n"
            "R5,2026-07-01,x,apac,Lamp,+2,5,completed\n"
            "R6,2026-07-01,x,apac,Lamp,2,12.505,completed\n"
            "R7,2026-07-01,x,apac,Lamp,2,0.00,completed\n"
            "R8,2026-07-01,x,apac,Lamp,2,\"$1,200.5\",shipped\n"
            "R9,2026-07-01,x,apac,Lamp,2,\"$ 1,200.5\",COMPLETED\n"
        )
        s = build_summary(path)
        self.assertEqual(s["invalid_reasons"], {"order_date": 1, "region": 1, "product": 1, "quantity": 2,
                                                "unit_price": 2, "status": 1})
        self.assertEqual(s["revenue_by_region"], {"Asia Pacific": "2401.00"})
        self.assertEqual(s["status_counts"], {"completed": 1})

    def test_top_products_ties_and_units(self):
        path = write_csv(
            "T1,2026-07-01,x,EU,Beta,2,5,completed\n"
            "T2,2026-07-01,x,EU,Alpha,1,10,completed\n"
            "T3,2026-07-01,x,EU,Gamma,1,20,completed\n"
            "T4,2026-07-01,x,EU,Delta,1,1,completed\n"
            "T5,2026-07-01,x,EU,Delta,9,100,refunded\n"
        )
        s = build_summary(path)
        self.assertEqual(s["top_products"], [
            {"product": "Gamma", "revenue": "20.00", "units": 1},
            {"product": "Alpha", "revenue": "10.00", "units": 1},
            {"product": "Beta", "revenue": "10.00", "units": 2},
        ])
        self.assertEqual(s["status_counts"], {"completed": 4, "refunded": 1})


class CommandTest(unittest.TestCase):
    def test_command_writes_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "new" / "summary.json"
            proc = subprocess.run([sys.executable, "-m", "sales.pipeline", str(DATA / "sales_hidden.csv"), str(out)],
                                  cwd=WORKSPACE, capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            expected = json.loads((DATA / "expected_hidden.json").read_text(encoding="utf-8"))
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), expected)

    def test_q3_summary_file_written(self):
        out = WORKSPACE / "out" / "summary.json"
        self.assertTrue(out.exists(), "out/summary.json is missing")
        expected = json.loads((DATA / "expected_q3.json").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(out.read_text(encoding="utf-8")), expected)
