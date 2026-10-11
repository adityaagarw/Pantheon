# Ticket FIN-4821: Wrong invoice totals

Reported by: Finance team
Priority: High

We found these problems this month:

1. **INV-2207.** The customer bought books. Books have the "reduced" tax class (5%).
   The invoice charged 20% tax on the books.
2. **INV-2213.** We gave a 10.00 coupon. The invoice took the coupon off after tax.
   Our billing rules say that discounts come before tax. The tax amount on the invoice is wrong.
3. **INV-2219.** The invoice total is 0.12. Our accounting system says 0.13.
   Accounting always rounds half up.
4. Draft invoices with no lines crash the invoice preview page (error 500).
5. A 50.00 coupon on a 10.00 order made a negative total of -40.00.

Please fix all of these. We do not want to see these bugs again.
