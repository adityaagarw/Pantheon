"""Money helpers. Money is always Decimal."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def cents(value):
    """Round to cents, half up (the only rounding the close policy allows)."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt(value):
    """Money as the close report writes it: exactly two decimals, e.g. "1234.50"."""
    return f"{cents(value):.2f}"
