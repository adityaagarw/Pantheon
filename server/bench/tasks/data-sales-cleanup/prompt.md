The sales team needs a clean summary of the Q3 orders.
The data is in `data/sales_2026_q3.csv`. It is dirty.
`SPEC.md` has the exact cleaning rules and the output format.

Do this:

1. Write the pipeline in `sales/pipeline.py`: the function `build_summary(csv_path)`
   and the command `python -m sales.pipeline INPUT.csv OUTPUT.json`.
2. Follow every rule in `SPEC.md` exactly. The hidden tests compare each number.
3. Run the pipeline on the Q3 file and write the result to `out/summary.json`.
4. Add tests in `tests/` with small CSV examples for the rules.

The hidden tests also run the pipeline on a different file with the same format.
Do not write numbers from the Q3 file into the code.
