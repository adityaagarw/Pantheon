import unittest

from shop.pricing import calculate_price


def order(customer, *items, **extra):
    return {"customer": customer, "items": list(items), **extra}


class RuleTest(unittest.TestCase):
    def test_gold_tier(self):
        result = calculate_price(order({"type": "regular", "tier": "gold"}, {"price": 10000, "qty": 1}))
        self.assertEqual(result["discount"], 500)

    def test_silver_tier_unchanged(self):
        result = calculate_price(order({"type": "regular", "tier": "silver"}, {"price": 10000, "qty": 1}))
        self.assertEqual(result["discount"], 200)

    def test_save10_uses_amount_after_tier(self):
        result = calculate_price(order({"type": "regular", "tier": "silver"}, {"price": 5050, "qty": 1}, coupon="SAVE10"))
        self.assertEqual(result["discount"], 101)

    def test_staff_gets_no_coupon(self):
        result = calculate_price(order({"type": "staff"}, {"price": 1000, "qty": 1, "category": "books"}, coupon="FREESHIP"))
        self.assertEqual(result["shipping"], 499)

    def test_reverse_charge(self):
        result = calculate_price(order({"type": "business", "country": "UK", "vat_id": "GB1"}, {"price": 1000, "qty": 1}))
        self.assertEqual(result["tax"], 0)


if __name__ == "__main__":
    unittest.main()
