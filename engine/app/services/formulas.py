"""A small spreadsheet formula evaluator.

The Stage spreadsheet calculates in the browser, but agents read and write sheets
when nobody has them open, so the server computes the common formulas too:
arithmetic, comparisons, ``&``, cell and range references (``B2``, ``$A$1``,
``A1:C9``, ``Sheet2!A1``), and the functions most sheets use. Anything else
evaluates to ``#NAME?`` and the browser's value (if any) is kept.
"""

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Callable
from typing import Any

CELL_RE = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d{1,7})$")


class FormulaError(Exception):
    """An Excel-style error value, e.g. #DIV/0!."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def col_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def col_letters(index: int) -> str:
    out = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def parse_cell(ref: str) -> tuple[int, int]:
    """'B3' -> (row 2, col 1)."""
    m = CELL_RE.match(ref.strip())
    if not m:
        raise ValueError(f"not a cell reference: {ref}")
    return int(m.group(2)) - 1, col_index(m.group(1))


def cell_name(row: int, col: int) -> str:
    return f"{col_letters(col)}{row + 1}"


def parse_range(ref: str) -> tuple[int, int, int, int]:
    """'A1:C3' (or 'B2') -> (r1, c1, r2, c2), inclusive and ordered."""
    a, _, b = ref.partition(":")
    r1, c1 = parse_cell(a)
    r2, c2 = parse_cell(b) if b else (r1, c1)
    return min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2)


# --- tokens ---------------------------------------------------------------------------------

TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<ref>(?:(?:'[^']+'|[A-Za-z_][\w.]*)!)?
            \$?[A-Za-z]{1,3}\$?\d{1,7}(?::\$?[A-Za-z]{1,3}\$?\d{1,7})?)(?![\w(])
  | (?P<num>\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)
  | (?P<bool>TRUE|FALSE)(?![\w(])
  | (?P<func>[A-Za-z][\w.]*)(?=\s*\()
  | (?P<op><>|<=|>=|[-+*/^&=<>%(),:])
""", re.X | re.I)


def tokenize(src: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    while pos < len(src):
        m = TOKEN_RE.match(src, pos)
        if not m:
            raise FormulaError("#NAME?")
        pos = m.end()
        kind = m.lastgroup or ""
        if kind != "ws":
            out.append((kind, m.group(kind)))
    return out


# --- evaluation -----------------------------------------------------------------------------


class Range:
    def __init__(self, values: list[list[Any]]) -> None:
        self.values = values

    def flat(self) -> list[Any]:
        return [v for row in self.values for v in row]


def _num(v: Any) -> float:
    if isinstance(v, Range):
        flat = v.flat()
        if len(flat) != 1:
            raise FormulaError("#VALUE!")
        v = flat[0]
    if v is None or v == "":
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        raise FormulaError("#VALUE!") from None


def _text(v: Any) -> str:
    if isinstance(v, Range):
        flat = v.flat()
        v = flat[0] if flat else ""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _truthy(v: Any) -> bool:
    if isinstance(v, str):
        if v.upper() in ("TRUE", "FALSE"):
            return v.upper() == "TRUE"
        raise FormulaError("#VALUE!")
    return bool(_num(v))


def _numbers(args: list[Any]) -> list[float]:
    """Numbers in the arguments; ranges skip blanks and text, like a spreadsheet."""
    out: list[float] = []
    for a in args:
        if isinstance(a, Range):
            out += [float(v) for v in a.flat()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)]
        else:
            out.append(_num(a))
    return out


def _values(args: list[Any]) -> list[Any]:
    out: list[Any] = []
    for a in args:
        out += a.flat() if isinstance(a, Range) else [a]
    return out


def _round(x: float, digits: float = 0) -> float:
    q = 10 ** int(digits)
    return math.floor(abs(x) * q + 0.5) / q * (1 if x >= 0 else -1)


def _criteria(crit: Any) -> Callable[[Any], bool]:
    """SUMIF/COUNTIF criteria: 5, ">5", "<>x", "apple"."""
    if not isinstance(crit, str):
        return lambda v: v is not None and v != "" and _safe_eq(v, crit)
    m = re.match(r"^(<=|>=|<>|<|>|=)?(.*)$", crit)
    op, rhs = m.group(1) or "=", m.group(2)
    try:
        target: Any = float(rhs)
    except ValueError:
        target = rhs
    if isinstance(target, str) and op in ("=", "<>") and re.search(r"[*?]", target):
        # Wildcards, as in COUNTIF(H2:H9, "Yes*"): * any text, ? one character, ~ escapes.
        pattern = re.compile("".join(
            re.escape(p[-1]) if p.startswith("~") else ".*" if p == "*" else "." if p == "?"
            else re.escape(p) for p in re.findall(r"~[*?~]|[*?]|[^*?~]+|~", target)),
            re.I | re.S)
        return lambda v: (pattern.fullmatch(_text(v)) is not None) == (op == "=")

    def test(v: Any) -> bool:
        try:
            return _compare(op, v, target)
        except FormulaError:
            return False
    return test


