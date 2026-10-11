# Request: clean up pricing and add the gold tier

From: Head of e-commerce

`shop/pricing.py` is hard to change. Each new rule takes days and breaks something.
Marketing now wants a new **gold** loyalty tier.

## 1. Refactor

- Make each pricing rule easy to find and change.
- The public function `calculate_price(order)` must stay. It must return the same dict keys.
- **Do not change any result for any order that has no gold tier.** Finance checks
  old orders against new results. Keep the strange old behavior too (for example,
  the order of the discounts and the rounding down with `//`).
- Code quality limits, for every function in the `shop` package:
  - no function is longer than 40 lines,
  - the nesting depth of `if` / `for` / `while` / `with` / `try` blocks is at most 3
    (an `elif` does not add a level).

## 2. New rule: gold tier

- A customer can have `"tier": "gold"`. A customer has only one tier.
- Gold gives 5% off. It works the same way as the silver tier (2%):
  it uses the amount after the line discounts, it rounds down, it is applied
  at the same point as silver, and it does not apply to staff.
