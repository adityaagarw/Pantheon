"""Shared Excalidraw whiteboards.

The browser owns the Excalidraw scene; the server stores it and merges edits
element by element (a newer ``version`` wins, ties go to the lower
``versionNonce`` — Excalidraw's own reconciliation rule), so the user and agents
can draw at the same time without overwriting each other.

Agents don't write raw Excalidraw JSON: they describe shapes ("a box labeled
Database, an arrow from API to Database"), which become Excalidraw *skeleton*
elements in ``pending``; an open editor converts them into real elements with
Excalidraw's own converter and saves them back. Agents read boards as text.
"""

from __future__ import annotations

import base64
import secrets
from typing import Any

from sqlalchemy import select

from app.core.db import SessionLocal
from app.events import bus
from app.models import Whiteboard

SHAPES = ("rectangle", "ellipse", "diamond", "text", "arrow", "line")
DEFAULT_SIZE = {"rectangle": (180, 80), "ellipse": (160, 90), "diamond": (170, 110)}
MAX_ELEMENTS = 5000


class WhiteboardError(ValueError):
    pass


def to_dict(b: Whiteboard, *, scene: bool = False) -> dict[str, Any]:
    out = {"id": b.id, "orgId": b.org_id, "title": b.title, "version": b.version,
           "updatedBy": b.updated_by, "hasThumbnail": bool(b.thumbnail),
           "elementCount": sum(1 for e in b.elements or [] if not e.get("isDeleted")),
           "pendingCount": len(b.pending or []),
           "updatedAt": b.updated_at.isoformat() if b.updated_at else None}
    if scene:
        out |= {"elements": b.elements or [], "appState": b.app_state or {},
                "files": b.files or {}, "pending": b.pending or []}
    return out


async def for_org(org_id: str, *, ensure: bool = True) -> list[Whiteboard]:
    async with SessionLocal() as session:
        rows = list((await session.execute(select(Whiteboard).where(
            Whiteboard.org_id == org_id).order_by(Whiteboard.created_at))).scalars())
    if not rows and ensure:
        rows = [await create(org_id, "Whiteboard")]
    return rows


async def create(org_id: str, title: str, by: str = "user") -> Whiteboard:
    b = Whiteboard(org_id=org_id, title=(title or "Whiteboard").strip()[:120], elements=[],
                   app_state={}, files={}, pending=[], updated_by=by)
    async with SessionLocal() as session:
        session.add(b)
        await session.commit()
    await bus.publish("whiteboard.updated", to_dict(b), org_id=org_id)
    return b


async def get(board_id: str) -> Whiteboard | None:
    async with SessionLocal() as session:
        return await session.get(Whiteboard, board_id)


async def resolve(org_id: str, ref: str | None) -> Whiteboard:
    boards = await for_org(org_id)
    if not ref:
        return boards[0]
    q = ref.strip().lower()
    for b in boards:
        if b.id == ref or b.title.lower() == q:
            return b
    raise WhiteboardError(f"no whiteboard '{ref}' (boards: {', '.join(b.title for b in boards)})")


