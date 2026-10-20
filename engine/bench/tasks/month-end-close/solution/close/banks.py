"""Bank statements (CLOSE_POLICY.md, section 1)."""

import csv
import re
from datetime import date, datetime
from decimal import Decimal


def _us_amount(text):
    text = text.strip().replace(",", "")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    value = Decimal(text)
    return -value if negative else value


def _de_amount(text):
    return Decimal(text.strip().replace(".", "").replace(",", "."))


def _date(text, fmt):
    try:
        return datetime.strptime(text.strip(), fmt).date()
    except ValueError:
        return None


def load_northwind(path):
    lines = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            day = _date(row.get("Date") or "", "%m/%d/%Y")
            if day is None:
                continue
            n = len(lines) + 1
            lines.append({"id": f"northwind:{n}", "bank": "northwind", "date": day,
                          "description": (row["Description"] or "").strip(),
                          "amount": _us_amount(row["Amount"]), "currency": "USD",
                          "balance": _us_amount(row["Balance"])})
    return lines


def load_eurobank(path):
    lines = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter=";"):
            day = _date(row["Buchungstag"], "%d.%m.%Y")
            if day is None:
                continue
            n = len(lines) + 1
            lines.append({"id": f"eurobank:{n}", "bank": "eurobank", "date": day,
                          "description": row["Verwendungszweck"].strip(),
                          "amount": _de_amount(row["Betrag"]), "currency": "EUR"})
    return lines


REFERENCE = re.compile(r"\b(INV|BILL)-(\d+)\b", re.IGNORECASE)


def references(description):
    """Item ids in a description, in order, each once."""
    seen = []
    for kind, digits in REFERENCE.findall(description):
        ref = f"{kind.upper()}-{digits}"
        if ref not in seen:
            seen.append(ref)
    return seen


def is_bank_charge(description):
    text = description.strip().upper()
    return "ACCOUNT FEE" in text or text.startswith("FEE")


__all__ = ["load_northwind", "load_eurobank", "references", "is_bank_charge", "date"]
