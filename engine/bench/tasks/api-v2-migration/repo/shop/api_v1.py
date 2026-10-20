"""The v1 orders API. Other teams depend on it: its responses must not change."""

from datetime import datetime, timezone
from decimal import Decimal

STATUSES = {"pending", "paid", "shipped", "cancelled"}


def _money(value):
    return f"{Decimal(str(value)):.2f}"


def _created(value):
    """Stored times are UTC ("2026-01-05 10:22:01"); old rows may carry an offset."""
    text = str(value).replace("T", " ")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return text
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def serialize(record):
    status = {"canceled": "cancelled"}.get(record["status"], record["status"])
    return {
        "id": record["id"],
        "customer": record["customer"],
        "items": [{"sku": i["sku"], "qty": i["qty"], "price": _money(i["price"])}
                  for i in record["items"]],
        "total": _money(record["total"]),
        "status": status,
        "created": _created(record["created"]),
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
            status = serialize(record)["status"]
            if status not in ("pending", "paid"):
                return 409, {"error": f"cannot cancel a {status} order"}
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
            clean = [{"sku": str(i["sku"]), "qty": int(i["qty"]), "price": _money(i["price"])}
                     for i in items]
        except (KeyError, TypeError, ValueError, ArithmeticError):
            return 400, {"error": "each item needs sku, qty and price"}
        if any(i["qty"] < 1 for i in clean):
            return 400, {"error": "qty must be at least 1"}
        total = sum(Decimal(i["price"]) * i["qty"] for i in clean)
        created = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")
        record = store.add({"customer": str(body["customer"]), "items": clean,
                            "total": _money(total), "status": "pending", "created": created})
        return 201, serialize(record)
    return 405, {"error": "method not allowed"}