def _safe_eq(a: Any, b: Any) -> bool:
    try:
        return _compare("=", a, b)
    except FormulaError:
        return False


def _compare(op: str, a: Any, b: Any) -> bool:
    if isinstance(a, Range) or isinstance(b, Range):
        a, b = _scalar(a), _scalar(b)
    if isinstance(a, str) or isinstance(b, str):
        x, y = _text(a).lower(), _text(b).lower()
        if isinstance(a, (int, float)) and isinstance(b, str) or isinstance(b, (int, float)) \
                and isinstance(a, str):
            try:
                x, y = _num(a), _num(b)  # type: ignore[assignment]
            except FormulaError:
                return op == "<>"
    else:
        x, y = _num(a), _num(b)  # type: ignore[assignment]
    return {"=": x == y, "<>": x != y, "<": x < y, ">": x > y, "<=": x <= y, ">=": x >= y}[op]


def _scalar(v: Any) -> Any:
    if isinstance(v, Range):
        flat = v.flat()
        if len(flat) != 1:
            raise FormulaError("#VALUE!")
        return flat[0]
    return v


def _sumif(rng: Range, crit: Any, total: Range | None = None) -> float:
    test = _criteria(crit)
    cells, sums = rng.flat(), (total or rng).flat()
    return sum(_num(s) for v, s in zip(cells, sums, strict=False)
               if test(v) and isinstance(s, (int, float)))


def _average(*a: Any) -> float:
    nums = _numbers(list(a))
    if not nums:
        raise FormulaError("#DIV/0!")
    return sum(nums) / len(nums)


def _sqrt(x: float) -> float:
    if x < 0:
        raise FormulaError("#NUM!")
    return math.sqrt(x)


def _mod(x: float, y: float) -> float:
    if y == 0:
        raise FormulaError("#DIV/0!")
    return x % y


def _if(cond: Any, yes: Any = True, no: Any = False) -> Any:
    return yes if _truthy(cond) else no


def _vlookup(key: Any, table: Range, col: Any, approx: Any = False) -> Any:
    idx = int(_num(col)) - 1
    for row in table.values:
        if row and _safe_eq(row[0], key):
            if not 0 <= idx < len(row):
                raise FormulaError("#REF!")
            return row[idx]
    raise FormulaError("#N/A")


FUNCS: dict[str, Callable[..., Any]] = {
    "SUM": lambda *a: sum(_numbers(list(a))),
    "AVERAGE": _average,
    "MIN": lambda *a: min(_numbers(list(a)), default=0.0),
    "MAX": lambda *a: max(_numbers(list(a)), default=0.0),
    "COUNT": lambda *a: float(len(_numbers([x for x in a if isinstance(x, Range)]))
                              + sum(1 for x in a if not isinstance(x, Range)
                                    and isinstance(x, (int, float)))),
    "COUNTA": lambda *a: float(sum(1 for v in _values(list(a)) if v not in (None, ""))),
    "COUNTBLANK": lambda *a: float(sum(1 for v in _values(list(a)) if v in (None, ""))),
    "PRODUCT": lambda *a: math.prod(_numbers(list(a))),
    "MEDIAN": lambda *a: statistics.median(_numbers(list(a))),
    "ROUND": lambda x, d=0: _round(_num(x), _num(d)),
    "ROUNDUP": lambda x, d=0: math.ceil(abs(_num(x)) * 10 ** int(_num(d))) / 10 ** int(_num(d))
    * (1 if _num(x) >= 0 else -1),
    "ROUNDDOWN": lambda x, d=0: math.floor(abs(_num(x)) * 10 ** int(_num(d)))
    / 10 ** int(_num(d)) * (1 if _num(x) >= 0 else -1),
    "INT": lambda x: float(math.floor(_num(x))),
    "ABS": lambda x: abs(_num(x)),
    "SQRT": lambda x: _sqrt(_num(x)),
    "POWER": lambda x, y: _num(x) ** _num(y),
    "MOD": lambda x, y: _mod(_num(x), _num(y)),
    "IF": _if,
    "IFERROR": lambda v, alt: v,  # errors are caught in Evaluator.call
    "AND": lambda *a: all(_truthy(v) for v in _values(list(a))),
    "OR": lambda *a: any(_truthy(v) for v in _values(list(a))),
    "NOT": lambda v: not _truthy(v),
    "CONCAT": lambda *a: "".join(_text(v) for v in _values(list(a))),
    "CONCATENATE": lambda *a: "".join(_text(v) for v in _values(list(a))),
    "LEN": lambda v: float(len(_text(v))),
    "UPPER": lambda v: _text(v).upper(),
    "LOWER": lambda v: _text(v).lower(),
    "TRIM": lambda v: " ".join(_text(v).split()),
    "LEFT": lambda v, n=1: _text(v)[: int(_num(n))],
    "RIGHT": lambda v, n=1: _text(v)[-int(_num(n)):] if int(_num(n)) else "",
    "SUMIF": _sumif,
    "COUNTIF": lambda rng, crit: float(sum(1 for v in rng.flat() if _criteria(crit)(v))),
    "AVERAGEIF": lambda rng, crit, total=None: _average(Range([[
        s for v, s in zip(rng.flat(), (total or rng).flat(), strict=False)
        if _criteria(crit)(v)]])),
    "VLOOKUP": _vlookup,
    "PI": lambda: math.pi,
}


