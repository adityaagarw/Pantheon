import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(os.environ["SWARMBENCH_WORKSPACE"])
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(WORKSPACE))

GOLDEN = json.loads((HERE / "v1_golden.json").read_text(encoding="utf-8"))
MIGRATED = json.loads((HERE / "expected_migrated.json").read_text(encoding="utf-8"))
CLOCK = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
LIST_REQUESTS = [g for g in GOLDEN if g["method"] == "GET" and g["path"] == "/v1/orders"][:19]


class Fixture:
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="v2-"))
        self.data = self.tmp / "orders.jsonl"
        shutil.copy(HERE / "orders_v1.jsonl", self.data)
        self.now = CLOCK

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def app(self):
        from shop.app import App
        from shop.store import Store

        return App(Store(self.data), clock=lambda: self.now)

    def migrate(self):
        env = {**os.environ, "PYTHONPATH": str(WORKSPACE)}
        return subprocess.run([sys.executable, "-m", "shop.migrate", str(self.data)],
                              capture_output=True, text=True, env=env, timeout=120, cwd=self.tmp)


def replay(test, app, golden):
    for g in golden:
        r = app.dispatch(g["method"], g["path"], {}, g["query"], g["body"])
        test.assertEqual((r.status, r.body), (g["status"], g["response"]),
                         f"{g['method']} {g['path']} {g['query']}")


class V1(Fixture, unittest.TestCase):
    def test_lists_unchanged(self):
        replay(self, self.app(), LIST_REQUESTS)

    def test_orders_unchanged(self):
        replay(self, self.app(), [g for g in GOLDEN if g["method"] == "GET"
                                  and g["path"] != "/v1/orders"][:242])

    def test_changes_unchanged(self):
        replay(self, self.app(), GOLDEN[-10:])

    def test_unchanged_after_migration(self):
        self.assertEqual(self.migrate().returncode, 0)
        replay(self, self.app(), GOLDEN)

    def test_deprecation_headers(self):
        r = self.app().dispatch("GET", "/v1/orders/1", {}, {}, None)
        self.assertEqual(r.headers.get("Deprecation"), "true")
        self.assertEqual(r.headers.get("Sunset"), "Wed, 30 Sep 2026 00:00:00 GMT")
        self.assertEqual(r.headers.get("Link"), '</v2/orders>; rel="successor-version"')
        r = self.app().dispatch("GET", "/v1/orders/999", {}, {}, None)
        self.assertEqual((r.status, r.headers.get("Deprecation")), (404, "true"))


