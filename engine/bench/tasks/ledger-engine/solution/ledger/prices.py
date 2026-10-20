"""Market prices and value (LEDGER.md section 3)."""

from decimal import Decimal, localcontext

from .amounts import Amount, MixedAmount
from .errors import PriceError


class PriceDB:
    def __init__(self, journal):
        self.prices = list(journal.prices)

    def rate(self, frm, to, on):
        if frm == to:
            return Decimal(1)
        best = None  # (date, direct?, index, rate)
        for i, p in enumerate(self.prices):
            if p.date > on:
                continue
            if p.commodity == frm and p.price.commodity == to:
                cand = (p.date, 1, i, p.price.quantity)
            elif p.commodity == to and p.price.commodity == frm and p.price.quantity != 0:
                with localcontext() as ctx:
                    ctx.prec = 28
                    cand = (p.date, 0, i, Decimal(1) / p.price.quantity)
            else:
                continue
            if best is None or cand[:3] > best[:3]:
                best = cand
        if best is None:
            raise PriceError(f"no price for {frm} in {to} on {on.isoformat()}")
        return best[3]

    def value(self, mixed, to, on):
        out = MixedAmount()
        for a in mixed.amounts():
            try:
                r = self.rate(a.commodity, to, on)
            except PriceError:
                out = out + a
                continue
            out = out + Amount(a.quantity * r, to)
        return out
