"""Reference pricing for the hidden tests: the legacy code plus the gold rule.

All money values are int cents.
Written in 2019. Many people changed it since then. Be careful.
"""


def calculate_price(order):
    customer = order.get("customer", {})
    items = order.get("items", [])
    coupon = order.get("coupon")
    subtotal = 0
    discount = 0
    total_qty = 0
    for item in items:
        line = item["price"] * item["qty"]
        total_qty = total_qty + item["qty"]
        subtotal = subtotal + line
        line_discount = 0
        if item["qty"] >= 10:
            if item.get("category") != "food":
                line_discount = line * 5 // 100
        if customer.get("type") == "business":
            if item.get("category") == "electronics":
                line_discount = line_discount + (line - line_discount) * 10 // 100
        else:
            if customer.get("type") == "staff":
                if item.get("category") != "books":
                    line_discount = line_discount + (line - line_discount) * 20 // 100
        discount = discount + line_discount
    after_lines = subtotal - discount
    if customer.get("type") != "staff":
        if customer.get("tier") == "silver":
            discount = discount + after_lines * 2 // 100
        elif customer.get("tier") == "gold":
            discount = discount + after_lines * 5 // 100
        if coupon:
            if coupon == "SAVE10":
                if subtotal - discount >= 5000:
                    discount = discount + (subtotal - discount) * 10 // 100
            elif coupon == "BULK5":
                if total_qty >= 20:
                    discount = discount + (subtotal - discount) * 5 // 100
    net = subtotal - discount
    country = customer.get("country", "US")
    shipping = 0
    free_ship = coupon == "FREESHIP" and customer.get("type") != "staff"
    if country == "US":
        if net < 5000:
            if not free_ship:
                shipping = 499
        if order.get("express"):
            shipping = shipping + 1500
    else:
        if country == "DE" or country == "UK":
            if net < 10000:
                if not free_ship:
                    shipping = 999
        else:
            if not free_ship:
                shipping = 1999
        if order.get("express"):
            shipping = shipping + 2500
    tax = 0
    if country == "DE":
        if customer.get("type") == "business" and customer.get("vat_id"):
            tax = 0
        else:
            tax = net * 19 // 100
    elif country == "UK":
        if customer.get("type") == "business" and customer.get("vat_id"):
            tax = 0
        else:
            tax = net * 20 // 100
    return {
        "subtotal": subtotal,
        "discount": discount,
        "shipping": shipping,
        "tax": tax,
        "total": net + shipping + tax,
    }
