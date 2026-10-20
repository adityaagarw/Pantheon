"""The month-end close (CLOSE_POLICY.md, section 5)."""

import json
from decimal import Decimal
from pathlib import Path

from close.banks import load_eurobank, load_northwind
from close.fx import Rates
from close.ledger import BUCKETS, bucket, load_aliases, load_items
from close.matching import match
from close.money import cents, fmt


def run(data_dir, close_date):
    data = Path(data_dir)
    northwind = load_northwind(data / "bank_northwind.csv")
    eurobank = load_eurobank(data / "bank_eurobank.csv")
    items = load_items(data / "ledger.csv")
    aliases = load_aliases(data / "counterparties.json")
    rates = Rates.load(data / "fx.csv")
    opening = {k: Decimal(v) for k, v in
               json.loads((data / "opening_balances.json").read_text(encoding="utf-8")).items()}

    result = match(northwind + eurobank, items, aliases)

    fees = Decimal(0)
    for line, fee in result["fees"]:
        fees += rates.to_usd(fee, line["currency"], line["date"])
    for line in result["bank_charges"]:
        fees += rates.to_usd(abs(line["amount"]), line["currency"], line["date"])

    fx = Decimal(0)
    for line, item, amount in result["applied"]:
        if item["currency"] != "EUR":
            continue
        diff = cents(amount * (rates.rate(line["date"]) - rates.rate(item["issued"])))
        fx += diff if item["kind"] == "invoice" else -diff

    open_items, aging = [], {"invoice": dict.fromkeys(BUCKETS, Decimal(0)),
                             "bill": dict.fromkeys(BUCKETS, Decimal(0))}
    for item in sorted(items, key=lambda i: i["id"]):
        if item["open"] <= 0:
            continue
        b = bucket(item["due"], close_date)
        open_items.append({"id": item["id"], "kind": item["kind"],
                           "counterparty": item["counterparty"], "currency": item["currency"],
                           "open": fmt(item["open"]), "bucket": b})
        aging[item["kind"]][b] += rates.to_usd(item["open"], item["currency"], close_date)

    cash = {}
    for bank, lines, currency in (("northwind", northwind, "USD"), ("eurobank", eurobank, "EUR")):
        closing = opening[bank] + sum((l["amount"] for l in lines), Decimal(0))
        cash[bank] = {"currency": currency, "opening": fmt(opening[bank]), "closing": fmt(closing)}
    if northwind:
        newest = max(northwind, key=lambda l: (l["date"], -int(l["id"].split(":")[1])))
        if newest["balance"] != Decimal(cash["northwind"]["closing"]):
            raise ValueError(f"Northwind's statement ends at {newest['balance']}, "
                             f"but the lines add up to {cash['northwind']['closing']}")
    total = sum((rates.to_usd(Decimal(c["closing"]), c["currency"], close_date)
                 for c in cash.values()), Decimal(0))

    return {
        "period_end": close_date.isoformat(),
        "bank_lines": len(northwind) + len(eurobank),
        "matches": [{"line": m["line"]["id"], "kind": m["kind"],
                     "items": {i: fmt(a) for i, a in m["items"]}} for m in result["matches"]],
        "bank_charges": [l["id"] for l in result["bank_charges"]],
        "unmatched": [{"line": u["line"]["id"], "reason": u["reason"]} for u in result["unmatched"]],
        "overpayments": [{"line": o["line"]["id"], "counterparty": o["counterparty"],
                          "currency": o["currency"], "amount": fmt(o["amount"])}
                         for o in result["overpayments"]],
        "open_items": open_items,
        "ar_aging_usd": {b: fmt(v) for b, v in aging["invoice"].items()},
        "ap_aging_usd": {b: fmt(v) for b, v in aging["bill"].items()},
        "fees_usd": fmt(fees),
        "fx_gain_loss_usd": fmt(fx),
        "cash": cash,
        "cash_usd_total": fmt(total),
    }
