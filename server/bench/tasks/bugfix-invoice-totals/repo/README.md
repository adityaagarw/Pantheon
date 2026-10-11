# invoicing

Calculates invoice amounts for the billing service.

## Usage

```python
from invoicing.calc import invoice_summary, invoice_total

items = [
    {"sku": "BOOK-1", "unit_price": "12.50", "quantity": 2, "tax_class": "reduced"},
    {"sku": "PEN-3", "unit_price": "1.99", "quantity": 4},
]
invoice_summary(items, discount={"type": "percent", "value": 10})
```

`invoice_summary` returns a dict with these keys: `subtotal`, `discount`, `tax`,
`total`, `average_unit_price`. Each value is a `decimal.Decimal` with 2 decimal places.
`invoice_total` returns only the `total`.

## Input

- `unit_price`: a string, int, float or Decimal. Convert floats with `Decimal(str(value))`.
- `quantity`: an int.
- `tax_class`: `"standard"` (20%), `"reduced"` (5%) or `"zero"` (0%).
  This key is optional. The default is `"standard"`. An unknown tax class raises `ValueError`.
- `discount`: `None`, `{"type": "percent", "value": N}` or `{"type": "amount", "value": N}`.

## Billing rules

1. Line net = unit price x quantity.
2. Subtotal = sum of all line nets.
3. The discount is calculated on the subtotal, **before tax**.
   - Percent discount: subtotal x value / 100.
   - Amount discount: the value.
   - The discount is never more than the subtotal.
4. The discount is shared across the lines in proportion to their line net.
5. Tax = sum, for each line, of (line net - share of discount) x tax rate of that line.
6. Total = subtotal - discount + tax.
7. Calculate with full precision. Round each returned value only at the end,
   to 0.01, **half up** (0.005 becomes 0.01).
8. Average unit price = subtotal / total quantity. It is 0.00 for an invoice with no lines.
9. An invoice with no lines returns 0.00 for every value.
