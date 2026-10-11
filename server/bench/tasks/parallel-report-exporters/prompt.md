Add four export formats to the `reports` package: `csv`, `markdown`, `html` and `json`.

`SPEC.md` has the exact output of each format and the file name of each exporter.
The `text` exporter in `reports/exporters/text.py` is an example.
`examples/sales.json` is an example report file.

Do this:

1. Write the four exporters. They do not depend on each other, so you can build them at the same time.
2. Connect them to the registry in `reports/exporters/__init__.py`.
3. Add the `md` alias, the `formats` command and the error handling from the spec.
4. Add tests for each format in `tests/`.
5. Update `README.md` with the new formats.
