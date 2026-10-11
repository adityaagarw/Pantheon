import unittest
from decimal import Decimal

from invoicing.calc import invoice_summary, invoice_total


class InvoiceTest(unittest.TestCase):
    def test_single_standard_line(self):
        items = [{"sku": "A", "unit_price": "10.00", "quantity": 1}]
        summary = invoice_summary(items)
        self.assertEqual(summary["subtotal"], Decimal("10.00"))
        self.assertEqual(summary["tax"], Decimal("2.00"))
        self.assertEqual(summary["total"], Decimal("12.00"))

    def test_several_standard_lines(self):
        items = [
            {"sku": "A", "unit_price": "10.00", "quantity": 2},
            {"sku": "B", "unit_price": 5, "quantity": 3},
        ]
        self.assertEqual(invoice_total(items), Decimal("42.00"))

    def test_values_are_decimal(self):
        summary = invoice_summary([{"sku": "A", "unit_price": "1.00", "quantity": 1}])
        for value in summary.values():
            self.assertIsInstance(value, Decimal)


if __name__ == "__main__":
    unittest.main()
