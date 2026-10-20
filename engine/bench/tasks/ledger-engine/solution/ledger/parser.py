"""The journal format (LEDGER.md section 2)."""

import os
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from .amounts import DEFAULT_PRECISION, Amount, MixedAmount, format_amount, parse_amount_ex, round_q
from .dates import parse_date
from .errors import JournalError

_DATE = r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"
_HEADER = re.compile(rf"({_DATE})(?:=\S+)?(?:\s+|$)(?:([*!])\s*)?(?:\(([^)]*)\)\s*)?(.*)")
_TAG = re.compile(r"(?<![\w-])([A-Za-z][\w-]*):([^,]*)")
_PERIOD = re.compile(rf"(weekly|monthly|quarterly|yearly)(?:\s+from\s+({_DATE}))?")
_SPLIT = re.compile(r"\t| {2,}")
DIRECTIVES = ("include", "commodity", "P", "alias", "end", "account")


@dataclass
class Price:
    date: date
    commodity: str
    price: Amount


@dataclass(eq=False)
class Posting:
    account: str
    amount: Amount | None = None
    cost: Amount | None = None
    virtual: bool = False
    status: str = ""
    comment: str = ""
    tags: dict = field(default_factory=dict)
    assertion: Amount | None = None
    transaction: object = None
    line: int = 0
    own_tags: dict = field(default_factory=dict)
    inferred: bool = False

    @property
    def weight(self):
        return self.cost if self.cost is not None else self.amount


@dataclass(eq=False)
class Transaction:
    date: date
    status: str = ""
    code: str = ""
    description: str = ""
    payee: str = ""
    note: str = ""
    comment: str = ""
    tags: dict = field(default_factory=dict)
    postings: list = field(default_factory=list)
    path: str = "-"
    line: int = 0
    seq: int = 0


@dataclass(eq=False)
class Periodic:
    interval: str
    start: date | None = None
    description: str = ""
    postings: list = field(default_factory=list)
    path: str = "-"
    line: int = 0
    comment: str = ""
    tags: dict = field(default_factory=dict)


@dataclass
class Journal:
    transactions: list = field(default_factory=list)
    periodic: list = field(default_factory=list)
    prices: list = field(default_factory=list)
    precisions: dict = field(default_factory=dict)

    def precision(self, commodity):
        return self.precisions.get(commodity, DEFAULT_PRECISION)

    def format(self, amount):
        return format_amount(amount, self.precision(amount.commodity))


def _tags(comment):
    return {m[1]: m[2].strip() for m in _TAG.finditer(comment or "")}


def _read(path):
    return Path(path).read_text(encoding="utf-8")


class _State:
    def __init__(self, loader):
        self.loader = loader or _read
        self.aliases = []
        self.entries = []  # transactions and periodics in file order
        self.prices = []  # (seq, Price or posting) resolved later
        self.p_directives = []  # (seq, Price)
        self.seen = {}  # commodity -> max decimals written
        self.declared = {}
        self.stack = []
        self.seq = 0

    def next_seq(self):
        self.seq += 1
        return self.seq

    def note(self, amount, decimals):
        c = amount.commodity
        self.seen[c] = max(self.seen.get(c, 0), decimals)


def _amount(state, text, path, line):
    try:
        a, decimals = parse_amount_ex(text)
    except ValueError:
        raise JournalError(path, line, f"bad amount: {text.strip()}") from None
    state.note(a, decimals)
    return a


def _alias(state, account):
    for frm, to in state.aliases:
        if account == frm or account.startswith(frm + ":"):
            account = to + account[len(frm):]
    return account


