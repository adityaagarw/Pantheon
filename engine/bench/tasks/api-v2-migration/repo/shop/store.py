"""Orders, kept in a JSON Lines file (one order per line)."""

import json
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.orders = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    record = json.loads(line)
                    self.orders[record["id"]] = record

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
        lines = [json.dumps(self.orders[k], sort_keys=True) for k in sorted(self.orders)]
        self.path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
