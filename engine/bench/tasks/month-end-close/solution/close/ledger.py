"""The ledger, counterparties and aging (CLOSE_POLICY.md, sections 2 and 5)."""

import csv
import json
from datetime import date
from decimal import Decimal

BUCKETS = ["current", "1-30", "31-60", "61-90", "90+"]


def load_items(path):
    items = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            amount, paid = Decimal(row["amount"]), Decimal(row["paid"] or "0")
            items.append({"id": row["id"].strip(), "kind": row["kind"].strip(),
                          "counterparty": row["counterparty"].strip(),
                          "currency": row["currency"].strip(), "amount": amount,
                          "open": amount - paid, "issued": date.fromisoformat(row["issued"]),
                          "due": date.fromisoformat(row["due"])})
    return items


def load_aliases(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def bucket(due, close_date):
    late = (close_date - due).days
    if late <= 0:
        return "current"
    if late <= 30:
        return "1-30"
    if late <= 60:
        return "31-60"
    if late <= 90:
        return "61-90"
    return "90+"
