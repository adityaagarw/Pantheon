import json
import math
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKSPACE = Path(os.environ["SWARMBENCH_WORKSPACE"])
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(WORKSPACE))

UTC = timezone.utc
MONDAY = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
PARIS = {"lat": 48.8566, "lon": 2.3522}
BERLIN = {"lat": 52.52, "lon": 13.405}
WAREHOUSES = {w["id"]: w for w in json.loads((HERE / "data" / "warehouses.json").read_text())}


def km(wid, to):
    w = WAREHOUSES[wid]
    p1, p2 = math.radians(w["lat"]), math.radians(to["lat"])
    dp, dl = p2 - p1, math.radians(to["lon"] - w["lon"])
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * 6371.0088 * math.asin(math.sqrt(a)), 1)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="alloc-"))
        for name in ("warehouses.json", "stock.json", "inbound.json"):
            shutil.copy(HERE / "data" / name, self.dir / name)
        self.clock = Clock(MONDAY)
        self.svc = self.new()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def new(self):
        from fulfil.service import Fulfilment

        return Fulfilment(self.dir, self.clock)

    def order(self, oid, lines, to=PARIS):
        return {"id": oid, "lines": [{"sku": s, "qty": q} for s, q in lines], "ship_to": to}


class OneWarehouse(Base):
    def test_nearest_that_has_everything(self):
        plan = self.svc.allocate(self.order("A-1", [("S1", 3)]))
        self.assertEqual(plan, {
            "order": "A-1", "reservation": "R-A-1", "backorders": [],
            "shipments": [{"warehouse": "W1", "lines": [{"sku": "S1", "qty": 3}],
                           "distance_km": km("W1", PARIS), "ships_on": "2026-06-01"}]})

    def test_a_farther_warehouse_when_the_nearest_lacks_a_line(self):
        plan = self.svc.allocate(self.order("A-2", [("S1", 2), ("S3", 5)]))
        self.assertEqual([s["warehouse"] for s in plan["shipments"]], ["W2"])
        self.assertEqual(plan["shipments"][0]["lines"], [{"sku": "S1", "qty": 2}, {"sku": "S3", "qty": 5}])


class Split(Base):
    def test_greedy_split_with_ties_broken_by_distance(self):
        plan = self.svc.allocate(self.order("B-1", [("S1", 12), ("S2", 6), ("S3", 3)]))
        self.assertEqual(plan["shipments"], [
            {"warehouse": "W1", "lines": [{"sku": "S1", "qty": 10}, {"sku": "S2", "qty": 5}],
             "distance_km": km("W1", PARIS), "ships_on": "2026-06-01"},
            {"warehouse": "W2", "lines": [{"sku": "S1", "qty": 2}, {"sku": "S2", "qty": 1}, {"sku": "S3", "qty": 3}],
             "distance_km": km("W2", PARIS), "ships_on": "2026-06-01"}])
        self.assertEqual(plan["backorders"], [])

    def test_split_then_backorder(self):
        plan = self.svc.allocate(self.order("B-2", [("S3", 12), ("S1", 1)], to=BERLIN))
        self.assertEqual([(s["warehouse"], s["lines"]) for s in plan["shipments"]], [
            ("W2", [{"sku": "S3", "qty": 7}, {"sku": "S1", "qty": 1}]),
            ("W3", [{"sku": "S3", "qty": 2}])])
        self.assertEqual(plan["backorders"], [{"sku": "S3", "qty": 3, "expected": "2026-06-12"}])


class Backorders(Base):
    def test_promises_use_up_inbound(self):
        first = self.svc.allocate(self.order("C-1", [("S9", 5)], to=BERLIN))
        self.assertEqual(first["shipments"], [])
        self.assertEqual(first["backorders"], [{"sku": "S9", "qty": 5, "expected": "2026-06-10"}])
        second = self.svc.allocate(self.order("C-2", [("S9", 2)], to=BERLIN))
        self.assertEqual(second["backorders"], [{"sku": "S9", "qty": 2, "expected": "2026-06-10"}])
        third = self.svc.allocate(self.order("C-3", [("S9", 1)], to=BERLIN))
        self.assertEqual(third["backorders"], [{"sku": "S9", "qty": 1, "expected": None}])

    def test_not_enough_inbound_promises_nothing(self):
        big = self.svc.allocate(self.order("C-4", [("S9", 8)], to=BERLIN))
        self.assertEqual(big["backorders"][0]["expected"], None)
        small = self.svc.allocate(self.order("C-5", [("S9", 4)], to=BERLIN))
        self.assertEqual(small["backorders"][0]["expected"], "2026-06-08")

    def test_cancel_releases_promises(self):
        self.svc.allocate(self.order("C-1", [("S9", 5)], to=BERLIN))
        self.svc.cancel("C-1")
        again = self.svc.allocate(self.order("C-6", [("S9", 7)], to=BERLIN))
        self.assertEqual(again["backorders"][0]["expected"], "2026-06-10")


