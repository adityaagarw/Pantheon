"""The server-side spreadsheet formula evaluator."""

from __future__ import annotations

import pytest

from app.services.formulas import cell_name, col_letters, parse_range, recalculate


def calc(cells: dict[str, object], other: dict[str, object] | None = None) -> dict[str, object]:
    sheet = {ref: ({"f": v} if isinstance(v, str) and v.startswith("=") else {"v": v})
             for ref, v in cells.items()}
    book = {"Sheet1": sheet}
    if other is not None:
        book["Data"] = {ref: {"v": v} for ref, v in other.items()}
    recalculate(book)
    return {ref: c.get("v") for ref, c in sheet.items()}


def test_references_and_names():
    assert col_letters(0) == "A" and col_letters(25) == "Z" and col_letters(27) == "AB"
    assert cell_name(0, 27) == "AB1"
    assert parse_range("C3:A1") == (0, 0, 2, 2)


@pytest.mark.parametrize(("formula", "expected"), [
    ("=1+2*3", 7), ("=(1+2)*3", 9), ("=2^3^1", 8), ("=-A1+10", 8), ("=50%", 0.5),
    ("=A1*B1", 6), ("=SUM(A1:B2)", 15), ("=AVERAGE(A1:B2)", 3.75), ("=MAX(A1:B2,20)", 20),
    ("=COUNT(A1:C2)", 4), ("=COUNTA(A1:C2)", 5), ("=ROUND(10/3,2)", 3.33),
    ('=IF(A1>1,"big","small")', "big"), ('=A1&"-"&C1', "2-hello"), ('=UPPER(C1)', "HELLO"),
    ("=A1=2", True), ('=AND(A1>0,B1>0)', True), ("=IFERROR(1/0,99)", 99), ("=1/0", "#DIV/0!"),
    ("=NOSUCH(1)", "#NAME?"), ('=SUMIF(A1:B2,">3")', 10), ('=COUNTIF(A1:B2,">=3")', 3),
    ("=Data!A1*2", 84), ("=SUM(Data!A1:A2)", 50), ("=VLOOKUP(4,A1:B2,2,FALSE)", 6),
    ('=IF(A1>5,1/0,"safe")', "safe"), ("=$A$1+$B1", 5), ("=SQRT(16)", 4), ("=MOD(7,3)", 1),
    ("=ROW()", 1), ("=COLUMN()", 4), ("=ROW(B7)", 7), ("=COLUMN(Data!C2)", 3),
    ("=ROWS(A1:B9)", 9), ("=COLUMNS(A1:C2)", 3), ("=ROW()*10+COLUMN()", 14),
    ('=COUNTIF(C1:C2,"hel*")', 1), ('=COUNTIF(C1:C2,"h?llo")', 1), ('=COUNTIF(C1:C2,"<>x*")', 2),
])
def test_formulas(formula, expected):
    out = calc({"A1": 2, "B1": 3, "A2": 4, "B2": 6, "C1": "hello", "D1": formula},
               {"A1": 42, "A2": 8})
    assert out["D1"] == pytest.approx(expected) if isinstance(expected, (int, float)) \
        and not isinstance(expected, bool) else out["D1"] == expected


def test_chains_and_cycles():
    out = calc({"A1": 5, "B1": "=A1*2", "C1": "=B1+1", "X1": "=Y1", "Y1": "=X1"})
    assert out["C1"] == 11
    assert out["X1"] == "#CIRCULAR!"


def test_row_is_the_calculating_cell():
    out = calc({"A5": "=ROW()", "B5": "=A5+ROW()", "C9": "=B5"})
    assert out["A5"] == 5 and out["B5"] == 10 and out["C9"] == 10


def test_unknown_function_keeps_browser_value():
    book = {"Sheet1": {"A1": {"f": "=XLOOKUP(1,B1:B2,C1:C2)", "v": 7}}}
    recalculate(book)
    assert book["Sheet1"]["A1"]["v"] == 7
