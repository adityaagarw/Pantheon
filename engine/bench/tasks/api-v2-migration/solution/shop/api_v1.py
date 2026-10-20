"""The v1 orders API. Other teams depend on it: its responses must not change.

It reads and writes schema 2 records (shop/store.py) and shows them the way v1 always has.
"""

from datetime import datetime, timezone
from decimal import Decimal

from shop.store import to_minor


def _money(minor):
    return f"{Decimal(minor) / 100:.2f}"


def serialize(record):
    created = datetime.strptime(record["created_at"], "%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": record["id"],
        "customer": record["customer_id"],
        "items": [{"sku": l["sku"], "qty": l["quantity"], "price": _money(l["unit_price"])}
                  for l in record["lines"]],
        "total": _money(record["total"]),
        "status": record["status"],
        "created": created.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def handle(store, method, path, query=None, body=None, now=None):
    """Answer a v1 request: returns (status code, body)."""
    query = query or {}
    parts = [p for p in path.split("/") if p]
    if parts[:2] != ["v1", "orders"]:
        return 404, {"error": "not found"}
    if method == "GET" and len(parts) == 2:
        page = max(1, _int(query.get("page"), 1))
        per_page = min(100, max(1, _int(query.get("per_page"), 20)))
        orders = [serialize(r) for r in store.all()]
        if query.get("status"):
            orders = [o for o in orders if o["status"] == query["status"]]
        start = (page - 1) * per_page
        return 200, {"orders": orders[start:start + per_page], "page": page,
                     "per_page": per_page, "total": len(orders)}
    if len(parts) >= 3:
        record = store.get(_int(parts[2], -1))
        if record is None:
            return 404, {"error": "not found"}
        if method == "GET" and len(parts) == 3:
            return 200, serialize(record)
        if method == "POST" and parts[3:] == ["cancel"]:
            if record["status"] not in ("pending", "paid"):
                return 409, {"error": f"cannot cancel a {record['status']} order"}
            record = {**record, "status": "cancelled"}
            store.update(record)
            return 200, serialize(record)
        return 404, {"error": "not found"}
    if method == "POST" and len(parts) == 2:
        body = body or {}
        items = body.get("items")
        if not body.get("customer") or not isinstance(items, list) or not items:
            return 400, {"error": "customer and items are required"}
        try:
            lines = [{"sku": str(i["sku"]), "quantity": int(i["qty"]),
                      "unit_price": to_minor(f"{Decimal(str(i['price'])):.2f}")} for i in items]
        except (KeyError, TypeError, ValueError, ArithmeticError):
            return 400, {"error": "each item needs sku, qty and price"}
        if any(l["quantity"] < 1 for l in lines):
            return 400, {"error": "qty must be at least 1"}
        created = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
        record = store.add({"schema": 2, "customer_id": str(body["customer"]), "currency": "USD",
                            "lines": lines,
                            "total": sum(l["quantity"] * l["unit_price"] for l in lines),
                            "status": "pending", "created_at": created})
        return 201, serialize(record)
    return 405, {"error": "method not allowed"}
