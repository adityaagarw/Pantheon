import unittest
from decimal import Decimal

from invoicing.calc import invoice_summary


class TicketFin4821Test(unittest.TestCase):
    def test_reduced_tax_class_per_line(self):
        items = [{"sku": "BOOK", "unit_price": "10.00", "quantity": 1, "tax_class": "reduced"}]
        self.assertEqual(invoice_summary(items)["tax"], Decimal("0.50"))

    def test_amount_discount_before_tax(self):
        items = [{"sku": "A", "unit_price": "100.00", "quantity": 1}]
        summary = invoice_summary(items, {"type": "amount", "value": "10.00"})
        self.assertEqual(summary["tax"], Decimal("18.00"))
        self.assertEqual(summary["total"], Decimal("108.00"))

    def test_half_up_rounding(self):
        items = [{"sku": "A", "unit_price": "0.125", "quantity": 1, "tax_class": "zero"}]
        self.assertEqual(invoice_summary(items)["total"], Decimal("0.13"))

    def test_empty_invoice(self):
        self.assertEqual(invoice_summary([])["total"], Decimal("0.00"))

    def test_discount_is_capped(self):
        items = [{"sku": "A", "unit_price": "10.00", "quantity": 1}]
        self.assertEqual(invoice_summary(items, {"type": "amount", "value": 50})["total"], Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()