class Migration(Fixture, unittest.TestCase):
    def test_migrates_every_row(self):
        proc = self.migrate()
        self.assertEqual(proc.returncode, 0, proc.stderr[-1500:])
        self.assertIn("migrated 240 of 240 orders", proc.stdout)
        rows = [json.loads(l) for l in self.data.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(rows, MIGRATED)

    def test_second_run_changes_nothing(self):
        self.migrate()
        before = self.data.read_bytes()
        proc = self.migrate()
        self.assertIn("migrated 0 of 240 orders", proc.stdout)
        self.assertEqual(self.data.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["orders.jsonl"])

    def test_money_is_exact(self):
        self.migrate()
        rows = {r["id"]: r for r in (json.loads(l) for l in self.data.read_text().splitlines())}
        source = [json.loads(l) for l in (HERE / "orders_v1.jsonl").read_text().splitlines()]
        floats = [(s["id"], i) for s in source for i, it in enumerate(s["items"])
                  if isinstance(it["price"], float) and it["price"] == 19.99]
        self.assertTrue(floats)
        for oid, i in floats:
            self.assertEqual(rows[oid]["lines"][i]["unit_price"], 1999)


def resource(row):
    cur = row["currency"]
    return {"id": f"ord_{row['id']}", "customer_id": row["customer_id"], "currency": cur,
            "lines": [{"sku": l["sku"], "quantity": l["quantity"],
                       "unit_price": {"amount": l["unit_price"], "currency": cur}}
                      for l in row["lines"]],
            "total": {"amount": row["total"], "currency": cur}, "status": row["status"],
            "created_at": row["created_at"]}


NEWEST_FIRST = sorted(MIGRATED, key=lambda r: (r["created_at"], r["id"]), reverse=True)


class V2Read(Fixture, unittest.TestCase):
    def get(self, app, path, **query):
        return app.dispatch("GET", path, {}, {k: str(v) for k, v in query.items()}, None)

    def follow(self, app, **query):
        seen, cursor = [], None
        for _ in range(300):
            q = dict(query, **({"cursor": cursor} if cursor else {}))
            r = self.get(app, "/v2/orders", **q)
            self.assertEqual(r.status, 200, r.body)
            seen += r.body["data"]
            cursor = r.body["next_cursor"]
            if cursor is None:
                return seen
        self.fail("the cursors never ended")

    def test_get_order(self):
        app = self.app()
        for row in (MIGRATED[0], MIGRATED[100], MIGRATED[-1]):
            r = self.get(app, f"/v2/orders/ord_{row['id']}")
            self.assertEqual((r.status, r.body), (200, resource(row)))

    def test_unknown_orders(self):
        app = self.app()
        for path in ("/v2/orders/17", "/v2/orders/ord_x", "/v2/orders/ord_9999"):
            r = self.get(app, path)
            self.assertEqual(r.status, 404, path)
            self.assertEqual(r.body["error"]["code"], "not_found")

    def test_first_page(self):
        r = self.get(self.app(), "/v2/orders")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.body["data"], [resource(x) for x in NEWEST_FIRST[:25]])
        self.assertIsInstance(r.body["next_cursor"], str)

    def test_paging_gives_every_order_once(self):
        got = self.follow(self.app(), limit=7)
        self.assertEqual([o["id"] for o in got], [f"ord_{x['id']}" for x in NEWEST_FIRST])

    def test_paging_ignores_orders_created_meanwhile(self):
        app = self.app()
        first = self.get(app, "/v2/orders", limit=10)
        body = {"customer_id": "c-1", "currency": "USD",
                "lines": [{"sku": "A1", "quantity": 1, "unit_price": 100}]}
        self.now = datetime(2025, 12, 1, 9, 0, 0, tzinfo=timezone.utc)  # a clock running late
        for _ in range(3):
            self.assertEqual(app.dispatch("POST", "/v2/orders", {}, {}, body).status, 201)
        rest = self.follow(app, limit=10, cursor=first.body["next_cursor"])
        ids = [o["id"] for o in first.body["data"] + rest]
        self.assertEqual(ids, [f"ord_{x['id']}" for x in NEWEST_FIRST])

    def test_filters(self):
        app = self.app()
        got = self.follow(app, limit=11, status="paid")
        self.assertEqual([o["id"] for o in got],
                         [f"ord_{x['id']}" for x in NEWEST_FIRST if x["status"] == "paid"])
        got = self.follow(app, customer_id="c-5", limit=2)
        self.assertEqual([o["id"] for o in got],
                         [f"ord_{x['id']}" for x in NEWEST_FIRST if x["customer_id"] == "c-5"])

    def test_bad_cursor_and_limit(self):
        app = self.app()
        cursor = self.get(app, "/v2/orders", status="paid", limit=5).body["next_cursor"]
        for query, field in (({"cursor": cursor}, "cursor"), ({"cursor": "nope!!"}, "cursor"),
                             ({"cursor": cursor, "status": "shipped"}, "cursor"),
                             ({"limit": 0}, "limit"), ({"limit": 101}, "limit"),
                             ({"limit": "lots"}, "limit")):
            r = self.get(app, "/v2/orders", **query)
            self.assertEqual(r.status, 400, query)
            self.assertEqual((r.body["error"]["code"], r.body["error"].get("field")),
                             ("invalid_request", field), query)


GOOD = {"customer_id": "c-42", "currency": "EUR",
        "lines": [{"sku": "A2", "quantity": 3, "unit_price": 1999},
                  {"sku": "D9", "quantity": 1, "unit_price": 10}]}


