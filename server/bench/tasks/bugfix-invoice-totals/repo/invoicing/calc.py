"""Invoice calculations. See README.md for the billing rules."""

from decimal import ROUND_HALF_EVEN, Decimal

TAX_RATES = {
    "standard": Decimal("0.20"),
    "reduced": Decimal("0.05"),
    "zero": Decimal("0"),
}
CENT = Decimal("0.01")


def money(value):
    """Round a value to cents."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_EVEN)


def to_decimal(value):
    return Decimal(str(value))


def line_net(item):
    return to_decimal(item["unit_price"]) * item["quantity"]


def discount_value(base, discount):
    if not discount:
        return Decimal("0")
    value = to_decimal(discount["value"])
    if discount["type"] == "percent":
        return base * value / 100
    return value


def invoice_summary(items, discount=None, tax_class="standard"):
    subtotal = sum((line_net(item) for item in items), Decimal("0"))
    tax = subtotal * TAX_RATES[tax_class]
    gross = subtotal + tax
    discount_amount = discount_value(gross, discount)
    total = gross - discount_amount
    quantity = sum(item["quantity"] for item in items)
    return {
        "subtotal": money(subtotal),
        "discount": money(discount_amount),
        "tax": money(tax),
        "total": money(total),
        "average_unit_price": money(subtotal / quantity),
    }


def invoice_total(items, discount=None, tax_class="standard"):
    return invoice_summary(items, discount, tax_class)["total"]
