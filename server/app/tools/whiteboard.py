"""Whiteboard tools: agents read and draw on the org's shared Excalidraw boards."""

from __future__ import annotations

from app.services import whiteboards as wb
from app.tools.base import B, S, ToolContext, ToolError, arr, obj, tool

SHAPE = {"type": "object", "additionalProperties": False, "required": ["type"], "properties": {
    "type": {"type": "string", "enum": list(wb.SHAPES)},
    "id": {"type": "string", "description": "optional, to connect arrows to it later"},
    "label": {"type": "string", "description": "text inside a box/ellipse/diamond, or on an arrow"},
    "text": {"type": "string", "description": "for type text"},
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
       "Give shapes: boxes/ellipses/diamonds with a label (placed in a column to the right of "
       "what's there unless you give x, y), text, and arrows between shapes (from/to = a shape "
       "id or label, including shapes in the same call). Example: [{type: 'rectangle', id: 'api', "
       "label: 'API'}, {type: 'rectangle', id: 'db', label: 'Database', fill: '#a5d8ff'}, "
       "{type: 'arrow', from: 'api', to: 'db', label: 'SQL'}].",
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


@wtool("whiteboard_new", "Start a new whiteboard.", obj({"title": S}, ["title"]))
async def whiteboard_new(args: dict, ctx: ToolContext) -> str:
    b = await wb.create(ctx.org.id, args["title"], by=ctx.agent.name)
    return f"Created whiteboard {b.id} '{b.title}'."
