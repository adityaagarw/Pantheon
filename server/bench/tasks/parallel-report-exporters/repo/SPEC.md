# Spec: report exporters

Customers want to download reports in more formats. Today there is only `text`.
Add four exporters: `csv`, `markdown`, `html` and `json`.

The four exporters do not depend on each other. Each one is a separate module in
`reports/exporters/`. Use these file names:

| Format | File |
| --- | --- |
| csv | `reports/exporters/csv_export.py` |
| markdown | `reports/exporters/markdown_export.py` |
| html | `reports/exporters/html_export.py` |
| json | `reports/exporters/json_export.py` |

## Common rules

- Each exporter is a function `(report) -> str`, registered with `@register("<format>")`.
- `exporters.export(report, "<format>")` returns the full output as a `str`.
- Value text (for csv, markdown and html): use `model.format_value`. It gives
  `""` for `None`, 2 decimals for money (`"1234.50"`), `YYYY-MM-DD` for dates,
  and `str(value)` for the rest.
- "Numeric columns" are columns with type `int` or `money`.

## csv

- RFC 4180. The first row has the column names. The title is not in the output.
- Separator `,`. Line end `\r\n`, also after the last row.
- Quote a field only when it contains `,`, `"`, `\r` or `\n`. Write a `"` in a field as `""`.

## markdown

```
# Sales by product

| Product | Units | Revenue |
| --- | ---: | ---: |
| Widget | 3 | 59.97 |
```

- Line 1: `# ` + title. Line 2: empty.
- Header row, then the separator row: `---:` for numeric columns, `---` for others.
- Each row: `| ` + cells joined with ` | ` + ` |`.
- In cells (also header cells): write `|` as `\|`. Write a line break (`\r\n` or `\n`) as `<br>`.
- If the report has no rows: after the separator row add an empty line and `_No rows._`.
- The output ends with one `\n`.

## html

```
<table>
<caption>Sales by product</caption>
<thead>
<tr><th>Product</th><th class="num">Units</th><th class="num">Revenue</th></tr>
</thead>
<tbody>
<tr><td>Widget</td><td class="num">3</td><td class="num">59.97</td></tr>
</tbody>
</table>
```

- Exactly this structure, one element per line as shown. The output ends with one `\n`.
- Escape the title, the column names and the values with `html.escape(text, quote=True)`.
  After escaping, write each line break (`\r\n` or `\n`) as `<br>`.
- Numeric columns have `class="num"` on both `th` and `td`.
- If the report has no rows, the `tbody` is `<tbody>` and `</tbody>` on two lines with nothing between.

## json

```json
{
  "title": "Sales by product",
  "generated_at": "2026-10-01T08:30:00",
  "columns": [{"name": "Product", "type": "text"}],
  "rows": [{"Product": "Widget"}]
}
```

- Use `json.dumps(data, indent=2, ensure_ascii=False)` + `"\n"`.
- `generated_at`: `report.generated_at.isoformat()`.
- `rows`: one object for each row. The keys are the column names, in column order.
- Values: `int` columns give a JSON number, `money` columns give a string with 2 decimals
  (`"59.97"`), `date` columns give `"YYYY-MM-DD"`, `text` columns give a string.
  `None` gives `null`.
- The output of the json exporter must load again with `Report.from_dict` and give
  the same report.

## Registry and command line

- `exporters.available()` returns `["csv", "html", "json", "markdown", "text"]`.
- `md` is an alias for `markdown`. `export(report, "md")` works, but `available()`
  does not list `md`.
- An unknown format raises `ValueError("unknown format: <name>")`.
- New command: `python -m reports formats` prints the names from `available()`, one per line.
- `python -m reports export --format <name> FILE [-o OUT]` works for all formats.
  With `-o`, write the output exactly (no change of line ends).
  An unknown format prints `error: unknown format: <name>` to stderr and exits with code 2.