class V2Create(Fixture, unittest.TestCase):
    def post(self, app, body, key=None, header="Idempotency-Key"):
        return app.dispatch("POST", "/v2/orders", {header: key} if key else {}, {}, body)

    def test_create(self):
        r = self.post(self.app(), GOOD)
        self.assertEqual(r.status, 201)
        self.assertEqual(r.body, {
            "id": "ord_241", "customer_id": "c-42", "currency": "EUR",
            "lines": [{"sku": "A2", "quantity": 3, "unit_price": {"amount": 1999, "currency": "EUR"}},
                      {"sku": "D9", "quantity": 1, "unit_price": {"amount": 10, "currency": "EUR"}}],
            "total": {"amount": 6007, "currency": "EUR"}, "status": "pending",
            "created_at": "2026-05-01T12:00:00Z"})

    def test_validation(self):
        app = self.app()
        line = GOOD["lines"][0]
        cases = [
            ({**GOOD, "customer_id": ""}, "customer_id"),
            ({**GOOD, "customer_id": 5, "currency": "eur"}, "customer_id"),
            ({**GOOD, "currency": "eur"}, "currency"),
            ({**GOOD, "currency": "EURO"}, "currency"),
            ({**GOOD, "lines": []}, "lines"),
            ({**GOOD, "lines": "A1"}, "lines"),
            ({**GOOD, "lines": [line, {**line, "sku": ""}]}, "lines[1].sku"),
            ({**GOOD, "lines": [{**line, "quantity": 0, "unit_price": -1}]}, "lines[0].quantity"),
            ({**GOOD, "lines": [{**line, "quantity": 2.0}]}, "lines[0].quantity"),
            ({**GOOD, "lines": [{**line, "quantity": True}]}, "lines[0].quantity"),
            ({**GOOD, "lines": [{**line, "unit_price": "19.99"}]}, "lines[0].unit_price"),
            ({**GOOD, "lines": [{**line, "unit_price": False}]}, "lines[0].unit_price"),
        ]
        for body, field in cases:
            r = self.post(app, body)
            self.assertEqual(r.status, 400, body)
            self.assertEqual((r.body["error"]["code"], r.body["error"].get("field")),
                             ("invalid_request", field), body)
        self.assertEqual(len(app.store.all()), 240)

    def test_replay(self):
        app = self.app()
        first = self.post(app, GOOD, "k-1")
        again = self.post(app, json.loads(json.dumps(GOOD)), "k-1")
        self.assertEqual((again.status, again.body), (first.status, first.body))
        self.assertEqual(again.headers.get("Idempotent-Replayed"), "true")
        self.assertEqual(len(app.store.all()), 241)

    def test_conflict(self):
        app = self.app()
        self.post(app, GOOD, "k-2")
        r = self.post(app, {**GOOD, "currency": "USD"}, "k-2")
        self.assertEqual((r.status, r.body["error"]["code"]), (422, "idempotency_conflict"))
        self.assertEqual(len(app.store.all()), 241)

    def test_keys_survive_a_restart(self):
        first = self.post(self.app(), GOOD, "k-3")
        again = self.post(self.app(), GOOD, "k-3")
        self.assertEqual((again.status, again.body), (201, first.body))
        self.assertEqual(len(self.app().store.all()), 241)

    def test_header_name_in_any_case(self):
        app = self.app()
        self.post(app, GOOD, "k-4", header="idempotency-key")
        again = self.post(app, GOOD, "k-4", header="IDEMPOTENCY-KEY")
        self.assertEqual(again.headers.get("Idempotent-Replayed"), "true")
        self.assertEqual(len(app.store.all()), 241)

    def test_invalid_request_does_not_use_the_key(self):
        app = self.app()
        bad = self.post(app, {**GOOD, "lines": []}, "k-5")
        self.assertEqual(bad.status, 400)
        good = self.post(app, GOOD, "k-5")
        self.assertEqual(good.status, 201)

    def test_v1_sees_v2_orders(self):
        app = self.app()
        self.post(app, GOOD)
        r = app.dispatch("GET", "/v1/orders/241", {}, {}, None)
        self.assertEqual(r.body, {"id": 241, "customer": "c-42", "items": [
            {"sku": "A2", "qty": 3, "price": "19.99"}, {"sku": "D9", "qty": 1, "price": "0.10"}],
            "total": "60.07", "status": "pending", "created": "2026-05-01 12:00:00"})


