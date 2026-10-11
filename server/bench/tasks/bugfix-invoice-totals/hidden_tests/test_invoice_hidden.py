import unittest
from decimal import Decimal

from invoicing import calc

D = Decimal


def item(price, qty=1, tax_class=None):
    data = {"sku": "X", "unit_price": price, "quantity": qty}
    if tax_class:
        data["tax_class"] = tax_class
    return data


class HiddenInvoiceTest(unittest.TestCase):
    def test_half_up_rounding(self):
        summary = calc.invoice_summary([item("0.125", 1, "zero")])
        self.assertEqual(summary["subtotal"], D("0.13"))
        self.assertEqual(summary["total"], D("0.13"))

    def test_mixed_tax_classes(self):
        summary = calc.invoice_summary([item("10.00", 2, "reduced"), item("5.00", 1, "standard")])
        self.assertEqual(summary["tax"], D("2.00"))
        self.assertEqual(summary["total"], D("27.00"))

    def test_zero_tax_class(self):
        self.assertEqual(calc.invoice_summary([item("9.99", 1, "zero")])["total"], D("9.99"))

    def test_default_tax_class_is_standard(self):
        self.assertEqual(calc.invoice_summary([item("10.00")])["tax"], D("2.00"))

    def test_percent_discount_before_tax(self):
        summary = calc.invoice_summary([item("100.00")], {"type": "percent", "value": 10})
        self.assertEqual(summary["discount"], D("10.00"))
        self.assertEqual(summary["tax"], D("18.00"))
        self.assertEqual(summary["total"], D("108.00"))

    def test_amount_discount_before_tax(self):
        summary = calc.invoice_summary([item("100.00")], {"type": "amount", "value": "10.00"})
        self.assertEqual(summary["discount"], D("10.00"))
        self.assertEqual(summary["tax"], D("18.00"))
        self.assertEqual(summary["total"], D("108.00"))

    def test_discount_shared_across_tax_classes(self):
        items = [item("50.00", 1, "reduced"), item("50.00", 1, "standard")]
        summary = calc.invoice_summary(items, {"type": "amount", "value": 20})
        self.assertEqual(summary["tax"], D("10.00"))
        self.assertEqual(summary["total"], D("90.00"))

    def test_discount_capped_at_subtotal(self):
        summary = calc.invoice_summary([item("10.00")], {"type": "amount", "value": "50.00"})
        self.assertEqual(summary["discount"], D("10.00"))
        self.assertEqual(summary["tax"], D("0.00"))
        self.assertEqual(summary["total"], D("0.00"))

    def test_empty_invoice(self):
        summary = calc.invoice_summary([])
        for key in ("subtotal", "discount", "tax", "total", "average_unit_price"):
            self.assertEqual(summary[key], D("0.00"), key)

    def test_empty_invoice_with_discount(self):
        self.assertEqual(calc.invoice_summary([], {"type": "amount", "value": 5})["total"], D("0.00"))

    def test_average_unit_price(self):
        summary = calc.invoice_summary([item("10.00", 2), item("5.00", 1)])
        self.assertEqual(summary["average_unit_price"], D("8.33"))

    def test_unknown_tax_class_raises_value_error(self):
        with self.assertRaises(ValueError):
            calc.invoice_summary([item("1.00", 1, "luxury")])

    def test_float_prices(self):
        self.assertEqual(calc.invoice_summary([item(0.1, 3)])["total"], D("0.36"))

    def test_total_matches_summary(self):
        items = [item("19.99", 3, "reduced"), item("4.50", 2)]
        discount = {"type": "percent", "value": 15}
        self.assertEqual(calc.invoice_total(items, discount), calc.invoice_summary(items, discount)["total"])

    def test_values_are_decimal(self):
        for value in calc.invoice_summary([item("1.00")]).values():
            self.assertIsInstance(value, Decimal)
