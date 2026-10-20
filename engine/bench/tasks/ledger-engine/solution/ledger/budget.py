"""Budgets from periodic transactions (LEDGER.md section 6)."""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .amounts import MixedAmount
from .dates import period_start, periods, step
from .reports import sort_key


@dataclass
class BudgetRow:
    month: date
    account: str
    budget: MixedAmount
    actual: MixedAmount
    percent: int | None
    over: bool


def _percent(budget, actual):
    b, a = budget.amounts(), actual.amounts()
    if len(b) != 1 or len(a) > 1:
        return None
    if a and a[0].commodity != b[0].commodity:
        return None
    if b[0].quantity == 0:
        return None
    aq = a[0].quantity if a else Decimal(0)
    return int((aq / b[0].quantity * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def budget_report(journal, begin, end):
    months = periods("monthly", begin, end)
    accounts = sorted({p.account for pt in journal.periodic for p in pt.postings if not p.inferred},
                      key=sort_key)
    budget = {(m, a): MixedAmount() for m, _ in months for a in accounts}

    def month_of(d):
        for s, e in months:
            if s <= d < e:
                return s
        return None

    for pt in journal.periodic:
        start = pt.start or period_start(begin, pt.interval)
        k = 0
        while True:
            d = step(start, pt.interval, k)
            if d >= end:
                break
            k += 1
            if d < begin:
                continue
            m = month_of(d)
            if m is None:
                continue
            for p in pt.postings:
                if not p.inferred:
                    budget[(m, p.account)] = budget[(m, p.account)] + p.amount
    actual = {key: MixedAmount() for key in budget}
    for t in journal.transactions:
        m = month_of(t.date) if begin <= t.date < end else None
        if m is None:
            continue
        for p in t.postings:
            for a in accounts:
                if p.account == a or p.account.startswith(a + ":"):
                    actual[(m, a)] = actual[(m, a)] + p.amount
    rows = []
    for m, _ in months:
        for a in accounts:
            pct = _percent(budget[(m, a)], actual[(m, a)])
            rows.append(BudgetRow(m, a, budget[(m, a)], actual[(m, a)], pct,
                                  pct is not None and pct > 100))
    return rows