def _posting(state, text, path, line):
    body = text.strip()
    status = ""
    if body[:2] in ("* ", "! "):
        status, body = body[0], body[2:].lstrip()
    comment = ""
    if ";" in body:
        body, comment = body.split(";", 1)
        comment = comment.strip()
    assertion = None
    if "=" in body:
        body, atext = body.split("=", 1)
        assertion = _amount(state, atext, path, line)
    parts = _SPLIT.split(body.rstrip(), maxsplit=1)
    account = parts[0].strip()
    rest = parts[1].strip() if len(parts) > 1 else ""
    if not account:
        raise JournalError(path, line, "posting has no account")
    virtual = False
    if account.startswith("["):
        raise JournalError(path, line, "bracketed virtual postings are not supported")
    if account.startswith("(") and account.endswith(")"):
        virtual, account = True, account[1:-1].strip()
    amount = cost = None
    if rest:
        if "@@" in rest:
            atext, ctext = rest.split("@@", 1)
            total = True
        elif "@" in rest:
            atext, ctext = rest.split("@", 1)
            total = False
        else:
            atext, ctext, total = rest, None, False
        amount = _amount(state, atext, path, line)
        if ctext is not None:
            c = _amount(state, ctext, path, line)
            unit = abs(c.quantity) if total else abs(c.quantity) * abs(amount.quantity)
            sign = -1 if amount.quantity < 0 else 1
            cost = Amount(unit * sign, c.commodity)
    if virtual and amount is None:
        raise JournalError(path, line, "a virtual posting needs an amount")
    own = _tags(comment)
    return Posting(account=_alias(state, account), amount=amount, cost=cost, virtual=virtual,
                   status=status, comment=comment, assertion=assertion, line=line,
                   own_tags=own)


def _parse_text(state, text, path):
    current = None
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\r\n")
        if not line.strip():
            current = None
            continue
        if line[0] in " \t":
            if current is None:
                raise JournalError(path, n, "posting outside a transaction")
            s = line.strip()
            if s.startswith(";"):
                c = s[1:].strip()
                target = current.postings[-1] if current.postings else current
                target.comment = (target.comment + "\n" + c) if target.comment else c
                if isinstance(target, Posting):
                    target.own_tags.update(_tags(c))
                else:
                    target.tags.update(_tags(c))
                continue
            current.postings.append(_posting(state, line, path, n))
            continue
        current = None
        if line[0] in ";#*%":
            continue
        if re.match(_DATE, line):
            m = _HEADER.fullmatch(line)
            if not m:
                raise JournalError(path, n, "unexpected line")
            try:
                d = parse_date(m[1])
            except ValueError:
                raise JournalError(path, n, f"bad date: {m[1]}") from None
            rest = m[4] or ""
            comment = ""
            if ";" in rest:
                rest, comment = rest.split(";", 1)
                comment = comment.strip()
            desc = rest.strip()
            payee, note = (desc.split("|", 1) if "|" in desc else (desc, ""))
            current = Transaction(date=d, status=m[2] or "", code=m[3] if m[3] is not None else "",
                                  description=desc, payee=payee.strip(), note=note.strip(),
                                  comment=comment, tags=_tags(comment), path=path, line=n,
                                  seq=state.next_seq())
            state.entries.append(current)
            continue
        if line.startswith("~"):
            spec = line[1:].strip()
            parts = _SPLIT.split(spec, maxsplit=1)
            m = _PERIOD.fullmatch(parts[0].strip())
            if not m:
                raise JournalError(path, n, "bad period")
            current = Periodic(interval=m[1], start=parse_date(m[2]) if m[2] else None,
                               description=parts[1].strip() if len(parts) > 1 else "",
                               path=path, line=n)
            state.entries.append(current)
            continue
        word = line.split(None, 1)[0]
        arg = line[len(word):].strip()
        if word == "include":
            _include(state, arg, path, n)
        elif word == "commodity":
            try:
                a, decimals = parse_amount_ex(arg)
                state.declared[a.commodity] = decimals
            except ValueError:
                if not re.fullmatch(r"[A-Za-z]+|[^\sA-Za-z0-9]", arg):
                    raise JournalError(path, n, f"bad commodity directive: {arg}") from None
        elif word == "P":
            toks = arg.split(None, 2)
            if len(toks) < 3:
                raise JournalError(path, n, "bad price directive")
            try:
                d = parse_date(toks[0])
            except ValueError:
                raise JournalError(path, n, f"bad date: {toks[0]}") from None
            price = _amount(state, toks[2], path, n)
            state.prices.append((state.next_seq(), Price(d, toks[1], price)))
        elif word == "alias":
            if "=" not in arg:
                raise JournalError(path, n, "bad alias")
            frm, to = (s.strip() for s in arg.split("=", 1))
            state.aliases.append((frm, to))
        elif word == "end" and arg == "aliases":
            state.aliases = []
        elif word == "account":
            pass
        else:
            raise JournalError(path, n, "unexpected line")


