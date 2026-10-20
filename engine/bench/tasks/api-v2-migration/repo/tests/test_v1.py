import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from shop.app import App
from shop.client import ApiError, Client
from shop.store import Store

DATA = Path(__file__).resolve().parent.parent / "data" / "orders.jsonl"


class V1Test(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(DATA, self.tmp / "orders.jsonl")
        self.app = App(Store(self.tmp / "orders.jsonl"),
                       clock=lambda: datetime(2026, 5, 1, tzinfo=timezone.utc))
        self.client = Client(self.app.dispatch)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_list_and_get(self):
        orders = self.client.list_orders(per_page=5)
        self.assertEqual([o["id"] for o in orders], [1, 2, 3, 4, 5])
        self.assertEqual(self.client.get_order(2)["id"], 2)

    def test_old_spelling_is_shown_as_cancelled(self):
        statuses = {o["status"] for o in self.client.list_orders(per_page=100, page=1)}
        self.assertNotIn("canceled", statuses)

    def test_create_and_cancel(self):
        order = self.client.create_order("c-1", [{"sku": "A1", "qty": 2, "price": "9.99"}])
        self.assertEqual((order["total"], order["status"]), ("19.98", "pending"))
        self.assertEqual(self.client.cancel_order(order["id"])["status"], "cancelled")
        with self.assertRaises(ApiError):
            self.client.cancel_order(order["id"])


if __name__ == "__main__":
    unittest.main()
