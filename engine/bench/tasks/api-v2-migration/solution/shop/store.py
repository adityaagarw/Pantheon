"""Orders, kept in a JSON Lines file (one order per line), in schema 2 (API_V2.md).

Rows written before v2 are read and converted; everything is written in schema 2.
Idempotency keys live next to the orders file, in idempotency.json.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


def to_minor(value):
    cents = Decimal(str(value)) * 100
    if cents != cents.to_integral_value():
        raise ValueError(f"{value} has more than two decimals")
    return int(cents)


def utc_text(value):
    """A stored time (v1: "2026-01-05 10:22:01", maybe with an offset) as ...Z."""
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def upgrade(row):
    """A stored row in schema 2 (it may already be)."""
    if row.get("schema") == 2:
        return row
    lines = [{"sku": i["sku"], "quantity": int(i["qty"]), "unit_price": to_minor(i["price"])}
             for i in row["items"]]
    return {
        "schema": 2,
        "id": row["id"],
        "customer_id": row["customer"],
        "currency": row.get("currency") or "USD",
        "lines": lines,
        "total": sum(l["quantity"] * l["unit_price"] for l in lines),
        "status": {"canceled": "cancelled"}.get(row["status"], row["status"]),
        "created_at": utc_text(row["created"]),
    }


def write_atomically(path, text):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def dump(orders):
    lines = [json.dumps(orders[k], sort_keys=True) for k in sorted(orders)]
    return "\n".join(lines) + ("\n" if lines else "")


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.orders = {}
        self.legacy = 0  # rows read in the old shape
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.legacy += row.get("schema") != 2
                    record = upgrade(row)
                    self.orders[record["id"]] = record
        self.keys_path = self.path.with_name("idempotency.json")
        self.keys = (json.loads(self.keys_path.read_text(encoding="utf-8"))
                     if self.keys_path.exists() else {})

    def all(self):
        return [self.orders[k] for k in sorted(self.orders)]

    def get(self, order_id):
        return self.orders.get(order_id)

    def add(self, record):
        record = dict(record)
        record["id"] = max(self.orders, default=0) + 1
        self.orders[record["id"]] = record
        self.save()
        return record

    def update(self, record):
        self.orders[record["id"]] = record
        self.save()

    def save(self):
        write_atomically(self.path, dump(self.orders))

    def remember_key(self, key, entry):
        self.keys[key] = entry
        write_atomically(self.keys_path, json.dumps(self.keys, sort_keys=True))
