---
name: source-reconciliation
description: Use when the work must match, reconcile or audit records from several sources (ledgers, bank statements, invoices, contracts, logs) and explain every difference.
---

Most errors in this work come from a source nobody opened, or a difference nobody
explained.

1. Inventory every input before you analyse anything: each file, its format, its period,
   its row count and what it is the authority for (the bank statement for cash that
   cleared, the ledger for what was booked, the contract for the terms). Open every file
   and every sheet, not only the first one that looks right. Note inputs the request names
   that you don't have, and get them or say exactly what is missing.
2. Write down the join keys between the sources (reference numbers, dates, amounts,
   counterparty names and their aliases) and how you treat each mismatch: an exact match,
   a match within a tolerance you state, a partial or split payment, a fee netted out, a
   timing difference across the period end, a duplicate.
3. Match in passes, strictest first (same key and amount, then same amount and date
   window, then sums of several items), and record which pass matched each item. Never
   force a match: an item that doesn't fit stays an exception.
4. Every difference gets a line: what, where in each source (file, row, reference), the
   amount, the likely cause, and the action (an adjusting entry, a correction, a question
   for the owner). The exceptions plus the matched items must account for every record of
   every source: count them and show that the counts and totals tie.
5. When two sources disagree, say which one you follow and why (the more authoritative
   one for that fact), and keep the other's value visible.
6. Recompute every reported total from the detail, and reconcile each to its source total
   (for example, bank ending balance to the book balance after adjustments). Show the
   difference, even when it is zero.
7. Keep numbers as the sources give them until the last step; round only for display.