class Evaluator:
    """Evaluates formulas over a workbook: {sheet name: {"A1": {"v": .., "f": ..}}}."""

    def __init__(self, sheets: dict[str, dict[str, dict[str, Any]]]) -> None:
        self.sheets = sheets
        self.cache: dict[tuple[str, str], Any] = {}
        self.busy: set[tuple[str, str]] = set()
        self.at: list[str] = []  # the cells being calculated, innermost last (for ROW())

    def value(self, sheet: str, ref: str) -> Any:
        key = (sheet, ref)
        if key in self.cache:
            return self.cache[key]
        cell = self.sheets.get(sheet, {}).get(ref)
        if not cell:
            return None
        f = cell.get("f")
        if not f:
            return cell.get("v")
        if key in self.busy:
            raise FormulaError("#CIRCULAR!")
        self.busy.add(key)
        self.at.append(ref)
        try:
            v = self.formula(sheet, f)
        except FormulaError as e:
            v = e.code
        finally:
            self.busy.discard(key)
            self.at.pop()
        self.cache[key] = v
        return v

    def formula(self, sheet: str, f: str) -> Any:
        tokens = tokenize(f[1:] if f.startswith("=") else f)
        p = _Parser(tokens, sheet, self)
        v = p.expr()
        if p.i != len(tokens):
            raise FormulaError("#ERROR!")
        v = _scalar(v) if isinstance(v, Range) else v
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            raise FormulaError("#NUM!")
        return v

    def resolve(self, sheet: str, ref: str) -> Any:
        if "!" in ref:
            name, _, ref = ref.rpartition("!")
            sheet = name.strip("'")
            if sheet not in self.sheets:
                raise FormulaError("#REF!")
        r1, c1, r2, c2 = parse_range(ref)
        if (r2 - r1 + 1) * (c2 - c1 + 1) > 100_000:
            raise FormulaError("#REF!")
        if ":" not in ref:
            v = self.value(sheet, cell_name(r1, c1))
            if isinstance(v, str) and v.startswith("#") and v.endswith(("!", "?", "A")):
                raise FormulaError(v)
            return v
        return Range([[self.value(sheet, cell_name(r, c)) for c in range(c1, c2 + 1)]
                      for r in range(r1, r2 + 1)])

    def call(self, name: str, args: list[Callable[[], Any]]) -> Any:
        fn = FUNCS.get(name.upper())
        if fn is None:
            raise FormulaError("#NAME?")
        if name.upper() == "IFERROR":
            try:
                return args[0]()
            except FormulaError:
                return args[1]() if len(args) > 1 else ""
        if name.upper() == "IF":  # only the chosen branch is evaluated
            cond = args[0]() if args else False
            branch = 1 if _truthy(cond) else 2
            return args[branch]() if len(args) > branch else (branch == 1)
        try:
            return fn(*[a() for a in args])
        except FormulaError:
            raise
        except (TypeError, ValueError, ZeroDivisionError, statistics.StatisticsError):
            raise FormulaError("#VALUE!") from None