def _newer(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Does element ``a`` supersede ``b``?"""
    va, vb = int(a.get("version") or 0), int(b.get("version") or 0)
    if va != vb:
        return va > vb
    return int(a.get("versionNonce") or 0) < int(b.get("versionNonce") or 0)


def merge(stored: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> tuple[list, bool]:
    by_id = {e["id"]: e for e in stored if isinstance(e, dict) and e.get("id")}
    order = [e["id"] for e in stored if isinstance(e, dict) and e.get("id")]
    changed = False
    for e in incoming:
        if not isinstance(e, dict) or not e.get("id"):
            continue
        cur = by_id.get(e["id"])
        if cur is None:
            order.append(e["id"])
            by_id[e["id"]] = e
            changed = True
        elif _newer(e, cur):
            by_id[e["id"]] = e
            changed = True
    return [by_id[i] for i in order], changed


async def save_scene(board_id: str, elements: list[dict[str, Any]], *,
                     app_state: dict[str, Any] | None = None,
                     files: dict[str, Any] | None = None, by: str = "user") -> Whiteboard:
    if len(elements) > MAX_ELEMENTS:
        raise WhiteboardError(f"too many elements ({len(elements)})")
    async with SessionLocal() as session:
        b = await session.get(Whiteboard, board_id, with_for_update=True)
        if b is None:
            raise WhiteboardError("whiteboard not found")
        merged, changed = merge(list(b.elements or []), elements)
        if app_state:
            keep = {k: v for k, v in app_state.items()
                    if k in ("viewBackgroundColor", "gridSize", "gridModeEnabled")}
            if keep != (b.app_state or {}):
                b.app_state, changed = keep, True
        if files:
            new_files = {k: v for k, v in files.items() if k not in (b.files or {})}
            if new_files:
                b.files, changed = {**(b.files or {}), **new_files}, True
        if changed:
            b.elements = merged
            b.version += 1
            b.updated_by = by
        await session.commit()
    if changed:
        await bus.publish("whiteboard.updated", to_dict(b), org_id=b.org_id)
    return b


async def set_thumbnail(board_id: str, data_url: str) -> None:
    if not data_url.startswith("data:image/png;base64,") or len(data_url) > 3_000_000:
        raise WhiteboardError("thumbnail must be a PNG data: URL under 3 MB")
    async with SessionLocal() as session:
        b = await session.get(Whiteboard, board_id)
        if b is None:
            raise WhiteboardError("whiteboard not found")
        b.thumbnail = data_url
        await session.commit()


def thumbnail_png(b: Whiteboard) -> bytes | None:
    if not b.thumbnail:
        return None
    return base64.b64decode(b.thumbnail.split(",", 1)[1])


async def claim_pending(board_id: str) -> list[dict[str, Any]]:
    """An open editor takes the agents' queued shapes to convert and save."""
    async with SessionLocal() as session:
        b = await session.get(Whiteboard, board_id, with_for_update=True)
        if b is None:
            raise WhiteboardError("whiteboard not found")
        items, b.pending = list(b.pending or []), []
        await session.commit()
    return items


# --- agents' view ----------------------------------------------------------------------


def _live(b: Whiteboard) -> list[dict[str, Any]]:
    return [e for e in b.elements or [] if isinstance(e, dict) and not e.get("isDeleted")]


def _bounds(elements: list[dict[str, Any]]) -> tuple[float, float, float, float] | None:
    boxes = [(float(e.get("x", 0)), float(e.get("y", 0)),
              float(e.get("x", 0)) + abs(float(e.get("width") or 0)),
              float(e.get("y", 0)) + abs(float(e.get("height") or 0))) for e in elements]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def describe(b: Whiteboard) -> str:
    live = _live(b)
    by_id = {e["id"]: e for e in live}
    labels: dict[str, str] = {}
    for e in live:
        if e.get("type") == "text" and e.get("containerId"):
            labels[e["containerId"]] = str(e.get("text", "")).replace("\n", " ")

    def name(eid: str | None) -> str:
        if not eid:
            return "nothing"
        e = by_id.get(eid)
        label = labels.get(eid)
        return f"{e.get('type') if e else 'shape'} '{label}' ({eid})" if label \
            else f"{e.get('type') if e else 'shape'} ({eid})"

    lines = [f"Whiteboard '{b.title}' ({b.id}), version {b.version}."]
    counted = {"freedraw": 0, "image": 0}
    for e in live:
        t = e.get("type")
        if t in counted:
            counted[t] += 1
            continue
        if t == "text" and e.get("containerId"):
            continue
        pos = f"at ({round(e.get('x', 0))}, {round(e.get('y', 0))})"
        by = (e.get("customData") or {}).get("by")
        who = f" [by {by}]" if by else ""
        if t == "text":
            lines.append(f"- text \"{str(e.get('text', '')).replace(chr(10), ' ')[:200]}\" {pos} "
                         f"({e['id']}){who}")
        elif t in ("arrow", "line"):
            s = (e.get("startBinding") or {}).get("elementId")
            en = (e.get("endBinding") or {}).get("elementId")
            label = labels.get(e["id"])
            lines.append(f"- {t} from {name(s)} to {name(en)}"
                         + (f" labeled '{label}'" if label else "") + f" ({e['id']}){who}")
        else:
            size = f"{round(abs(e.get('width') or 0))}x{round(abs(e.get('height') or 0))}"
            label = labels.get(e["id"])
            lines.append(f"- {t}" + (f" '{label}'" if label else "") + f" {pos} size {size}"
                         + (f" fill {e['backgroundColor']}" if e.get("backgroundColor")
                            not in (None, "transparent") else "") + f" ({e['id']}){who}")
    for k, n in counted.items():
        if n:
            lines.append(f"- {n} {'freehand stroke' if k == 'freedraw' else 'image'}"
                         f"{'s' if n > 1 else ''}")
    for p in b.pending or []:
        label = (p.get("label") or {}).get("text") or p.get("text") or ""
        lines.append(f"- (being drawn) {p.get('type')} '{label}' ({p.get('id')})")
    if len(lines) == 1:
        lines.append("It's empty.")
    return "\n".join(lines)


def _skeleton(shape: dict[str, Any], by: str, known: dict[str, dict[str, Any]],
              cursor: list[float]) -> dict[str, Any]:
    t = shape.get("type")
    if t not in SHAPES:
        raise WhiteboardError(f"shape type must be one of {', '.join(SHAPES)}")
    sid = str(shape.get("id") or f"ag_{secrets.token_hex(4)}")
    stroke = shape.get("color") or "#1e1e1e"
    base: dict[str, Any] = {"id": sid, "type": t, "strokeColor": stroke,
                            "customData": {"by": by}}
    if t in DEFAULT_SIZE:
        w, h = DEFAULT_SIZE[t]
        w, h = float(shape.get("width") or w), float(shape.get("height") or h)
        x = float(shape["x"]) if shape.get("x") is not None else cursor[0]
        y = float(shape["y"]) if shape.get("y") is not None else cursor[1]
        if shape.get("x") is None and shape.get("y") is None:
            cursor[1] += h + 40
        base |= {"x": x, "y": y, "width": w, "height": h}
        if shape.get("fill"):
            base |= {"backgroundColor": shape["fill"], "fillStyle": "solid"}
        if shape.get("label"):
            base["label"] = {"text": str(shape["label"])[:500]}
    elif t == "text":
        x = float(shape["x"]) if shape.get("x") is not None else cursor[0]
        y = float(shape["y"]) if shape.get("y") is not None else cursor[1]
        if shape.get("y") is None:
            cursor[1] += 50
        base |= {"x": x, "y": y, "text": str(shape.get("text") or shape.get("label") or "")[:2000],
                 "fontSize": float(shape.get("size") or 20)}
    else:  # arrow / line: between two shapes, or explicit points
        def find(ref: Any) -> dict[str, Any] | None:
            ref = str(ref or "").strip()
            return known.get(ref) or known.get(ref.lower()) if ref else None

        src, dst = find(shape.get("from")), find(shape.get("to"))
        if src and dst:
            sx, sy = src["x"] + src["width"], src["y"] + src["height"] / 2
            dx, dy = dst["x"], dst["y"] + dst["height"] / 2
            if dx < sx:  # target is to the left: connect bottom to top instead
                sx, sy = src["x"] + src["width"] / 2, src["y"] + src["height"]
                dx, dy = dst["x"] + dst["width"] / 2, dst["y"]
            base |= {"x": sx, "y": sy, "width": dx - sx, "height": dy - sy,
                     "start": {"id": src["id"]}, "end": {"id": dst["id"]}}
        else:
            pts = shape.get("points") or []
            if len(pts) < 2:
                raise WhiteboardError(f"{t} needs from/to shapes or at least two points")
            (x0, y0), (x1, y1) = pts[0], pts[-1]
            base |= {"x": float(x0), "y": float(y0), "width": float(x1) - float(x0),
                     "height": float(y1) - float(y0)}
        if shape.get("label"):
            base["label"] = {"text": str(shape["label"])[:200]}
        if t == "arrow":
            base["endArrowhead"] = "arrow"
    return base


def _layout(shapes: list[dict[str, Any]], origin: tuple[float, float]) -> list[dict[str, Any]]:
    """Give unplaced boxes positions: a left-to-right flow, one column per arrow step."""
    shapes = [dict(s) for s in shapes]
    nodes = [s for s in shapes if s.get("type") in DEFAULT_SIZE
             and s.get("x") is None and s.get("y") is None]
    if not nodes:
        return shapes
    for s in nodes:
        s.setdefault("id", f"ag_{secrets.token_hex(4)}")
    key = {}
    for s in nodes:
        key[str(s["id"])] = s["id"]
        if s.get("label"):
            key[str(s["label"]).strip().lower()] = s["id"]

    def ref(r: Any) -> str | None:
        r = str(r or "").strip()
        return key.get(r) or key.get(r.lower())

    edges = [(ref(s.get("from")), ref(s.get("to"))) for s in shapes
             if s.get("type") in ("arrow", "line")]
    edges = [(a, c) for a, c in edges if a and c and a != c]
    depth = {s["id"]: 0 for s in nodes}
    for _ in range(len(nodes)):  # longest path, cycles capped by the iteration count
        changed = False
        for a, c in edges:
            if depth[c] < depth[a] + 1 and depth[a] + 1 < len(nodes):
                depth[c], changed = depth[a] + 1, True
        if not changed:
            break
    columns: dict[int, list[dict[str, Any]]] = {}
    for s in nodes:
        columns.setdefault(depth[s["id"]], []).append(s)
    tallest = max(len(c) for c in columns.values())
    for col, members in columns.items():
        w = max(float(m.get("width") or DEFAULT_SIZE[m["type"]][0]) for m in members)
        top = origin[1] + (tallest - len(members)) * 75  # center shorter columns
        for i, m in enumerate(members):
            m["x"] = origin[0] + col * (max(w, 180) + 150)
            m["y"] = top + i * 150
    return shapes


async def add_shapes(board_id: str, shapes: list[dict[str, Any]], by: str) -> list[dict]:
    """Queue agent-described shapes for an editor to convert; returns the skeletons."""
    if not shapes:
        raise WhiteboardError("no shapes given")
    async with SessionLocal() as session:
        b = await session.get(Whiteboard, board_id, with_for_update=True)
        if b is None:
            raise WhiteboardError("whiteboard not found")
        live = _live(b)
        known: dict[str, dict[str, Any]] = {}
        for e in live:
            if e.get("type") in DEFAULT_SIZE:
                known[e["id"]] = {k: float(e.get(k) or 0) for k in ("x", "y", "width", "height")}
                known[e["id"]]["id"] = e["id"]
        labels = {e.get("containerId"): str(e.get("text", "")).lower() for e in live
                  if e.get("type") == "text" and e.get("containerId")}
        for p in b.pending or []:
            if p.get("type") in DEFAULT_SIZE:
                known[p["id"]] = {"id": p["id"], "x": p["x"], "y": p["y"],
                                  "width": p["width"], "height": p["height"]}
                labels[p["id"]] = str((p.get("label") or {}).get("text", "")).lower()
        # Let agents refer to shapes by their label too.
        for sid, label in list(labels.items()):
            if sid in known and label:
                known.setdefault(label, known[sid])
        right = (_bounds(live + list(b.pending or [])) or (0, 0, -80, 0))[2]
        cursor = [right + 80, 0.0]
        shapes = _layout(shapes[:200], origin=(right + 80, 0.0))
        out = []
        for shape in shapes[:200]:
            sk = _skeleton(shape, by, {**known, **{k.lower(): v for k, v in known.items()}},
                           cursor)
            if sk["type"] in DEFAULT_SIZE:
                known[sk["id"]] = {k: sk[k] for k in ("id", "x", "y", "width", "height")}
                if sk.get("label"):
                    known[sk["label"]["text"].lower()] = known[sk["id"]]
            out.append(sk)
        b.pending = [*(b.pending or []), *out]
        b.version += 1
        b.updated_by = by
        await session.commit()
    await bus.publish("whiteboard.updated", to_dict(b), org_id=b.org_id)
    return out


async def erase(board_id: str, *, ids: list[str] | None = None, by: str | None = None) -> int:
    """Delete elements by id, or everything drawn by ``by`` (bound labels go with them)."""
    import random

    async with SessionLocal() as session:
        b = await session.get(Whiteboard, board_id, with_for_update=True)
        if b is None:
            raise WhiteboardError("whiteboard not found")
        target = set(ids or [])
        elements = [dict(e) for e in b.elements or []]
        for e in elements:
            if by and (e.get("customData") or {}).get("by") == by:
                target.add(e["id"])
        for e in elements:
            if e.get("containerId") in target:
                target.add(e["id"])
        n = 0
        for e in elements:
            if e.get("id") in target and not e.get("isDeleted"):
                e["isDeleted"] = True
                e["version"] = int(e.get("version") or 0) + 1
                e["versionNonce"] = random.randint(0, 2**31 - 1)
                n += 1
        pending = [p for p in b.pending or []
                   if p.get("id") not in target and not (by and p.get("customData", {}).get("by") == by)]
        n += len(b.pending or []) - len(pending)
        if n:
            b.elements, b.pending = elements, pending
            b.version += 1
        await session.commit()
    if n:
        await bus.publish("whiteboard.updated", to_dict(b), org_id=b.org_id)
    return n
