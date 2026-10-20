"""Reports (LEDGER.md section 5)."""

from dataclasses import dataclass, field
from datetime import date, timedelta

from .amounts import MixedAmount
from .dates import periods as _periods
from .prices import PriceDB
from .query import Query


@dataclass
class BalanceRow:
    account: str
    amount: MixedAmount


@dataclass
class BalanceReport:
    rows: list = field(default_factory=list)
    total: MixedAmount = field(default_factory=MixedAmount)

    def as_dict(self):
        return {r.account: r.amount for r in self.rows}


@dataclass
class RegisterRow:
    date: date
    payee: str
    account: str
    amount: MixedAmount
    total: MixedAmount


@dataclass
class IncomeStatement:
    revenues: BalanceReport
    expenses: BalanceReport
    net: MixedAmount


@dataclass
class PeriodReport:
    periods: list
    rows: list
    totals: list


def sort_key(account):
    return account.split(":")


def _query(query):
    if isinstance(query, Query):
        return query
    return Query(query or [])


def postings(journal, query=None, begin=None, end=None):
    q = _query(query)
    for t in journal.transactions:
        if begin is not None and t.date < begin:
            continue
        if end is not None and t.date >= end:
            continue
        for p in t.postings:
            if q.matches(p):
                yield p


def _clip(account, depth):
    return ":".join(account.split(":")[:depth]) if depth else account


def _report(journal, ps, depth=None, flat=False, empty=False):
    prec = journal.precisions
    own = {}
    total = MixedAmount()
    for p in ps:
        acct = _clip(p.account, depth)
        own[acct] = own.get(acct, MixedAmount()) + p.amount
        total = total + p.amount
    if flat:
        rows = [BalanceRow(a, own[a]) for a in sorted(own, key=sort_key)
                if empty or not own[a].is_zero(prec)]
        return BalanceReport(rows, total)
    incl = {}
    for acct, amt in own.items():
        segs = acct.split(":")
        for i in range(1, len(segs) + 1):
            name = ":".join(segs[:i])
            incl[name] = incl.get(name, MixedAmount()) + amt
    shown = {a for a, amt in incl.items() if empty or not amt.is_zero(prec)}
    for a in list(shown):
        segs = a.split(":")
        for i in range(1, len(segs)):
            shown.add(":".join(segs[:i]))
    rows = [BalanceRow(a, incl[a]) for a in sorted(shown, key=sort_key)]
    return BalanceReport(rows, total)


def _value_date(journal, end):
    if end is not None:
        return end - timedelta(days=1)
    return journal.transactions[-1].date if journal.transactions else date.today()


def balance(journal, query=None, begin=None, end=None, depth=None, flat=False, empty=False,
            value=None):
    rep = _report(journal, postings(journal, query, begin, end), depth, flat, empty)
    if value:
        db, on = PriceDB(journal), _value_date(journal, end)
        rep = BalanceReport([BalanceRow(r.account, db.value(r.amount, value, on)) for r in rep.rows],
                            db.value(rep.total, value, on))
    return rep


def register(journal, query=None, begin=None, end=None):
    rows, running = [], MixedAmount()
    for p in postings(journal, query, begin, end):
        amt = MixedAmount([p.amount])
        running = running + amt
        rows.append(RegisterRow(p.transaction.date, p.transaction.payee, p.account, amt, running))
    return rows


def _top(account):
    return account.split(":", 1)[0].lower()


def income_statement(journal, begin=None, end=None, depth=None):
    ps = list(postings(journal, None, begin, end))
    rev = _report(journal, [p for p in ps if _top(p.account) in ("income", "revenue", "revenues")], depth)
    rev = BalanceReport([BalanceRow(r.account, -r.amount) for r in rev.rows], -rev.total)
    exp = _report(journal, [p for p in ps if _top(p.account) in ("expenses", "expense")], depth)
    return IncomeStatement(rev, exp, rev.total - exp.total)


def balance_by_period(journal, interval, query=None, begin=None, end=None, depth=None):
    if begin is None:
        begin = journal.transactions[0].date if journal.transactions else date.today()
    if end is None:
        end = (journal.transactions[-1].date if journal.transactions else begin) + timedelta(days=1)
    spans = _periods(interval, begin, end)
    if not spans:
        return PeriodReport([], [], [])
    lo, hi = spans[0][0], spans[-1][1]
    cells, totals = {}, [MixedAmount() for _ in spans]
    for p in postings(journal, query, lo, hi):
        i = next(k for k, (s, e) in enumerate(spans) if s <= p.transaction.date < e)
        acct = _clip(p.account, depth)
        row = cells.setdefault(acct, [MixedAmount() for _ in spans])
        row[i] = row[i] + p.amount
        totals[i] = totals[i] + p.amount
    prec = journal.precisions
    rows = [(a, cells[a]) for a in sorted(cells, key=sort_key)
            if not all(m.is_zero(prec) for m in cells[a])]
    return PeriodReport(spans, rows, totals)
