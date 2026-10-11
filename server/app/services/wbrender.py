"""Draw a whiteboard as a PNG, so an agent can look at it (whiteboard_view).

It draws what Excalidraw stores (boxes, ellipses, diamonds, text, arrows, lines,
freehand strokes; images as placeholders) plus the shapes agents queued that an editor
hasn't converted yet, and lays the cell grid over it (columns A, B, C... across the
top, rows 1, 2, 3... down the side) so what the agent sees matches how it places things.
"""

from __future__ import annotations

import base64
import io
import math
from functools import cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.services import wbgrid

MAX_W, MAX_H = 1600, 1200
GUTTER = 26
FONTS = ("/usr/share/fonts/truetype/freefont/FreeSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf")


@cache
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = max(8, min(int(size), 96))
    for path in FONTS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def _color(c: Any, default: str) -> str | None:
    c = str(c or "")
    if c == "transparent":
        return None
    return c if c.startswith("#") and len(c) in (4, 7, 9) else default


def _elements(live: list[dict[str, Any]], pending: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Live elements, plus pending skeletons in the same shape (labels become bound text)."""
    out = list(live)
    for p in pending:
        e = dict(p)
        if e.get("type") in ("arrow", "line") and "points" not in e:
            e["points"] = [[0, 0], [e.get("width", 0), e.get("height", 0)]]
        out.append(e)
        if isinstance(p.get("label"), dict) and p["label"].get("text"):
            out.append({"type": "text", "text": p["label"]["text"], "containerId": p["id"],
                        "fontSize": 20, "x": p.get("x", 0), "y": p.get("y", 0)})
    return out


def _bounds(elements: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    xs, ys = [0.0], [0.0]
    for e in elements:
        x, y = float(e.get("x") or 0), float(e.get("y") or 0)
        pts = e.get("points") or [[0, 0], [e.get("width") or 0, e.get("height") or 0]]
        for px, py in pts:
            xs.append(x + float(px))
            ys.append(y + float(py))
    # Whole cells, with one empty cell of margin on the right and bottom.
    x0 = math.floor(min(xs) / wbgrid.CELL_W) * wbgrid.CELL_W
    y0 = math.floor(min(ys) / wbgrid.CELL_H) * wbgrid.CELL_H
    x1 = (math.floor(max(xs) / wbgrid.CELL_W) + 2) * wbgrid.CELL_W
    y1 = (math.floor(max(ys) / wbgrid.CELL_H) + 2) * wbgrid.CELL_H
    return x0, y0, x1, y1


def render(live: list[dict[str, Any]], pending: list[dict[str, Any]], *, grid: bool = True) -> bytes:
    elements = _elements(live, pending)
    x0, y0, x1, y1 = _bounds(elements)
    scale = min(1.0, MAX_W / (x1 - x0), MAX_H / (y1 - y0))
    g = GUTTER if grid else 0
    img = Image.new("RGB", (int((x1 - x0) * scale) + g, int((y1 - y0) * scale) + g), "white")
    d = ImageDraw.Draw(img)

    def P(x: float, y: float) -> tuple[float, float]:
        return (x - x0) * scale + g, (y - y0) * scale + g

    if grid:
        label = _font(13)
        c = int(x0 // wbgrid.CELL_W)
        while c * wbgrid.CELL_W <= x1:
            px = P(c * wbgrid.CELL_W, 0)[0]
            d.line([(px, g), (px, img.height)], fill="#e3e6ea")
            if c >= 0 and (c + 0.5) * wbgrid.CELL_W < x1:
                d.text((P((c + 0.5) * wbgrid.CELL_W, 0)[0], g / 2), wbgrid.col_name(c),
                       fill="#868e96", font=label, anchor="mm")
            c += 1
        r = int(y0 // wbgrid.CELL_H)
        while r * wbgrid.CELL_H <= y1:
            py = P(0, r * wbgrid.CELL_H)[1]
            d.line([(g, py), (img.width, py)], fill="#e3e6ea")
            if r >= 0 and (r + 0.5) * wbgrid.CELL_H < y1:
                d.text((g / 2, P(0, (r + 0.5) * wbgrid.CELL_H)[1]), str(r + 1),
                       fill="#868e96", font=label, anchor="mm")
            r += 1

    boxes = {e.get("id"): e for e in elements}
    for e in elements:
        t = e.get("type")
        x, y = float(e.get("x") or 0), float(e.get("y") or 0)
        w, h = float(e.get("width") or 0), float(e.get("height") or 0)
        stroke = _color(e.get("strokeColor"), "#1e1e1e") or "#1e1e1e"
        fill = _color(e.get("backgroundColor"), "#ffffff")
        width = max(1, round(float(e.get("strokeWidth") or 2) * scale))
        a, b = P(min(x, x + w), min(y, y + h)), P(max(x, x + w), max(y, y + h))
        if t == "rectangle":
            d.rounded_rectangle([a, b], radius=8 * scale if e.get("roundness") else 0,
                                outline=stroke, fill=fill, width=width)
        elif t == "ellipse":
            d.ellipse([a, b], outline=stroke, fill=fill, width=width)
        elif t == "diamond":
            cx, cy = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            d.polygon([(cx, a[1]), (b[0], cy), (cx, b[1]), (a[0], cy)], outline=stroke,
                      fill=fill, width=width)
        elif t in ("arrow", "line", "freedraw"):
            pts = [P(x + float(px), y + float(py)) for px, py in e.get("points") or []]
            if len(pts) >= 2:
                d.line(pts, fill=stroke, width=width, joint="curve")
                if t == "arrow" and e.get("endArrowhead", "arrow"):
                    _arrowhead(d, pts[-2], pts[-1], stroke, max(8.0, 14 * scale))
        elif t == "image":
            d.rectangle([a, b], outline="#adb5bd", fill="#f1f3f5", width=1)
            d.text(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), "image", fill="#868e96",
                   font=_font(14), anchor="mm")
    for e in elements:  # text last, on top
        if e.get("type") != "text" or not str(e.get("text") or "").strip():
            continue
        font = _font(float(e.get("fontSize") or 20) * scale)
        color = _color(e.get("strokeColor"), "#1e1e1e") or "#1e1e1e"
        text = str(e["text"])
        box = boxes.get(e.get("containerId"))
        if box is not None:
            bx, by = float(box.get("x") or 0), float(box.get("y") or 0)
            bw, bh = float(box.get("width") or 0), float(box.get("height") or 0)
            if box.get("type") in ("arrow", "line"):
                pts = box.get("points") or [[0, 0], [bw, bh]]
                mid = pts[len(pts) // 2] if len(pts) > 2 else [(pts[0][0] + pts[-1][0]) / 2,
                                                              (pts[0][1] + pts[-1][1]) / 2]
                center = P(bx + float(mid[0]), by + float(mid[1]))
                tw = d.multiline_textbbox(center, text, font=font, anchor="mm", align="center")
                d.rectangle(tw, fill="white")
            else:
                center = P(bx + bw / 2, by + bh / 2)
            d.multiline_text(center, _wrap(text, font, bw * scale - 8 if bw else 10_000), fill=color,
                             font=font, anchor="mm", align="center")
        else:
            d.multiline_text(P(float(e.get("x") or 0), float(e.get("y") or 0)), text, fill=color,
                             font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _wrap(text: str, font: Any, width: float) -> str:
    lines = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            trial = f"{line} {word}".strip()
            if line and font.getlength(trial) > width:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
    return "\n".join(lines)


def _arrowhead(d: ImageDraw.ImageDraw, a: tuple[float, float], b: tuple[float, float],
               color: str, size: float) -> None:
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    left = (b[0] - size * math.cos(ang - 0.45), b[1] - size * math.sin(ang - 0.45))
    right = (b[0] - size * math.cos(ang + 0.45), b[1] - size * math.sin(ang + 0.45))
    d.polygon([b, left, right], fill=color)


def data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()
