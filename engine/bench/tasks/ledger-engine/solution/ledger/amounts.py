"""Amounts and mixed amounts (LEDGER.md section 1)."""

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

DEFAULT_PRECISION = 2
_NOT_SYMBOL = set(" \t.,-+;=@()[]\"")
_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_CODE = r"[A-Za-z]+"
_SYM = r"[^\sA-Za-z0-9.,\-+;=@()\[\]\"]"
_FORMS = [
    re.compile(rf"(?P<s1>-)?(?P<c>{_SYM}) ?(?P<s2>-)?(?P<n>{_NUM})"),
    re.compile(rf"(?P<s1>-)?(?P<c>{_CODE}) +(?P<s2>-)?(?P<n>{_NUM})"),
    re.compile(rf"(?P<s1>-)?(?P<n>{_NUM}) ?(?P<c>{_SYM})"),
    re.compile(rf"(?P<s1>-)?(?P<n>{_NUM}) +(?P<c>{_CODE})"),
    re.compile(rf"(?P<s1>-)?(?P<n>{_NUM})"),
]


@dataclass(frozen=True)
class Amount:
    quantity: Decimal
    commodity: str = ""

    def __neg__(self):
        return Amount(-self.quantity, self.commodity)


def parse_amount_ex(text):
    """(Amount, decimals written) for an amount as written, or ValueError."""
    s = str(text).strip()
    for form in _FORMS:
        m = form.fullmatch(s)
        if m:
            d = m.groupdict()
            signs = (d.get("s1") or "") + (d.get("s2") or "")
            if len(signs) > 1:
                raise ValueError(f"bad amount: {text!r}")
            n = m["n"]
            q = Decimal(n.replace(",", ""))
            decimals = len(n.split(".", 1)[1]) if "." in n else 0
            return Amount(-q if signs else q, d.get("c") or ""), decimals
    raise ValueError(f"bad amount: {text!r}")


def parse_amount(text):
    return parse_amount_ex(text)[0]


def is_symbol(commodity):
    return bool(commodity) and not commodity.isalpha()


def round_q(q, precision):
    return q.quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP)


def format_amount(amount, precision):
    q = round_q(amount.quantity, precision)
    sign = "-" if q < 0 else ""
    q = abs(q)
    text = f"{q:,.{precision}f}"
    c = amount.commodity
    if not c:
        return sign + text
    if is_symbol(c):
        return f"{sign}{c}{text}"
    return f"{sign}{text} {c}"


def _prec(precisions, c):
    return (precisions or {}).get(c, DEFAULT_PRECISION)


class MixedAmount:
    def __init__(self, amounts=()):
        self._q = {}
        for a in amounts:
            self._add(a)

    def _add(self, a):
        q = self._q.get(a.commodity, Decimal(0)) + a.quantity
        if q == 0:
            self._q.pop(a.commodity, None)
        else:
            self._q[a.commodity] = q

    def copy(self):
        m = MixedAmount()
        m._q = dict(self._q)
        return m

    def __add__(self, other):
        m = self.copy()
        for a in ([other] if isinstance(other, Amount) else other.amounts()):
            m._add(a)
        return m

    __radd__ = __add__

    def __neg__(self):
        return MixedAmount(-a for a in self.amounts())

    def __sub__(self, other):
        return self + (-other)

    def amounts(self):
        return [Amount(self._q[c], c) for c in sorted(self._q)]

    def get(self, commodity):
        return self._q.get(commodity, Decimal(0))

    def commodities(self):
        return sorted(self._q)

    def __eq__(self, other):
        if isinstance(other, Amount):
            other = MixedAmount([other])
        if not isinstance(other, MixedAmount):
            return NotImplemented
        return self._q == other._q

    def __hash__(self):
        return hash(tuple(sorted(self._q.items())))

    def is_zero(self, precisions=None):
        return all(round_q(q, _prec(precisions, c)) == 0 for c, q in self._q.items())

    def lines(self, precisions=None):
        out = [format_amount(a, _prec(precisions, a.commodity)) for a in self.amounts()
               if round_q(a.quantity, _prec(precisions, a.commodity)) != 0]
        return out or ["0"]

    def __repr__(self):
        return "MixedAmount(" + ", ".join(f"{a.quantity} {a.commodity!r}" for a in self.amounts()) + ")"