class Holding(Base):
    def test_reservations_hold_and_expire(self):
        before = self.svc.availability("S1")
        self.assertEqual(before, {"W1": 10, "W2": 4, "W3": 50, "W4": 10})
        self.svc.allocate(self.order("D-1", [("S1", 12), ("S2", 6), ("S3", 3)]))
        self.assertEqual(self.svc.availability("S1"), {"W1": 0, "W2": 2, "W3": 50, "W4": 10})
        self.clock.now = MONDAY + timedelta(minutes=29, seconds=59)
        self.assertEqual(self.svc.availability("S1")["W1"], 0)
        self.clock.now = MONDAY + timedelta(minutes=30)
        self.assertEqual(self.svc.availability("S1"), before)

    def test_confirm_takes_the_stock(self):
        self.svc.allocate(self.order("D-2", [("S2", 4)]))
        self.assertEqual(self.svc.confirm("D-2"), {"status": "confirmed"})
        self.clock.now = MONDAY + timedelta(hours=5)
        self.assertEqual(self.svc.availability("S2")["W1"], 1)

    def test_same_order_again(self):
        first = self.svc.allocate(self.order("D-3", [("S1", 3)]))
        self.clock.now = MONDAY + timedelta(minutes=10)
        again = self.svc.allocate(self.order("D-3", [("S1", 3)]))
        self.assertEqual(again, first)
        self.assertEqual(self.svc.availability("S1")["W1"], 7)

    def test_new_plan_after_expiry(self):
        self.svc.allocate(self.order("D-4", [("S1", 3)]))
        self.svc.allocate(self.order("X-1", [("S1", 8)], to=BERLIN))  # W1 runs low meanwhile
        self.clock.now = MONDAY + timedelta(minutes=31)
        self.svc.allocate(self.order("X-2", [("S1", 9)], to=BERLIN))
        plan = self.svc.allocate(self.order("D-4", [("S1", 3)]))
        self.assertEqual(plan["shipments"][0]["warehouse"], "W2")

    def test_errors(self):
        from fulfil.service import ReservationError

        with self.assertRaises(ReservationError):
            self.svc.confirm("nope")
        self.svc.allocate(self.order("D-5", [("S1", 1)]))
        self.clock.now = MONDAY + timedelta(minutes=30)
        with self.assertRaises(ReservationError):
            self.svc.confirm("D-5")
        self.svc.allocate(self.order("D-5", [("S1", 1)]))
        self.svc.confirm("D-5")
        for call in (lambda: self.svc.confirm("D-5"), lambda: self.svc.cancel("D-5"),
                     lambda: self.svc.allocate(self.order("D-5", [("S1", 1)]))):
            with self.assertRaises(ReservationError):
                call()


class ShipDates(Base):
    def ships(self, now, to, lines=(("S1", 1),)):
        self.clock.now = now
        plan = self.svc.allocate(self.order(f"E-{now.isoformat()}-{to['lat']}", list(lines), to=to))
        return {s["warehouse"]: s["ships_on"] for s in plan["shipments"]}

    def test_cutoffs_in_local_time(self):
        friday = datetime(2026, 6, 5, 14, 30, tzinfo=UTC)  # Berlin 15:30, Newark 09:30
        self.assertEqual(self.ships(friday, BERLIN), {"W1": "2026-06-08"})
        newark = {"lat": 40.73, "lon": -74.17}
        self.assertEqual(self.ships(friday, newark), {"W3": "2026-06-05"})

    def test_just_before_the_cutoff(self):
        madrid = {"lat": 40.42, "lon": -3.70}
        self.assertEqual(self.ships(datetime(2026, 6, 5, 12, 59, 59, tzinfo=UTC), madrid), {"W2": "2026-06-05"})
        self.assertEqual(self.ships(datetime(2026, 6, 5, 13, 0, tzinfo=UTC), madrid), {"W2": "2026-06-08"})

    def test_weekend_and_local_date(self):
        self.assertEqual(self.ships(datetime(2026, 6, 6, 9, 0, tzinfo=UTC), BERLIN), {"W1": "2026-06-08"})
        newark = {"lat": 40.73, "lon": -74.17}
        # Monday 02:00 UTC is still Sunday 21:00 in Newark: ships Monday
        self.assertEqual(self.ships(datetime(2026, 6, 8, 2, 0, tzinfo=UTC), newark), {"W3": "2026-06-08"})


class Restart(Base):
    def test_state_survives(self):
        stock_before = (self.dir / "stock.json").read_bytes()
        held = self.svc.allocate(self.order("F-1", [("S1", 3)]))
        self.svc.allocate(self.order("F-2", [("S2", 4)]))
        self.svc.confirm("F-2")
        self.svc.allocate(self.order("F-3", [("S9", 5)], to=BERLIN))
        self.svc.allocate(self.order("F-4", [("S9", 2)], to=BERLIN))
        self.svc.cancel("F-4")
        again = self.new()
        for sku in ("S1", "S2", "S9"):
            self.assertEqual(again.availability(sku), self.svc.availability(sku), sku)
        self.assertEqual(again.allocate(self.order("F-1", [("S1", 3)])), held)
        later = again.allocate(self.order("F-5", [("S9", 2)], to=BERLIN))
        self.assertEqual(later["backorders"][0]["expected"], "2026-06-10")
        from fulfil.service import ReservationError

        with self.assertRaises(ReservationError):
            again.confirm("F-2")
        self.clock.now = MONDAY + timedelta(hours=2)
        self.assertEqual(self.new().availability("S2")["W1"], 1)
        self.assertEqual((self.dir / "stock.json").read_bytes(), stock_before)
        self.assertTrue((self.dir / "events.jsonl").is_file())


if __name__ == "__main__":
    unittest.main()
