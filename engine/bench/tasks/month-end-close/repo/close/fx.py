"""Exchange rates (CLOSE_POLICY.md, section 4)."""


class Rates:
    @classmethod
    def load(cls, path):
        raise NotImplementedError

    def rate(self, day):
        """Dollars per euro on a day (Decimal)."""
        raise NotImplementedError

    def to_usd(self, amount, currency, day):
        raise NotImplementedError
