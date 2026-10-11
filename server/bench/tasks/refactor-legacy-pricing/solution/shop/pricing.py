"""Order pricing.

All money values are int cents. All percentages round down (//).
The order of the steps matters: line discounts, tier, coupon, shipping, tax.
"""

TIER_PERCENT = {"silver": 2, "gold": 5}
EU_COUNTRIES = ("DE", "UK")
TAX_PERCENT = {"DE": 19, "UK": 20}


def percent(amount, value):
    return amount * value // 100


def line_discount(item, customer_type):
    line = item["price"] * item["qty"]
    category = item.get("category")
    discount = 0
    if item["qty"] >= 10 and category != "food":
        discount = percent(line, 5)
    if customer_type == "business" and category == "electronics":
        discount += percent(line - discount, 10)
    elif customer_type == "staff" and category != "books":
        discount += percent(line - discount, 20)
    return discount


def tier_discount(customer, amount):
    return percent(amount, TIER_PERCENT.get(customer.get("tier"), 0))


def coupon_discount(coupon, net, total_qty):
    if coupon == "SAVE10" and net >= 5000:
        return percent(net, 10)
    if coupon == "BULK5" and total_qty >= 20:
        return percent(net, 5)
    return 0


def shipping_cost(country, net, free_ship, express):
    if country == "US":
        base, free_from, express_fee = 499, 5000, 1500
    elif country in EU_COUNTRIES:
        base, free_from, express_fee = 999, 10000, 2500
    else:
        base, free_from, express_fee = 1999, None, 2500
    shipping = 0
    if not free_ship and (free_from is None or net < free_from):
        shipping = base
    if express:
        shipping += express_fee
    return shipping


def tax_amount(customer, country, net):
    if country not in TAX_PERCENT:
        return 0
    if customer.get("type") == "business" and customer.get("vat_id"):
        return 0
    return percent(net, TAX_PERCENT[country])


def calculate_price(order):
    customer = order.get("customer", {})
    items = order.get("items", [])
    coupon = order.get("coupon")
    is_staff = customer.get("type") == "staff"

    subtotal = sum(item["price"] * item["qty"] for item in items)
    total_qty = sum(item["qty"] for item in items)
    discount = sum(line_discount(item, customer.get("type")) for item in items)
    if not is_staff:
        discount += tier_discount(customer, subtotal - discount)
        discount += coupon_discount(coupon, subtotal - discount, total_qty)

    net = subtotal - discount
    country = customer.get("country", "US")
    free_ship = coupon == "FREESHIP" and not is_staff
    shipping = shipping_cost(country, net, free_ship, order.get("express"))
    tax = tax_amount(customer, country, net)
    return {
        "subtotal": subtotal,
        "discount": discount,
        "shipping": shipping,
        "tax": tax,
        "total": net + shipping + tax,
    }
