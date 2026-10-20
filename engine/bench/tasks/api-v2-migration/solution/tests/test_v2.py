import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from shop.app import App
from shop.client import ApiError, ClientV2
from shop.migrate import migrate
from shop.store import Store, to_minor

DATA = Path(__file__).resolve().parent.parent / "data" / "orders.jsonl"
BODY = {"customer_id": "c-1", "currency": "USD", "lines": [{"sku": "A1", "quantity": 2, "unit_price": 999}]}


class V2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "orders.jsonl"
        shutil.copy(DATA, self.path)
        self.app = App(Store(self.path), clock=lambda: datetime(2026, 5, 1, tzinfo=timezone.utc))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_money_from_floats_is_exact(self):
        self.assertEqual(to_minor(19.99), 1999)
        self.assertEqual(to_minor("0.1"), 10)

    def test_v1_same_after_migration(self):
        before = [self.app.dispatch("GET", f"/v1/orders/{i}").body for i in range(1, 50)]
        migrate(self.path)
        app = App(Store(self.path))
        self.assertEqual([app.dispatch("GET", f"/v1/orders/{i}").body for i in range(1, 50)], before)
        self.assertEqual(migrate(self.path)[0], 0)

    def test_paging_is_complete(self):
        ids = [o["id"] for o in ClientV2(self.app.dispatch).iter_orders(limit=13)]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), len(self.app.store.all()))

    def test_idempotency(self):
        first = self.app.dispatch("POST", "/v2/orders", {"Idempotency-Key": "a"}, {}, BODY)
        again = self.app.dispatch("POST", "/v2/orders", {"idempotency-key": "a"}, {}, json.loads(json.dumps(BODY)))
        self.assertEqual(again.body, first.body)
        conflict = self.app.dispatch("POST", "/v2/orders", {"Idempotency-Key": "a"}, {}, {**BODY, "currency": "EUR"})
        self.assertEqual(conflict.status, 422)

    def test_validation_field(self):
        r = self.app.dispatch("POST", "/v2/orders", {}, {}, {**BODY, "lines": [{"sku": "A1", "quantity": True, "unit_price": 1}]})
        self.assertEqual(r.body["error"]["field"], "lines[0].quantity")

    def test_sdk_errors(self):
        with self.assertRaises(ApiError) as caught:
            ClientV2(self.app.dispatch).get_order("ord_99999")
        self.assertEqual(caught.exception.code, "not_found")


if __name__ == "__main__":
    unittest.main()