def _include(state, arg, path, line):
    base = os.path.dirname(path) if path and path != "-" else ""
    target = arg if os.path.isabs(arg) else os.path.join(base, arg)
    key = os.path.normpath(target)
    if key in state.stack:
        raise JournalError(path, line, f"include cycle: {arg}")
    try:
        text = state.loader(target)
    except OSError as e:
        raise JournalError(path, line, f"can't include {arg}: {e}") from None
    state.stack.append(key)
    try:
        _parse_text(state, text, target)
    finally:
        state.stack.pop()


def _balance(entry, journal):
    real = [p for p in entry.postings if not p.virtual]
    if not entry.postings:
        raise JournalError(entry.path, entry.line, "transaction has no postings")
    missing = [p for p in real if p.amount is None]
    if len(missing) > 1:
        raise JournalError(entry.path, entry.line, "more than one posting without an amount")
    total = MixedAmount(p.weight for p in real if p.amount is not None)
    if missing:
        blank = missing[0]
        rest = (-total).amounts()
        if not rest:
            rest = [Amount(Decimal(0), "")]
        i = entry.postings.index(blank)
        new = []
        for a in rest:
            p = Posting(account=blank.account, amount=a, virtual=False, status=blank.status,
                        comment=blank.comment, line=blank.line,
                        own_tags=dict(blank.own_tags), inferred=True)
            new.append(p)
        if blank.assertion is not None:
            # Checked after the piece in its commodity (or after the last piece).
            match = [p for p in new if p.amount.commodity == blank.assertion.commodity]
            (match[0] if match else new[-1]).assertion = blank.assertion
        entry.postings[i:i + 1] = new
    elif not total.is_zero(journal.precisions):
        raise JournalError(entry.path, entry.line, "transaction does not balance")
    for p in entry.postings:
        p.transaction = entry
        p.tags = {**entry.tags, **p.own_tags}


def _assertions(journal):
    bal = {}
    for t in journal.transactions:
        for p in t.postings:
            key = (p.account, p.amount.commodity)
            bal[key] = bal.get(key, Decimal(0)) + p.amount.quantity
            if p.assertion is not None:
                c = p.assertion.commodity
                got = bal.get((p.account, c), Decimal(0))
                prec = journal.precision(c)
                if round_q(got - p.assertion.quantity, prec) != 0:
                    raise JournalError(
                        t.path, p.line,
                        f"balance assertion failed for {p.account}: expected "
                        f"{format_amount(p.assertion, prec)}, got {format_amount(Amount(got, c), prec)}")


def parse(text, path="-", loader=None):
    state = _State(loader)
    if path and path != "-":
        state.stack.append(os.path.normpath(path))
    _parse_text(state, text, path or "-")
    journal = Journal()
    journal.precisions = {**state.seen, **state.declared}
    for e in state.entries:
        _balance(e, journal)
        (journal.transactions if isinstance(e, Transaction) else journal.periodic).append(e)
    for p in (p for t in journal.transactions for p in t.postings):
        journal.precisions.setdefault(p.amount.commodity, DEFAULT_PRECISION)
    journal.transactions.sort(key=lambda t: (t.date, t.seq))
    prices = list(state.prices)
    for t in journal.transactions:
        for p in t.postings:
            if p.cost is not None and p.amount.quantity != 0:
                unit = abs(p.cost.quantity) / abs(p.amount.quantity)
                prices.append((t.seq, Price(t.date, p.amount.commodity, Amount(unit, p.cost.commodity))))
    prices.sort(key=lambda sp: sp[0])
    journal.prices = [pr for _, pr in prices]
    _assertions(journal)
    return journal


def load(path):
    return parse(_read(path), path=str(path))
