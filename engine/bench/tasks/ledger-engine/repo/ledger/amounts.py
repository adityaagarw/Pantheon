"""Amounts and mixed amounts (LEDGER.md section 1). To build."""


class Amount:
    """A frozen dataclass: quantity (Decimal), commodity (str)."""


class MixedAmount:
    """A sum of amounts in any number of commodities."""


def parse_amount(text):
    raise NotImplementedError


def format_amount(amount, precision):
    raise NotImplementedError
