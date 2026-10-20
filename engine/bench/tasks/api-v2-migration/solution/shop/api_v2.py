"""The v2 orders API (API_V2.md)."""

import base64
import json
import re
from datetime import datetime, timezone

ID = re.compile(r"^ord_([1-9][0-9]*)$")
CURRENCY = re.compile(r"^[A-Z]{3}$")


def error(status, code, message, field=None):
    body = {"error": {"code": code, "message": message}}
    if field:
        body["error"]["field"] = field
    return status, body, {}


def resource(record):
    cur = record["currency"]
    return {
        "id": f"ord_{record['id']}",
        "customer_id": record["customer_id"],
        "currency": cur,
        "lines": [{"sku": l["sku"], "quantity": l["quantity"],
                   "unit_price": {"amount": l["unit_price"], "currency": cur}}
                  for l in record["lines"]],
        "total": {"amount": record["total"], "currency": cur},
        "status": record["status"],
        "created_at": record["created_at"],
    }


def _cursor(record, filters, newest):
    raw = json.dumps({"c": record["created_at"], "i": record["id"], "f": filters, "m": newest},
                     sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _read_cursor(text):
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
        data = json.loads(raw)
        return str(data["c"]), int(data["i"]), dict(data["f"]), int(data["m"])
    except (ValueError, KeyError, TypeError):
        return None


def list_orders(store, query):
    try:
        limit = int(query.get("limit", 25))
        if isinstance(query.get("limit"), bool) or not 1 <= limit <= 100:
            raise ValueError
    except (TypeError, ValueError):
        return error(400, "invalid_request", "limit must be an integer from 1 to 100", "limit")
    filters = {k: query[k] for k in ("status", "customer_id") if query.get(k)}
    orders = sorted((r for r in store.all()
                     if all(r.get(k) == v for k, v in filters.items())),
                    key=lambda r: (r["created_at"], r["id"]), reverse=True)
    newest = max((r["id"] for r in store.all()), default=0)
    if query.get("cursor"):
        parsed = _read_cursor(str(query["cursor"]))
        if parsed is None or parsed[2] != filters:
            return error(400, "invalid_request", "this cursor isn't valid for this query", "cursor")
        created, last_id, _, newest = parsed
        orders = [r for r in orders if r["id"] <= newest
                  and (r["created_at"], r["id"]) < (created, last_id)]
    page = orders[:limit]
    more = len(orders) > limit
    return 200, {"data": [resource(r) for r in page],
                 "next_cursor": _cursor(page[-1], filters, newest) if more else None}, {}


def _validate(body):
    if not isinstance(body, dict):
        return "body", "the body must be an object"
    if not isinstance(body.get("customer_id"), str) or not body["customer_id"]:
        return "customer_id", "customer_id must be a non-empty string"
    if not isinstance(body.get("currency"), str) or not CURRENCY.match(body["currency"]):
        return "currency", "currency must be three uppercase letters"
    lines = body.get("lines")
    if not isinstance(lines, list) or not lines:
        return "lines", "lines must be a non-empty list"
    for n, line in enumerate(lines):
        if not isinstance(line, dict):
            return f"lines[{n}]", "each line must be an object"
        if not isinstance(line.get("sku"), str) or not line["sku"]:
            return f"lines[{n}].sku", "sku must be a non-empty string"
        q = line.get("quantity")
        if isinstance(q, bool) or not isinstance(q, int) or q < 1:
            return f"lines[{n}].quantity", "quantity must be an integer of at least 1"
        p = line.get("unit_price")
        if isinstance(p, bool) or not isinstance(p, int) or p < 0:
            return f"lines[{n}].unit_price", "unit_price must be an integer of at least 0"
    return None


def create_order(store, headers, body, now):
    key = next((v for k, v in headers.items() if k.lower() == "idempotency-key"), None)
    canonical = json.dumps(body, sort_keys=True)
    if key is not None and key in store.keys:
        seen = store.keys[key]
        if seen["body"] != canonical:
            return error(422, "idempotency_conflict",
                         "this Idempotency-Key was used with a different body")
        return seen["status"], seen["response"], {"Idempotent-Replayed": "true"}
    problem = _validate(body)
    if problem:
        return error(400, "invalid_request", problem[1], problem[0])
    lines = [{"sku": l["sku"], "quantity": l["quantity"], "unit_price": l["unit_price"]}
             for l in body["lines"]]
    record = store.add({"schema": 2, "customer_id": body["customer_id"],
                        "currency": body["currency"], "lines": lines,
                        "total": sum(l["quantity"] * l["unit_price"] for l in lines),
                        "status": "pending",
                        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
    response = resource(record)
    if key is not None:
        store.remember_key(key, {"body": canonical, "status": 201, "response": response})
    return 201, response, {}


def handle(store, method, path, headers=None, query=None, body=None, now=None):
    """Answer a v2 request: returns (status, body, extra headers)."""
    headers, query = headers or {}, query or {}
    now = now or datetime.now(timezone.utc)
    parts = [p for p in path.split("/") if p]
    if parts[:2] != ["v2", "orders"] or len(parts) > 4:
        return error(404, "not_found", "no such route")
    if len(parts) == 2:
        if method == "GET":
            return list_orders(store, query)
        if method == "POST":
            return create_order(store, headers, body, now)
        return error(405, "method_not_allowed", f"{method} isn't allowed here")
    m = ID.match(parts[2])
    record = store.get(int(m.group(1))) if m else None
    if len(parts) == 4 and parts[3] != "cancel":
        return error(404, "not_found", "no such route")
    if record is None:
        return error(404, "not_found", "no such order")
    if len(parts) == 3:
        if method != "GET":
            return error(405, "method_not_allowed", f"{method} isn't allowed here")
        return 200, resource(record), {}
    if method != "POST":
        return error(405, "method_not_allowed", f"{method} isn't allowed here")
    if record["status"] == "cancelled":
        return 200, resource(record), {}
    if record["status"] not in ("pending", "paid"):
        return error(409, "invalid_state", f"a {record['status']} order can't be cancelled")
    record = {**record, "status": "cancelled"}
    store.update(record)
    return 200, resource(record), {}
