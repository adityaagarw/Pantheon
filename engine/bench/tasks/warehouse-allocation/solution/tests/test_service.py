import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fulfil.service import Fulfilment, ReservationError, ship_date

DATA = Path(__file__).resolve().parent.parent / "data"
NOW = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
LONDON = {"lat": 51.5, "lon": -0.12}


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        for f in DATA.iterdir():
            shutil.copy(f, self.dir / f.name)
        self.now = NOW
        self.svc = Fulfilment(self.dir, lambda: self.now)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def order(self, oid, lines, to=LONDON):
        return {"id": oid, "lines": [{"sku": s, "qty": q} for s, q in lines], "ship_to": to}

    def test_single_warehouse(self):
        plan = self.svc.allocate(self.order("1", [("MUG-01", 10)]))
        self.assertEqual(plan["shipments"][0]["warehouse"], "DC-LON")

    def test_split_and_backorder(self):
        plan = self.svc.allocate(self.order("2", [("TEE-L", 70), ("BAG-01", 30)]))
        self.assertEqual([s["warehouse"] for s in plan["shipments"]], ["DC-RTM", "DC-LON"])
        self.assertEqual(plan["backorders"], [{"sku": "TEE-L", "qty": 8, "expected": None},
                                              {"sku": "BAG-01", "qty": 30, "expected": "2026-06-15"}])

    def test_expiry_and_restart(self):
        self.svc.allocate(self.order("3", [("CAP-RED", 5)]))
        self.assertEqual(Fulfilment(self.dir, lambda: self.now).availability("CAP-RED")["DC-RTM"], 17)
        self.now = NOW + timedelta(minutes=30)
        self.assertEqual(self.svc.availability("CAP-RED")["DC-RTM"], 22)
        with self.assertRaises(ReservationError):
            self.svc.confirm("3")

    def test_ship_dates(self):
        wh = {"utc_offset": "+01:00", "cutoff": "15:00"}
        self.assertEqual(ship_date(datetime(2026, 6, 5, 13, 59, tzinfo=timezone.utc), wh).isoformat(), "2026-06-05")
        self.assertEqual(ship_date(datetime(2026, 6, 5, 14, 0, tzinfo=timezone.utc), wh).isoformat(), "2026-06-08")


if __name__ == "__main__":
    unittest.main()
