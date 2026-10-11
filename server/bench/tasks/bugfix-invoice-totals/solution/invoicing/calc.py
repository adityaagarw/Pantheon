"""Invoice calculations. See README.md for the billing rules."""

from decimal import ROUND_HALF_UP, Decimal

TAX_RATES = {
    "standard": Decimal("0.20"),
    "reduced": Decimal("0.05"),
    "zero": Decimal("0"),
}
CENT = Decimal("0.01")
ZERO = Decimal("0")


def money(value):
    """Round a value to cents, half up."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def to_decimal(value):
    return Decimal(str(value))


def line_net(item):
    return to_decimal(item["unit_price"]) * item["quantity"]


def tax_rate(item):
    tax_class = item.get("tax_class", "standard")
    if tax_class not in TAX_RATES:
        raise ValueError(f"unknown tax class: {tax_class}")
    return TAX_RATES[tax_class]


def discount_value(subtotal, discount):
    if not discount:
        return ZERO
    value = to_decimal(discount["value"])
    if discount["type"] == "percent":
        value = subtotal * value / 100
    return min(value, subtotal)


def invoice_summary(items, discount=None):
    nets = [(line_net(item), tax_rate(item)) for item in items]
    subtotal = sum((net for net, _ in nets), ZERO)
    discount_amount = discount_value(subtotal, discount)
    if subtotal:
        remaining = (subtotal - discount_amount) / subtotal
        tax = sum((net * rate for net, rate in nets), ZERO) * remaining
    else:
        tax = ZERO
    quantity = sum(item["quantity"] for item in items)
    return {
        "subtotal": money(subtotal),
        "discount": money(discount_amount),
        "tax": money(tax),
        "total": money(subtotal - discount_amount + tax),
        "average_unit_price": money(subtotal / quantity) if quantity else money(ZERO),
    }


def invoice_total(items, discount=None):
    return invoice_summary(items, discount)["total"]
