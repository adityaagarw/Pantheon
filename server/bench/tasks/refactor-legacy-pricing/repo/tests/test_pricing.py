import unittest

from shop.pricing import calculate_price


class PricingTest(unittest.TestCase):
    def test_simple_us_order(self):
        order = {"customer": {"type": "regular"}, "items": [{"price": 1000, "qty": 2}]}
        self.assertEqual(calculate_price(order), {
            "subtotal": 2000, "discount": 0, "shipping": 499, "tax": 0, "total": 2499,
        })

    def test_german_order_has_tax(self):
        order = {"customer": {"type": "regular", "country": "DE"}, "items": [{"price": 20000, "qty": 1}]}
        result = calculate_price(order)
        self.assertEqual(result["tax"], 3800)
        self.assertEqual(result["shipping"], 0)

    def test_empty_order(self):
        self.assertEqual(calculate_price({})["total"], 499)


if __name__ == "__main__":
    unittest.main()
