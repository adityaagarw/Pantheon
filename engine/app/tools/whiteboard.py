"""Whiteboard tools: agents read and draw on the org's shared Excalidraw boards."""

from __future__ import annotations

from app.services import wbgrid, wbrender
from app.services import whiteboards as wb
from app.tools.base import B, S, ToolContext, ToolError, ToolOutput, arr, obj, tool

SHAPE = {"type": "object", "additionalProperties": False, "required": ["type"], "properties": {
    "type": {"type": "string", "enum": list(wb.SHAPES)},
    "id": {"type": "string", "description": "optional, to connect arrows to it later"},
    "label": {"type": "string", "description": "text inside a box/ellipse/diamond, or on an arrow"},
    "text": {"type": "string", "description": "for type text"},
    "cell": {"type": "string", "description": "where it goes on the grid: a cell like 'B3', or a "
             "range like 'B3:D4' for something bigger. Easier and neater than x, y."},
    "near": {"type": "string", "description": "put it next to this shape (id or label)..."},
    "side": {"type": "string", "enum": list(wbgrid.SIDES), "description": "...on this side (default right)"},
    "x": {"type": "number"}, "y": {"type": "number"},
    "width": {"type": "number"}, "height": {"type": "number"},
    "from": {"type": "string", "description": "arrow/line start: a shape id or its label"},
    "to": {"type": "string", "description": "arrow/line end: a shape id or its label"},
    "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
    "color": {"type": "string", "description": "stroke color, e.g. #1971c2"},
    "fill": {"type": "string", "description": "fill color, e.g. #a5d8ff"},
    "size": {"type": "number", "description": "font size for text"},
}}


def wtool(name: str, description: str, params: dict):
    return tool(name, description, params, category="whiteboard")


def _err(e: Exception) -> ToolError:
    return ToolError(str(e))


@wtool("whiteboard_list", "The organization's whiteboards.", obj({}))
async def whiteboard_list(args: dict, ctx: ToolContext) -> str:
    boards = await wb.for_org(ctx.org.id)
    return "\n".join(f"- {b.id} '{b.title}': {wb.to_dict(b)['elementCount']} elements"
                     for b in boards)


@wtool("whiteboard_read", "Read a whiteboard as text: its boxes, labels, arrows (what connects "
       "to what), text and who drew what. board: id or title (default: the first board).",
       obj({"board": S}))
async def whiteboard_read(args: dict, ctx: ToolContext) -> str:
    try:
        b = await wb.resolve(ctx.org.id, args.get("board"))
    except wb.WhiteboardError as e:
        raise _err(e) from None
    return wb.describe(b)


@wtool("whiteboard_draw", "Draw on a shared whiteboard (Excalidraw) that the user sees live. "
       "The board has a grid of cells (columns A, B, C... rows 1, 2, 3...; see whiteboard_view). "
       "Place each box/ellipse/diamond with `cell` ('B2', or 'B2:C3' for a bigger one) or next to "
       "another shape (`near` + `side`); boxes you don't place are laid out for you in the first "
       "free columns, following the arrows. Arrows and lines connect shapes (from/to = id or "
       "label, including shapes in the same call). Avoid raw x, y. Example: [{type: 'rectangle', "
       "id: 'api', label: 'API', cell: 'B2'}, {type: 'rectangle', id: 'db', label: 'Database', "
       "near: 'api', side: 'right', fill: '#a5d8ff'}, {type: 'arrow', from: 'api', to: 'db', "
       "label: 'SQL'}]. For whole diagrams use whiteboard_diagram.",
       obj({"shapes": arr(SHAPE), "board": S}, ["shapes"]))
async def whiteboard_draw(args: dict, ctx: ToolContext) -> str:
    try:
        b = await wb.resolve(ctx.org.id, args.get("board"))
        drawn = await wb.add_shapes(b.id, args["shapes"], ctx.agent.name)
    except wb.WhiteboardError as e:
        raise _err(e) from None
    return (f"Drew {len(drawn)} shape(s) on '{b.title}': "
            + ", ".join(f"{s['type']} {s['id']}" for s in drawn)
            + ". They appear as soon as the board is open (whiteboard_read shows them).")