class V2Cancel(Fixture, unittest.TestCase):
    def test_cancel_rules(self):
        app = self.app()
        paid = next(r for r in MIGRATED if r["status"] == "paid")
        shipped = next(r for r in MIGRATED if r["status"] == "shipped")
        r = app.dispatch("POST", f"/v2/orders/ord_{paid['id']}/cancel", {}, {}, None)
        self.assertEqual((r.status, r.body["status"]), (200, "cancelled"))
        again = app.dispatch("POST", f"/v2/orders/ord_{paid['id']}/cancel", {}, {}, None)
        self.assertEqual((again.status, again.body), (200, r.body))
        r = app.dispatch("POST", f"/v2/orders/ord_{shipped['id']}/cancel", {}, {}, None)
        self.assertEqual((r.status, r.body["error"]["code"]), (409, "invalid_state"))
        r = app.dispatch("POST", "/v2/orders/ord_9999/cancel", {}, {}, None)
        self.assertEqual((r.status, r.body["error"]["code"]), (404, "not_found"))

    def test_methods(self):
        app = self.app()
        for method, path in (("GET", "/v2/orders/ord_1/cancel"), ("PUT", "/v2/orders"),
                             ("DELETE", "/v2/orders/ord_1")):
            r = app.dispatch(method, path, {}, {}, None)
            self.assertEqual((r.status, r.body["error"]["code"]), (405, "method_not_allowed"), path)


class Sdk(Fixture, unittest.TestCase):
    def client(self, transport=None, **kw):
        from shop.client import ClientV2

        return ClientV2(transport or self.app().dispatch, **kw)

    def test_iter_orders(self):
        got = list(self.client().iter_orders(limit=9))
        self.assertEqual([o["id"] for o in got], [f"ord_{x['id']}" for x in NEWEST_FIRST])
        got = list(self.client().iter_orders(status="shipped", limit=4))
        self.assertEqual(len(got), sum(x["status"] == "shipped" for x in MIGRATED))

    def test_lost_response_is_retried_with_the_same_key(self):
        app, calls = self.app(), []

        def flaky(method, path, headers, query, body):
            calls.append(dict(headers))
            r = app.dispatch(method, path, headers, query, body)
            if len(calls) == 1:
                raise ConnectionError("the response was lost")
            return r

        order = self.client(flaky).create_order("c-9", "USD", [{"sku": "A1", "quantity": 2, "unit_price": 999}])
        self.assertEqual(order["id"], "ord_241")
        self.assertEqual(len(app.store.all()), 241)
        keys = [next(v for k, v in h.items() if k.lower() == "idempotency-key") for h in calls]
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1])
        uuid.UUID(keys[0])

    def test_503_is_retried(self):
        from shop.app import Response

        app, calls = self.app(), []

        def busy(method, path, headers, query, body):
            calls.append(1)
            if len(calls) < 3:
                return Response(503, {"error": {"code": "unavailable", "message": "busy"}})
            return app.dispatch(method, path, headers, query, body)

        order = self.client(busy, retries=2).create_order("c-9", "USD", [{"sku": "A1", "quantity": 1, "unit_price": 5}])
        self.assertEqual((len(calls), order["total"]["amount"]), (3, 5))

    def test_gives_up(self):
        calls = []

        def down(*args):
            calls.append(1)
            raise ConnectionError("down")

        with self.assertRaises(ConnectionError):
            self.client(down, retries=1).create_order("c-9", "USD", [{"sku": "A1", "quantity": 1, "unit_price": 5}])
        self.assertEqual(len(calls), 2)

    def test_errors(self):
        from shop.client import ApiError

        with self.assertRaises(ApiError) as caught:
            self.client().get_order("ord_9999")
        self.assertEqual((caught.exception.status, caught.exception.code), (404, "not_found"))
        shipped = next(r for r in MIGRATED if r["status"] == "shipped")
        with self.assertRaises(ApiError) as caught:
            self.client().cancel_order(f"ord_{shipped['id']}")
        self.assertEqual(caught.exception.code, "invalid_state")


if __name__ == "__main__":
    unittest.main()
