"""Files given to an agent: list, read, search and look at them."""

from __future__ import annotations

from app.services import attachments as att
from app.tools.base import I, S, ToolContext, ToolError, ToolOutput, obj, tool

AMBIENT = ("attachment_list", "attachment_read", "attachment_search", "attachment_view")


def ftool(name: str, description: str, params: dict):
    return tool(name, description, params, category="files")


async def _find(ctx: ToolContext, ref: str) -> att.Attachment:
    ref = ref.strip()
    files = await att.visible(ctx.org.id, ctx.agent.id, limit=500)
    for a in files:
        if a.id == ref:
            return a
    named = [a for a in files if a.name.lower() == ref.lower()]
    if len(named) == 1:
        return named[0]
    raise ToolError(f"no file '{ref}' (attachment_list shows your files)")


def _ready(a: att.Attachment) -> None:
    if a.status == "processing":
        raise ToolError(f"'{a.name}' is still being processed; try again in a moment")
    if a.status == "failed":
        raise ToolError(f"'{a.name}' couldn't be read: {a.error}")


@ftool("attachment_list", "The files you've been given: uploaded documents and images (your own "
       "library plus files shared with the organization).", obj({}))
async def attachment_list(args: dict, ctx: ToolContext) -> str:
    files = await att.visible(ctx.org.id, ctx.agent.id)
    if not files:
        return "No files yet."
    lines = []
    for a in files:
        info = a.info or {}
        detail = ", ".join(x for x in (
            f"{info['pages']} pages" if info.get("pages") else "",
            f"{info['chars']:,} characters" if info.get("chars") else "",
            f"{info['width']}x{info['height']}" if info.get("width") else "",
            a.status if a.status != "ready" else "") if x)
        lines.append(f"- {a.id} '{a.name}' [{a.kind}, {att.human_size(a.size)}"
                     f"{', ' + detail if detail else ''}]"
                     f"{' (shared with the org)' if a.agent_id is None else ''}"
                     f"{' — ' + info['note'] if info.get('note') else ''}")
    return "\n".join(lines)


@ftool("attachment_read", "Read a document you were given (by id or exact name): PDF, Word, "
       "Excel, text, code. Returns a slice of its text; continue with a later offset for long "
       "files.", obj({"file": S, "offset": I, "length": I}, ["file"]))
async def attachment_read(args: dict, ctx: ToolContext) -> str:
    a = await _find(ctx, args["file"])
    if a.kind == "image":
        raise ToolError(f"'{a.name}' is an image: use attachment_view")
    if a.kind != "document":
        raise ToolError(f"'{a.name}' is not a readable document")
    _ready(a)
    offset = int(args.get("offset") or 0)
    text, total = att.read_text(a, offset, int(args.get("length") or 6000))
    if not total:
        return f"'{a.name}' has no readable text." + (
            f" {a.info['note']}" if (a.info or {}).get("note") else "")
    end = offset + len(text)
    more = f"\n[… continues: call again with offset={end}]" if end < total else "\n[end of file]"
    return f"{a.name} (characters {offset}-{end} of {total}):\n{text}{more}"


@ftool("attachment_search", "Search your files (all documents you were given) for passages "
       "about something; matches by meaning and by exact words.",
       obj({"query": S, "limit": I}, ["query"]))
async def attachment_search(args: dict, ctx: ToolContext) -> str:
    hits = await att.search(ctx.org.id, ctx.agent.id, args["query"],
                            k=max(1, min(int(args.get("limit") or 5), 12)))
    if not hits:
        return "Nothing relevant in your files."
    return "\n\n".join(f"[{name} @ char {c.start}]\n{c.content}" for c, name, _ in hits)


@ftool("attachment_view", "Look at an image you were given (by id or exact name).",
       obj({"file": S}, ["file"]))
async def attachment_view(args: dict, ctx: ToolContext) -> ToolOutput:
    a = await _find(ctx, args["file"])
    if a.kind != "image":
        raise ToolError(f"'{a.name}' is not an image: use attachment_read")
    _ready(a)
    info = a.info or {}
    return ToolOutput(f"{a.name} ({info.get('width')}x{info.get('height')}); the image is "
                      "attached.", [att.image_data_url(a)])
