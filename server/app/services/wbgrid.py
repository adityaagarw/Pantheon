"""A grid over the whiteboard, so agents place things by cell instead of guessing pixels.

The board is divided into cells of ``CELL_W`` x ``CELL_H`` pixels: columns A, B, C ...
(then AA, AB ...) from the left, rows 1, 2, 3 ... from the top. A shape can be put in a
cell ("C2"), stretched over a range ("C2:E3"), or placed next to another shape
("right of api"). Higher-level diagrams (flowcharts, mind maps, tables, timelines,
lists) are laid out on the same grid. Everything here produces plain shape dicts for
``whiteboards.add_shapes``, which still does the drawing.
"""

from __future__ import annotations

import re
from typing import Any

CELL_W, CELL_H = 220, 140
PAD_X, PAD_Y = 20, 30  # a box in a cell leaves this much room on each side (arrows go there)
SIDES = {"right": (1, 0), "left": (-1, 0), "below": (0, 1), "above": (0, -1)}
CELL = re.compile(r"^([A-Za-z]{1,2})(\d{1,3})$")


class GridError(ValueError):
    pass


def col_name(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def col_index(name: str) -> int:
    n = 0
    for ch in name.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def parse_cell(ref: str) -> tuple[int, int]:
    m = CELL.match(str(ref).strip())
    if not m:
        raise GridError(f"'{ref}' is not a cell: use a column letter and a row number, like B3")
    row = int(m.group(2))
    if row < 1:
        raise GridError("rows start at 1")
    return col_index(m.group(1)), row - 1


def parse_range(ref: str) -> tuple[int, int, int, int]:
    """'B3' or 'B3:D4' -> (col0, row0, col1, row1), inclusive."""
    parts = str(ref).split(":")
    if len(parts) > 2:
        raise GridError(f"'{ref}' is not a cell or a range like B3:D4")
    c0, r0 = parse_cell(parts[0])
    c1, r1 = parse_cell(parts[-1])
    return min(c0, c1), min(r0, r1), max(c0, c1), max(r0, r1)


def cell_name(col: int, row: int) -> str:
    return f"{col_name(col)}{row + 1}"


def cell_at(x: float, y: float) -> str:
    return cell_name(max(0, int(x // CELL_W)), max(0, int(y // CELL_H)))


def range_of(x: float, y: float, w: float, h: float) -> str:
    a, b = cell_at(x + 1, y + 1), cell_at(x + max(w, 2) - 1, y + max(h, 2) - 1)
    return a if a == b else f"{a}:{b}"


def box_in(ref: str, w: float | None = None, h: float | None = None) -> dict[str, float]:
    """The rectangle a shape gets in a cell or range: centered, with room around it."""
    c0, r0, c1, r1 = parse_range(ref)
    area_w, area_h = (c1 - c0 + 1) * CELL_W, (r1 - r0 + 1) * CELL_H
    width = min(float(w), area_w) if w else area_w - 2 * PAD_X
    height = min(float(h), area_h) if h else area_h - 2 * PAD_Y
    return {"x": c0 * CELL_W + (area_w - width) / 2, "y": r0 * CELL_H + (area_h - height) / 2,
            "width": width, "height": height}


def occupied(boxes: list[tuple[float, float, float, float]]) -> set[tuple[int, int]]:
    """Cells that something already covers (boxes are x0, y0, x1, y1)."""
    out: set[tuple[int, int]] = set()
    for x0, y0, x1, y1 in boxes:
        for c in range(max(0, int(min(x0, x1) // CELL_W)), int(max(x0, x1) // CELL_W) + 1):
            for r in range(max(0, int(min(y0, y1) // CELL_H)), int(max(y0, y1) // CELL_H) + 1):
                out.add((c, r))
    return out


def first_free_column(taken: set[tuple[int, int]]) -> int:
    return max((c for c, _ in taken), default=-1) + 1 + (1 if taken else 0)


def place(shape: dict[str, Any], known: dict[str, dict[str, Any]],
          taken: set[tuple[int, int]]) -> dict[str, Any]:
    """Turn cell / near+side into x, y (and a size that fits). Other shapes pass through."""
    s = dict(shape)
    if s.get("cell"):
        s.update(box_in(s.pop("cell"), s.get("width"), s.get("height")))
    elif s.get("near"):
        ref = known.get(str(s["near"])) or known.get(str(s["near"]).strip().lower())
        if ref is None:
            raise GridError(f"no shape '{s['near']}' to place next to (use its id or label)")
        dc, dr = SIDES.get(str(s.pop("side", "right")), (1, 0))
        s.pop("near")
        c0, r0, c1, r1 = parse_range(range_of(ref["x"], ref["y"], ref["width"], ref["height"]))
        col = c1 + 1 if dc > 0 else c0 - 1 if dc < 0 else c0
        row = r1 + 1 if dr > 0 else r0 - 1 if dr < 0 else r0
        while (col, row) in taken and 0 <= col < 500 and 0 <= row < 500:
            col, row = col + dc, row + dr
        if col < 0 or row < 0:
            raise GridError(f"there is no room {('left of' if dc < 0 else 'above')} "
                            f"'{ref.get('id')}' (the board starts at A1)")
        s.update(box_in(cell_name(col, row), s.get("width"), s.get("height")))
    else:
        s.pop("side", None)
        return s
    taken |= occupied([(s["x"], s["y"], s["x"] + s["width"], s["y"] + s["height"])])
    return s


# --- diagrams -------------------------------------------------------------------------------


def _anchor(at: str | None, taken: set[tuple[int, int]]) -> tuple[int, int]:
    if at:
        return parse_cell(at)
    return first_free_column(taken), 0


def flowchart(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], *, direction: str = "right",
              at: str | None = None, taken: set[tuple[int, int]]) -> list[dict[str, Any]]:
    """Boxes in layers (one per arrow step), then the arrows."""
    if not nodes:
        raise GridError("a flowchart needs nodes")
    ids = [str(n.get("id") or n.get("label") or f"n{i}") for i, n in enumerate(nodes)]
    key = {i.lower(): i for i in ids} | {str(n.get("label", "")).lower(): ids[k]
                                         for k, n in enumerate(nodes) if n.get("label")}

    def ref(r: Any) -> str | None:
        return key.get(str(r or "").strip().lower())

    links = [(ref(e.get("from")), ref(e.get("to"))) for e in edges]
    links = [(a, b) for a, b in links if a and b and a != b]
    depth = dict.fromkeys(ids, 0)
    for _ in range(len(ids)):
        changed = False
        for a, b in links:
            if depth[b] < depth[a] + 1 < len(ids):
                depth[b], changed = depth[a] + 1, True
        if not changed:
            break
    layers: dict[int, list[int]] = {}
    for k, i in enumerate(ids):
        layers.setdefault(depth[i], []).append(k)
    widest = max(len(v) for v in layers.values())
    c0, r0 = _anchor(at, taken)
    out: list[dict[str, Any]] = []
    for d, members in layers.items():
        offset = (widest - len(members)) // 2
        for j, k in enumerate(members):
            n = nodes[k]
            col, row = (c0 + d, r0 + offset + j) if direction != "down" else (c0 + offset + j, r0 + d)
            out.append({"type": n.get("shape") or "rectangle", "id": ids[k],
                        "label": n.get("label") or ids[k], "fill": n.get("fill"),
                        "color": n.get("color"), **box_in(cell_name(col, row))})
    for e in edges:
        a, b = ref(e.get("from")), ref(e.get("to"))
        if a and b:
            out.append({"type": "arrow", "from": a, "to": b, "label": e.get("label")})
    return out


def mindmap(center: str, branches: list[dict[str, Any]], *, at: str | None = None,
            taken: set[tuple[int, int]]) -> list[dict[str, Any]]:
    """A central idea; branches alternate right and left, their children one column further."""
    if not center:
        raise GridError("a mind map needs a center")

    def span(b: dict[str, Any]) -> int:
        return max(1, len(b.get("children") or []))

    groups = {1: [b for i, b in enumerate(branches) if i % 2 == 0],
              -1: [b for i, b in enumerate(branches) if i % 2 == 1]}
    height = max(1, *(sum(span(b) for b in g) for g in groups.values()))
    c0, r0 = parse_cell(at) if at else (first_free_column(taken), 0)
    cc, cr = c0 + 2, r0 + (height - 1) // 2
    out = [{"type": "ellipse", "id": "mm_center", "label": center, "fill": "#ffec99",
            **box_in(cell_name(cc, cr))}]
    n = 0
    for side, group in groups.items():
        row = r0 + (height - sum(span(b) for b in group)) // 2
        for b in group:
            bid = f"mm_b{n}"
            n += 1
            out.append({"type": "rectangle", "id": bid, "label": str(b.get("label", "")),
                        "fill": b.get("fill") or "#d0ebff",
                        **box_in(cell_name(cc + side, row + (span(b) - 1) // 2))})
            out.append({"type": "line", "from": "mm_center", "to": bid})
            for j, kid in enumerate(b.get("children") or []):
                kid_id = f"{bid}_{j}"
                out.append({"type": "rectangle", "id": kid_id, "label": str(kid),
                            **box_in(cell_name(cc + 2 * side, row + j))})
                out.append({"type": "line", "from": bid, "to": kid_id})
            row += span(b)
    return out


def table(columns: list[str], rows: list[list[Any]], *, at: str | None = None,
          taken: set[tuple[int, int]]) -> list[dict[str, Any]]:
    """A header row and the rows, one cell per value."""
    if not columns:
        raise GridError("a table needs columns")
    c0, r0 = _anchor(at, taken)
    out = []
    for j, head in enumerate(columns):
        out.append({"type": "rectangle", "label": str(head), "fill": "#e9ecef",
                    **_tight(cell_name(c0 + j, r0))})
    for i, row in enumerate(rows):
        for j, value in enumerate(row[:len(columns)]):
            out.append({"type": "rectangle", "label": str(value),
                        **_tight(cell_name(c0 + j, r0 + 1 + i))})
    return out


def _tight(ref: str) -> dict[str, float]:
    """A table cell fills its grid cell (no gaps), so the table reads as one block."""
    c, r = parse_cell(ref)
    return {"x": c * CELL_W, "y": r * CELL_H, "width": CELL_W, "height": CELL_H}


def timeline(events: list[dict[str, Any]], *, at: str | None = None,
             taken: set[tuple[int, int]]) -> list[dict[str, Any]]:
    """Events left to right along a line, labels alternating above and below."""
    if not events:
        raise GridError("a timeline needs events")
    c0, r0 = _anchor(at, taken)
    y = (r0 + 1) * CELL_H + CELL_H / 2
    out: list[dict[str, Any]] = [{"type": "arrow", "points": [
        [c0 * CELL_W + 10, y], [(c0 + len(events)) * CELL_W + 40, y]]}]
    for i, ev in enumerate(events):
        row = r0 if i % 2 == 0 else r0 + 2
        label = str(ev.get("label", ""))
        if ev.get("when"):
            label = f"{ev['when']}\n{label}"
        eid = f"tl_{i}"
        out.append({"type": "rectangle", "id": eid, "label": label, "fill": ev.get("fill"),
                    **box_in(cell_name(c0 + i, row))})
        cx = (c0 + i) * CELL_W + CELL_W / 2
        edge = row * CELL_H + (CELL_H - PAD_Y if i % 2 == 0 else PAD_Y)
        out.append({"type": "line", "points": [[cx, edge], [cx, y]]})
    return out


def bullet_list(title: str, items: list[str], *, at: str | None = None,
                taken: set[tuple[int, int]]) -> list[dict[str, Any]]:
    c0, r0 = _anchor(at, taken)
    text = (f"{title}\n" if title else "") + "\n".join(f"• {i}" for i in items)
    return [{"type": "text", "text": text, "size": 20,
             "x": c0 * CELL_W + PAD_X, "y": r0 * CELL_H + PAD_Y / 2}]


KINDS = ("flowchart", "mindmap", "table", "timeline", "list")


def diagram(kind: str, spec: dict[str, Any], taken: set[tuple[int, int]]) -> list[dict[str, Any]]:
    at = spec.get("at")
    if kind == "flowchart":
        return flowchart(spec.get("nodes") or [], spec.get("edges") or [],
                         direction=spec.get("direction") or "right", at=at, taken=taken)
    if kind == "mindmap":
        return mindmap(str(spec.get("center") or ""), spec.get("branches") or [], at=at, taken=taken)
    if kind == "table":
        return table(spec.get("columns") or [], spec.get("rows") or [], at=at, taken=taken)
    if kind == "timeline":
        return timeline(spec.get("events") or [], at=at, taken=taken)
    if kind == "list":
        return bullet_list(str(spec.get("title") or ""), [str(i) for i in spec.get("items") or []],
                           at=at, taken=taken)
    raise GridError(f"kind must be one of {', '.join(KINDS)}")
