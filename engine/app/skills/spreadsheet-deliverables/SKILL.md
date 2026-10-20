---
name: spreadsheet-deliverables
description: Use when the work must deliver a workbook (.xlsx) or a CSV that someone else will open, total or load into a program.
---

A spreadsheet deliverable is read by people and by programs (pandas, a grader, a preview).
Both must see the same numbers.

1. Take the names from the request exactly: the file name, each sheet's name and each
   column's name, in the order given. Put the file where the request says (else the
   workspace root). One request item, one sheet or one file; don't merge or split them.
2. Write values, not only formulas. A workbook a script writes (openpyxl, pandas) keeps
   its formulas but no saved values, so every program that doesn't recalculate reads
   those cells as blank. Compute each number in your script and write the number. Where a
   formula helps the reader, put it in a column beside the value or in a note.
3. One fact per cell: numbers as numbers (not "1,234.50 USD" text), dates as dates or ISO
   text (2024-03-31), one currency per column with the currency in the header. Keep the
   source's identifiers (invoice numbers, check numbers, account codes) as text so leading
   zeros stay.
4. Totals and subtotals: label each row ("Subtotal: Line 8", "Grand total") and make each
   one equal the sum of the rows it covers. Check every total against the source figure it
   reconciles to and show the difference, even when it is zero.
5. CSV: one header row, a comma delimiter, UTF-8, and every value with a comma, quote or
   line break in quotes (use the csv module, never string joins). Every row has as many
   fields as the header.
6. Before you hand in, run check_files on each file: it opens them as their reader will
   and reports formula cells without values, error cells (#REF!, #DIV/0!), empty sheets,
   rows with the wrong number of fields and placeholder text. Fix everything it reports.
7. In your result, list each file with its sheets (or columns) and the key totals, so a
   reader can find them without opening the file.
