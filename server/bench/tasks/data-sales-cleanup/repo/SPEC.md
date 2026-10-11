# Spec: quarterly sales summary

The sales team exports orders from three old systems into one CSV file.
The data is dirty. Build a pipeline that cleans the data and writes a summary.

## Interface

```python
from sales.pipeline import build_summary
summary = build_summary("data/sales_2026_q3.csv")   # returns a dict
```

```
python -m sales.pipeline data/sales_2026_q3.csv out/summary.json
```

The command writes the summary as JSON (UTF-8, `indent=2`) and creates the
output folder if it does not exist.

## Input

A UTF-8 CSV file with a header row:

`order_id, order_date, customer, region, product, quantity, unit_price, status`

The `customer` column is not used.

## Rules (apply them in this order)

1. **Trim.** Remove spaces at the start and end of every field.
2. **Duplicates.** Some orders appear more than once. A later row is a correction
   of an earlier row. For each non-empty `order_id`, keep only the **last** row in
   the file. Count the removed rows in `duplicates_removed`. Rows with an empty
   `order_id` are not duplicates (they are invalid, see rule 3).
3. **Validate** each kept row. Check the fields in this order. The first field
   that fails is the reason for the row. Invalid rows are not used for anything else.
   - `order_id`: must not be empty.
   - `order_date`: must be a real date in one of these formats:
     `YYYY-MM-DD`, `YYYY/MM/DD`, `DD/MM/YYYY`, or `Mon DD, YYYY` (an English
     three-letter month, for example `Jul 03, 2026`). The day and the month can
     have one or two digits. All other formats are invalid.
   - `region`: compare without case, without dots, and with runs of spaces
     changed to one space. Map it like this:
     - `north america`, `na`, `n america` -> `North America`
     - `europe`, `eu`, `emea` -> `Europe`
     - `asia pacific`, `apac`, `asia` -> `Asia Pacific`
     - `latin america`, `latam` -> `Latin America`
     - anything else is invalid.
   - `product`: must not be empty. Use the name as it is (after trim).
   - `quantity`: digits only (no sign, no decimal point), value 1 or more.
   - `unit_price`: remove all `$`, `,` and space characters. The rest must be
     digits with an optional `.` and one or two decimal digits (for example `12`,
     `12.5`, `1200.50`). The value must be more than 0.
   - `status`: without case, one of `completed`, `refunded`, `cancelled`, `pending`.
4. **Revenue.** Only valid rows with status `completed` have revenue.
   Revenue of a row = quantity x unit price.

## Output

```json
{
  "rows_read": 0,
  "duplicates_removed": 0,
  "valid_rows": 0,
  "invalid_rows": 0,
  "invalid_reasons": {"order_date": 0},
  "status_counts": {"completed": 0},
  "total_revenue": "0.00",
  "revenue_by_region": {"Europe": "0.00"},
  "revenue_by_month": {"2026-07": "0.00"},
  "revenue_by_region_month": {"Europe": {"2026-07": "0.00"}},
  "top_products": [{"product": "Widget", "revenue": "0.00", "units": 0}]
}
```

- `rows_read`: number of data rows in the file (not the header). Fully empty lines are not rows.
- `invalid_reasons`: number of invalid rows for each field name. Only fields with a count of 1 or more.
- `status_counts`: number of **valid** rows for each status (lower case). Only statuses with a count of 1 or more.
- Money values are strings with exactly 2 decimals, for example `"1200.50"`.
- `revenue_by_region`, `revenue_by_month` and `revenue_by_region_month` contain only
  regions and months (`YYYY-MM`) that have revenue.
- `top_products`: the 3 products with the most revenue, highest first. If two
  products have the same revenue, sort them by name (A to Z). `units` is the sum of
  the quantity of the completed rows of that product.
- The pipeline must work on other files with the same format, not only on the given file.
