"""Clean the quarterly sales CSV and build a summary. See SPEC.md."""

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

FIELDS = ["order_id", "order_date", "customer", "region", "product", "quantity", "unit_price", "status"]
STATUSES = {"completed", "refunded", "cancelled", "pending"}
REGIONS = {
    "north america": "North America", "na": "North America", "n america": "North America",
    "europe": "Europe", "eu": "Europe", "emea": "Europe",
    "asia pacific": "Asia Pacific", "apac": "Asia Pacific", "asia": "Asia Pacific",
    "latin america": "Latin America", "latam": "Latin America",
}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun",
                                      "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DATE_PATTERNS = [
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$"), ("y", "m", "d")),
    (re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$"), ("y", "m", "d")),
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), ("d", "m", "y")),
]
MON_PATTERN = re.compile(r"^([A-Za-z]{3}) (\d{1,2}), (\d{4})$")
PRICE_PATTERN = re.compile(r"^\d+(\.\d{1,2})?$")
CENT = Decimal("0.01")


def parse_date(text):
    for pattern, order in DATE_PATTERNS:
        match = pattern.match(text)
        if match:
            parts = dict(zip(order, map(int, match.groups())))
            return _make_date(parts["y"], parts["m"], parts["d"])
    match = MON_PATTERN.match(text)
    if match and match.group(1).lower() in MONTHS:
        return _make_date(int(match.group(3)), MONTHS[match.group(1).lower()], int(match.group(2)))
    return None


def _make_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_region(text):
    key = re.sub(r"\s+", " ", text.lower().replace(".", "")).strip()
    return REGIONS.get(key)


def parse_quantity(text):
    return int(text) if text.isdigit() and int(text) >= 1 else None


def parse_price(text):
    cleaned = text.replace("$", "").replace(",", "").replace(" ", "")
    if not PRICE_PATTERN.match(cleaned):
        return None
    value = Decimal(cleaned)
    return value if value > 0 else None


def validate(row):
    """Return (reason, None) for an invalid row, or (None, clean_row)."""
    if not row["order_id"]:
        return "order_id", None
    order_date = parse_date(row["order_date"])
    if order_date is None:
        return "order_date", None
    region = parse_region(row["region"])
    if region is None:
        return "region", None
    if not row["product"]:
        return "product", None
    quantity = parse_quantity(row["quantity"])
    if quantity is None:
        return "quantity", None
    price = parse_price(row["unit_price"])
    if price is None:
        return "unit_price", None
    status = row["status"].lower()
    if status not in STATUSES:
        return "status", None
    return None, {"date": order_date, "region": region, "product": row["product"],
                  "quantity": quantity, "price": price, "status": status}


def money(value):
    return str(Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP))


def read_rows(csv_path):
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for raw in reader:
            rows.append({k: (raw.get(k) or "").strip() for k in FIELDS})
    return rows


def deduplicate(rows):
    last = {}
    for index, row in enumerate(rows):
        if row["order_id"]:
            last[row["order_id"]] = index
    kept = [row for index, row in enumerate(rows) if not row["order_id"] or last[row["order_id"]] == index]
    return kept, len(rows) - len(kept)


def build_summary(csv_path):
    rows = read_rows(csv_path)
    kept, duplicates = deduplicate(rows)
    reasons = Counter()
    statuses = Counter()
    by_region = defaultdict(Decimal)
    by_month = defaultdict(Decimal)
    by_region_month = defaultdict(lambda: defaultdict(Decimal))
    products = defaultdict(lambda: [Decimal(0), 0])
    valid = 0
    for row in kept:
        reason, clean = validate(row)
        if reason:
            reasons[reason] += 1
            continue
        valid += 1
        statuses[clean["status"]] += 1
        if clean["status"] != "completed":
            continue
        revenue = clean["price"] * clean["quantity"]
        month = clean["date"].strftime("%Y-%m")
        by_region[clean["region"]] += revenue
        by_month[month] += revenue
        by_region_month[clean["region"]][month] += revenue
        products[clean["product"]][0] += revenue
        products[clean["product"]][1] += clean["quantity"]

    top = sorted(products.items(), key=lambda kv: (-kv[1][0], kv[0]))[:3]
    return {
        "rows_read": len(rows),
        "duplicates_removed": duplicates,
        "valid_rows": valid,
        "invalid_rows": sum(reasons.values()),
        "invalid_reasons": dict(sorted(reasons.items())),
        "status_counts": dict(sorted(statuses.items())),
        "total_revenue": money(sum(by_region.values(), Decimal(0))),
        "revenue_by_region": {k: money(v) for k, v in sorted(by_region.items())},
        "revenue_by_month": {k: money(v) for k, v in sorted(by_month.items())},
        "revenue_by_region_month": {
            region: {m: money(v) for m, v in sorted(months.items())}
            for region, months in sorted(by_region_month.items())
        },
        "top_products": [{"product": name, "revenue": money(rev), "units": units} for name, (rev, units) in top],
    }


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: python -m sales.pipeline INPUT.csv OUTPUT.json", file=sys.stderr)
        return 2
    summary = build_summary(argv[0])
    out = Path(argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