class _Parser:
    """Recursive descent: comparison < & < +- < */ < ^ < unary < % < primary."""

    def __init__(self, tokens: list[tuple[str, str]], sheet: str, ev: Evaluator) -> None:
        self.t, self.i, self.sheet, self.ev = tokens, 0, sheet, ev

    def peek(self) -> tuple[str, str]:
        return self.t[self.i] if self.i < len(self.t) else ("", "")

    def take(self, value: str | None = None) -> tuple[str, str]:
        tok = self.peek()
        if not tok[0] or (value is not None and tok[1] != value):
            raise FormulaError("#ERROR!")
        self.i += 1
        return tok

    def expr(self) -> Any:
        left = self.concat()
        while self.peek()[1] in ("=", "<>", "<", ">", "<=", ">="):
            op = self.take()[1]
            left = _compare(op, left, self.concat())
        return left

    def concat(self) -> Any:
        left = self.add()
        while self.peek()[1] == "&":
            self.take()
            left = _text(left) + _text(self.add())
        return left

    def add(self) -> Any:
        left = self.mul()
        while self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            right = self.mul()
            left = _num(left) + _num(right) if op == "+" else _num(left) - _num(right)
        return left

    def mul(self) -> Any:
        left = self.power()
        while self.peek()[1] in ("*", "/"):
            op = self.take()[1]
            right = _num(self.power())
            if op == "/" and right == 0:
                raise FormulaError("#DIV/0!")
            left = _num(left) * right if op == "*" else _num(left) / right
        return left

    def power(self) -> Any:
        left = self.unary()
        while self.peek()[1] == "^":
            self.take()
            left = _num(left) ** _num(self.unary())
        return left

    def unary(self) -> Any:
        if self.peek()[1] in ("-", "+"):
            op = self.take()[1]
            v = _num(self.unary())
            return -v if op == "-" else v
        v = self.primary()
        while self.peek()[1] == "%":
            self.take()
            v = _num(v) / 100
        return v

    def primary(self) -> Any:
        kind, val = self.take()
        if kind == "num":
            return float(val)
        if kind == "str":
            return val[1:-1].replace('""', '"')
        if kind == "bool":
            return val.upper() == "TRUE"
        if kind == "ref":
            return self.ev.resolve(self.sheet, val)
        if kind == "func":
            self.take("(")
            spans: list[tuple[int, int]] = []
            if self.peek()[1] != ")":
                while True:
                    a = self.i
                    self._skip_arg()
                    spans.append((a, self.i))
                    if self.peek()[1] == ",":
                        self.take()
                        continue
                    break
            self.take(")")
            end = self.i
            if val.upper() in ("ROW", "COLUMN", "ROWS", "COLUMNS"):
                return self._position(val.upper(), spans)
            args = [self._lazy(a, b) for a, b in spans]
            self.i = end
            return self.ev.call(val, args)
        if val == "(":
            v = self.expr()
            self.take(")")
            return v
        raise FormulaError("#ERROR!")

    def _skip_arg(self) -> None:
        depth = 0
        while self.i < len(self.t):
            v = self.t[self.i][1]
            if v == "(" and self.t[self.i][0] == "op":
                depth += 1
            elif v == ")" and self.t[self.i][0] == "op":
                if depth == 0:
                    return
                depth -= 1
            elif v == "," and depth == 0:
                return
            self.i += 1

    def _position(self, name: str, spans: list[tuple[int, int]]) -> float:
        """ROW, COLUMN, ROWS and COLUMNS read the reference itself, not its values."""
        if not spans:
            if name in ("ROWS", "COLUMNS") or not self.ev.at:
                raise FormulaError("#VALUE!")
            ref = self.ev.at[-1]
        else:
            a, b = spans[0]
            if len(spans) > 1 or b - a != 1 or self.t[a][0] != "ref":
                raise FormulaError("#VALUE!")
            ref = self.t[a][1].rpartition("!")[2]
        r1, c1, r2, c2 = parse_range(ref)
        return float({"ROW": r1 + 1, "COLUMN": c1 + 1, "ROWS": r2 - r1 + 1,
                      "COLUMNS": c2 - c1 + 1}[name])

    def _lazy(self, a: int, b: int) -> Callable[[], Any]:
        def run() -> Any:
            if a == b:
                return None
            sub = _Parser(self.t[a:b], self.sheet, self.ev)
            v = sub.expr()
            if sub.i != b - a:
                raise FormulaError("#ERROR!")
            return v
        return run


def recalculate(sheets: dict[str, dict[str, dict[str, Any]]]) -> int:
    """Compute every formula cell in place. Returns how many were computed."""
    ev = Evaluator(sheets)
    n = 0
    for sheet, cells in sheets.items():
        for ref, cell in cells.items():
            if cell.get("f"):
                v = ev.value(sheet, ref)
                if isinstance(v, str) and v == "#NAME?" and "v" in cell and cell.get("v") \
                        not in (None, "#NAME?"):
                    continue  # a function we don't know: keep the browser's value
                cell["v"] = v
                n += 1
    return n


def display(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.10g}"
    return str(v)
