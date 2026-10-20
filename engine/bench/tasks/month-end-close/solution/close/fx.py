"""Exchange rates (CLOSE_POLICY.md, section 4)."""

import bisect
import csv
from datetime import date
from decimal import Decimal

from close.money import cents


class Rates:
    def __init__(self, rates):
        self._days = sorted(rates)
        self._rates = dict(rates)

    @classmethod
    def load(cls, path):
        rates = {}
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rates[date.fromisoformat(row["date"].strip())] = Decimal(row["eur_usd"].strip())
        return cls(rates)

    def rate(self, day):
        i = bisect.bisect_right(self._days, day)
        if i == 0:
            raise ValueError(f"no EUR/USD rate on or before {day}")
        return self._rates[self._days[i - 1]]

    def to_usd(self, amount, currency, day):
        if currency == "USD":
            return Decimal(amount)
        if currency == "EUR":
            return cents(Decimal(amount) * self.rate(day))
        raise ValueError(f"unknown currency {currency}")