@wtool("whiteboard_erase", "Erase from a whiteboard: specific element ids, or everything you drew "
       "(mine=true).", obj({"ids": arr(S), "mine": B, "board": S}))
async def whiteboard_erase(args: dict, ctx: ToolContext) -> str:
    if not args.get("ids") and not args.get("mine"):
        raise ToolError("give ids or mine=true")
    try:
        b = await wb.resolve(ctx.org.id, args.get("board"))
        n = await wb.erase(b.id, ids=args.get("ids"),
                           by=ctx.agent.name if args.get("mine") else None)
    except wb.WhiteboardError as e:
        raise _err(e) from None
    return f"Erased {n} element(s) from '{b.title}'."


@wtool("whiteboard_view", "Look at a whiteboard: a picture of it with the cell grid on top "
       "(columns A, B, C... across, rows 1, 2, 3... down), to check how your drawing looks "
       "before and after you change it. board: id or title.", obj({"board": S, "grid": B}))
async def whiteboard_view(args: dict, ctx: ToolContext) -> ToolOutput:
    try:
        b = await wb.resolve(ctx.org.id, args.get("board"))
    except wb.WhiteboardError as e:
        raise _err(e) from None
    png = wbrender.render(wb._live(b), list(b.pending or []), grid=args.get("grid", True) is not False)
    return ToolOutput(f"Whiteboard '{b.title}' (picture attached; if you can't see images, use "
                      "whiteboard_read).\n\n" + wb.describe(b), [wbrender.data_url(png)])


NODE = {"type": "object", "properties": {
    "id": S, "label": S, "fill": S, "color": S,
    "shape": {"type": "string", "enum": ["rectangle", "ellipse", "diamond"]}}}


@wtool("whiteboard_diagram", "Draw a whole diagram, laid out neatly on the grid for you: "
       "flowchart (nodes + edges, direction right or down), mindmap (center + branches with "
       "children), table (columns + rows), timeline (events with when/label) or list (title + "
       "items). `at` is the top-left cell (default: the first free columns on the right).",
       obj({"kind": {"type": "string", "enum": list(wbgrid.KINDS)}, "board": S, "at": S,
            "nodes": arr(NODE),
            "edges": arr({"type": "object", "properties": {"from": S, "to": S, "label": S}}),
            "direction": {"type": "string", "enum": ["right", "down"]},
            "center": S,
            "branches": arr({"type": "object", "properties": {
                "label": S, "fill": S, "children": arr(S)}}),
            "columns": arr(S), "rows": arr(arr(S)),
            "events": arr({"type": "object", "properties": {"when": S, "label": S, "fill": S}}),
            "title": S, "items": arr(S)}, ["kind"]))
async def whiteboard_diagram(args: dict, ctx: ToolContext) -> str:
    try:
        b = await wb.resolve(ctx.org.id, args.get("board"))
        shapes = wbgrid.diagram(args["kind"], args, wb.free_cells(b))
        drawn = await wb.add_shapes(b.id, shapes, ctx.agent.name)
    except (wb.WhiteboardError, wbgrid.GridError) as e:
        raise _err(e) from None
    cells = [wbgrid.range_of(s["x"], s["y"], abs(s.get("width") or 0), abs(s.get("height") or 0))
             for s in drawn if s.get("type") in wb.DEFAULT_SIZE]
    where = f" in {cells[0]}..{cells[-1]}" if cells else ""
    return (f"Drew a {args['kind']} on '{b.title}'{where} ({len(drawn)} shapes). "
            "whiteboard_view shows how it looks.")


@wtool("whiteboard_new", "Start a new whiteboard.", obj({"title": S}, ["title"]))
async def whiteboard_new(args: dict, ctx: ToolContext) -> str:
    b = await wb.create(ctx.org.id, args["title"], by=ctx.agent.name)
    return f"Created whiteboard {b.id} '{b.title}'."
