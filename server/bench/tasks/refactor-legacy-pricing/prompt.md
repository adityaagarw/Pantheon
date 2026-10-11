Refactor the old pricing code and add one new rule.

Read `REQUEST.md`. It has the goals and the limits.
The code is in `shop/pricing.py`. The README explains the order format.

Do this:

1. Refactor the `shop` package so that it meets all limits in the request.
2. Keep every result exactly the same for orders without the gold tier.
3. Add the gold tier rule.
4. Add tests in `tests/`. Include tests that show the old behavior did not change.
5. Update `README.md` for the gold tier.
