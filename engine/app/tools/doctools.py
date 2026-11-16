"""Stage documents for agents: spreadsheets, PDFs and notebooks the user sees live."""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import func, select

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import Attachment
from app.services import attachments as att
from app.services import stagedocs as docs
from app.services.stagedocs import DocError
from app.tools.base import (
    B,
    I,
    S,
    ToolContext,
    ToolError,
    ToolOutput,
    arr,
    obj,
    resolve_in_workspace,
    tool,
)

CELL = {"type": "object", "additionalProperties": False, "required": ["cell"], "properties": {
    "cell": {"type": "string", "description": "e.g. B2"},
    "value": {"description": "a number or text; empty to clear"},
    "formula": {"type": "string", "description": "e.g. =SUM(B2:B9)"},
    "sheet": {"type": "string"},
}}
ANY = {}


def dtool(name: str, description: str, params: dict, **kw: Any):
    return tool(name, description, params, category="stage", **kw)


async def _doc(ctx: ToolContext, ref: str, kind: str):
    try:
        return await docs.resolve(ctx.org.id, ref, kind)
    except DocError as e:
        raise ToolError(str(e)) from None


async def _file(ctx: ToolContext, ref: str) -> Attachment:
    """A file from the org's Files by id or name."""
    a = await att.get(ref)
    if a is None or a.org_id != ctx.org.id:
        async with SessionLocal() as session:
            a = (await session.execute(select(Attachment).where(
                Attachment.org_id == ctx.org.id,
                func.lower(Attachment.name) == ref.strip().lower()))).scalars().first()
    if a is None:
        raise ToolError(f"no file '{ref}' in Files")
    return a


def _err(e: Exception) -> ToolError:
    return ToolError(str(e))


KINDS = {"sheet": "spreadsheet", "pdf": "PDF", "notebook": "notebook",
         "markdown": "Markdown document", "word": "Word document"}


def _passage(a: dict[str, Any]) -> str:
    """The passage the user highlighted, or "" when they have highlighted nothing.

    Both editors wrap a real selection in triple quotes; what they say otherwise ("reading it",
    "editing line 12") is where they are, not what they mean. A long selection reaches us cut
    short with an ellipsis."""
    text = str(a.get("text") or "")
    _, _, rest = text.partition('"""')
    if rest == text:  # no quotes at all: nothing is selected
        return ""
    return rest.partition('"""')[0].strip().rstrip("…").strip()


async def _selected(ctx: ToolContext, kind: str, ref: str) -> str:
    """What the user has highlighted in the document they have open, and where it is.

    The Stage keeps one attention per agent (services/stage.py), refreshed while the document
    stays open, so an agent can ask mid-turn what "this" or "here" in a message means."""
    from app.services import stage, worddocs

    unit = "paragraph" if kind == "word" else "line"
    reader = "word_read" if kind == "word" else "md_read"
    a = stage.attention(ctx.agent.id) or {}
    asked = ref or str(a.get("docId") or "")
    if not asked:
        return ("No selection: the user has no document open with you on the Stage. Ask them to "
                "open one and highlight the passage.")
    title, doc_id = str(a.get("title") or ""), str(a.get("docId") or "")
    if not ref and a.get("kind") not in (None, kind):  # they highlighted in another kind of file
        shown = _passage(a)
        back = (" For their Markdown document use md_read with selection: true."
                if a.get("kind") == "markdown" else "")
        return (f"No selection: the user has the {KINDS.get(str(a.get('kind')), a.get('kind'))} "
                f"'{title}' ({doc_id}) open, not a {KINDS[kind]}."
                + (f" What they highlighted there:\n\"\"\"\n{shown}\n\"\"\"" if shown else "") + back)
    d = await _doc(ctx, asked, kind)
    if a.get("kind") != kind or doc_id != d.id:
        at = (f"they have the {KINDS.get(str(a.get('kind')), a.get('kind'))} '{title}' "
              f"({doc_id}) open instead" if doc_id else "no document is open on the Stage")
        return f"No selection in '{d.title}' ({d.id}): {at}."
    passage = _passage(a)
    if not passage:
        return (f"No selection: the user has '{d.title}' ({d.id}) open but has not highlighted "
                f"anything ({a.get('text') or 'they are just reading it'}).")
    shown = f'"""\n{passage}\n"""'
    found = (worddocs.where_is(d, passage) if kind == "word"
             else docs.where_is_markdown(d, passage))
    if not found:
        return (f"'{d.title}' ({d.id}): the user has this selected, but it is no longer in the "
                f"document, so it has no {unit} numbers (they may have edited it since):\n{shown}")
    first, last = found
    where = f"{unit} {first}" if first == last else f"{unit}s {first}-{last}"
    return (f"'{d.title}' ({d.id}): the user has {where} selected, counted the way {reader} "
            f"counts them — {where} is what \"this\" or \"here\" means:\n{shown}")


@dtool("word_selection", "What the user has highlighted in the Word document they have open: "
       "the passage and the paragraph numbers it spans, counted the way word_read counts them, "
       "so \"this\" or \"here\" in their message means this. doc: one particular document; "
       "leave it out for whichever one they are pointing at. Says \"no selection\" when they "
       "have highlighted nothing.", obj({"doc": S}))
async def word_selection(args: dict, ctx: ToolContext) -> str:
    return await _selected(ctx, "word", str(args.get("doc") or ""))


@dtool("doc_list", "The spreadsheets, PDFs, notebooks, Markdown and Word documents on this "
       "organization's Stage.", obj({}))
async def doc_list(args: dict, ctx: ToolContext) -> str:
    found = await docs.for_org(ctx.org.id)
    if not found:
        return "No documents on the Stage yet."
    return "\n".join(f"- {d.kind} '{d.title}' ({d.id}): {docs.summary(d)}" for d in found)


# --- spreadsheets ---------------------------------------------------------------------------


@dtool("sheet_new", "Create a spreadsheet on the Stage (the user sees it live). from_file: an "
       ".xlsx or .csv from Files (id or name) to start from.",
       obj({"title": S, "from_file": S}, ["title"]))
async def sheet_new(args: dict, ctx: ToolContext) -> str:
    data = None
    if args.get("from_file"):
        a = await _file(ctx, args["from_file"])
        try:
            data = {"sheets": docs.sheets_from_file(a.name, att.file_path(a).read_bytes())}
        except DocError as e:
            raise _err(e) from None
    d = await docs.create(ctx.org.id, "sheet", args["title"], data, created_by=ctx.agent.id,
                          by_name=ctx.agent.name)
    return f"Created spreadsheet '{d.title}' ({d.id}): {docs.summary(d)}."


@dtool("sheet_read", "Read a spreadsheet as a table with its formulas and their values. "
       "range: e.g. A1:F40 (default: everything used, up to 200 rows).",
       obj({"doc": S, "sheet": S, "range": S}, ["doc"]))
async def sheet_read(args: dict, ctx: ToolContext) -> str:
    d = await _doc(ctx, args["doc"], "sheet")
    try:
        return docs.read_sheet(d, args.get("sheet"), args.get("range"))
    except (DocError, ValueError) as e:
        raise _err(e) from None


@dtool("sheet_write", "Write to a spreadsheet the user sees live. Either cells: [{cell: 'B2', "
       "value: 12}, {cell: 'B5', formula: '=SUM(B2:B4)'}], or a block: start 'A1' and rows "
       "[[...], [...]] (text starting with = is a formula). Empty value clears a cell. "
       "Formulas are calculated (SUM, AVERAGE, IF, VLOOKUP, SUMIF, ROUND and more).",
       obj({"doc": S, "cells": arr(CELL), "start": S, "rows": arr(arr(ANY)), "sheet": S},
           ["doc"]))
async def sheet_write(args: dict, ctx: ToolContext) -> str:
    d = await _doc(ctx, args["doc"], "sheet")
    try:
        if args.get("rows"):
            changes = await docs.write_range(d.id, args.get("start") or "A1", args["rows"],
                                             ctx.agent.name, args.get("sheet"))
        elif args.get("cells"):
            writes = [{**c, "sheet": c.get("sheet") or args.get("sheet")} for c in args["cells"]]
            changes = await docs.write_cells(d.id, writes, ctx.agent.name)
        else:
            raise ToolError("give cells, or start and rows")
    except (DocError, ValueError) as e:
        raise _err(e) from None
    n = sum(len(c) for c in changes.values())
    errors = [f"{ref} {c['v']}" for cells in changes.values() for ref, c in cells.items()
              if c and isinstance(c.get("v"), str) and c["v"].startswith("#")]
    return (f"Wrote to '{d.title}' ({n} cells updated)."
            + (f" Formula errors: {', '.join(errors[:10])}" if errors else ""))


# --- PDFs -----------------------------------------------------------------------------------


@dtool("pdf_open", "Open a PDF on the Stage for the user and you to read together: a file "
       "from Files (id or name), or a url to download (it's saved to Files).",
       obj({"file": S, "url": S, "title": S}))
async def pdf_open(args: dict, ctx: ToolContext) -> str:
    if args.get("file"):
        a = await _file(ctx, args["file"])
    elif args.get("url"):
        a = await _download_pdf(ctx, args["url"])
    else:
        raise ToolError("give file or url")
    try:
        d = await docs.open_pdf(ctx.org.id, a, args.get("title"), created_by=ctx.agent.id,
                                by_name=ctx.agent.name)
    except DocError as e:
        raise _err(e) from None
    pages = (d.data or {}).get("pages")
    return (f"Opened '{d.title}' ({d.id}{f', {pages} pages' if pages else ''}) on the Stage. "
            "Read pages with pdf_read; point the user to something with pdf_show.")


async def _download_pdf(ctx: ToolContext, url: str) -> Attachment:
    if not url.startswith(("http://", "https://")):
        raise ToolError("the url must start with http:// or https://")
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 Pantheon"})
    except httpx.HTTPError as e:
        raise ToolError(f"couldn't download it: {e}") from None
    if r.status_code >= 400:
        raise ToolError(f"couldn't download it: HTTP {r.status_code}")
    if not r.content.startswith(b"%PDF"):
        raise ToolError("that link isn't a PDF")
    if len(r.content) > settings.max_upload_bytes:
        raise ToolError("the PDF is too large")
    name = url.rstrip("/").rsplit("/", 1)[-1].split("?")[0] or "document.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    try:
        return await att.save_upload(ctx.org.id, None, name, r.content, "application/pdf",
                                     uploaded_by=ctx.agent.id)
    except att.AttachmentError as e:
        raise _err(e) from None


@dtool("pdf_read", "Read the text of one page of a PDF on the Stage.",
       obj({"doc": S, "page": I}, ["doc", "page"]))
async def pdf_read(args: dict, ctx: ToolContext) -> str:
    d = await _doc(ctx, args["doc"], "pdf")
    a = await att.get((d.data or {}).get("attachmentId", ""))
    if a is None:
        raise ToolError("the PDF file is gone")
    try:
        text, total = docs.pdf_page_text(str(att.file_path(a)), int(args["page"]))
    except DocError as e:
        raise _err(e) from None
    return f"'{d.title}' page {args['page']} of {total}:\n\n{text or '(no text on this page)'}"


@dtool("pdf_show", "Point the user to something in a PDF on the Stage: turn to a page and/or "
       "highlight a passage (copy the exact words from pdf_read) with an optional note. "
       "clear=true removes earlier highlights.",
       obj({"doc": S, "page": I, "highlight": S, "note": S, "clear": B}, ["doc"]))
async def pdf_show(args: dict, ctx: ToolContext) -> str:
    d = await _doc(ctx, args["doc"], "pdf")
    try:
        d = await docs.show_pdf(d.id, args.get("page"), args.get("highlight"), args.get("note"),
                                ctx.agent.name, clear=bool(args.get("clear")))
    except DocError as e:
        raise _err(e) from None
    return f"Showing page {(d.data or {}).get('page', 1)} of '{d.title}'" + (
        " with your highlight." if args.get("highlight") else ".")


# --- notebooks ------------------------------------------------------------------------------


def _ran(cell: dict[str, Any]) -> ToolOutput:
    outs = cell.get("outputs") or []
    text = "\n".join(docs.output_text(o, 4000) for o in outs) or "(no output)"
    images = [f"data:image/png;base64,{o['data']}" for o in outs if o.get("type") == "image"]
    status = "error" if cell.get("status") == "error" else "ran"
    return ToolOutput(f"Cell {cell.get('count') or ''} {status}:\n{text}", images[:4])


@dtool("notebook_new", "Create a Python notebook on the Stage (code runs in the sandbox "
       "desktop; ~/shared is its working folder). from_file: an .ipynb from Files to open.",
       obj({"title": S, "from_file": S}, ["title"]))
async def notebook_new(args: dict, ctx: ToolContext) -> str:
    data = None
    if args.get("from_file"):
        a = await _file(ctx, args["from_file"])
        try:
            data = docs.notebook_from_ipynb(att.file_path(a).read_bytes())
        except DocError as e:
            raise _err(e) from None
    d = await docs.create(ctx.org.id, "notebook", args["title"], data, created_by=ctx.agent.id,
                          by_name=ctx.agent.name)
    return f"Created notebook '{d.title}' ({d.id}): {docs.summary(d)}."


@dtool("notebook_read", "Read a notebook: every cell (numbered) with its source and outputs.",
       obj({"doc": S}, ["doc"]))
async def notebook_read(args: dict, ctx: ToolContext) -> str:
    return docs.read_notebook(await _doc(ctx, args["doc"], "notebook"))


@dtool("notebook_add_cell", "Add a cell to a notebook (at the end, or after cell number "
       "`after`); run=true runs it and returns its output (charts come back as images).",
       obj({"doc": S, "source": S, "type": {"type": "string", "enum": ["code", "markdown"]},
            "after": I, "run": B}, ["doc", "source"]), timeout=600)
async def notebook_add_cell(args: dict, ctx: ToolContext) -> str | ToolOutput:
    d = await _doc(ctx, args["doc"], "notebook")
    try:
        cell = await docs.add_cell(d.id, args["source"], args.get("type") or "code",
                                   ctx.agent.name, args.get("after"))
        if args.get("run") and cell["type"] == "code":
            return _ran(await docs.run_cell(d.id, cell["id"], ctx.agent.name))
    except DocError as e:
        raise _err(e) from None
    n = len((await docs.get(d.id)).data.get("cells", []))
    return f"Added a {cell['type']} cell to '{d.title}' ({n} cells now)."


@dtool("notebook_edit_cell", "Change a notebook cell (by number): new source and/or type, "
       "delete=true to remove it, run=true to run it after editing.",
       obj({"doc": S, "cell": I, "source": S, "type": {"type": "string",
                                                         "enum": ["code", "markdown"]},
            "delete": B, "run": B}, ["doc", "cell"]), timeout=600)
async def notebook_edit_cell(args: dict, ctx: ToolContext) -> str | ToolOutput:
    d = await _doc(ctx, args["doc"], "notebook")
    try:
        if args.get("delete"):
            await docs.delete_cell(d.id, args["cell"], ctx.agent.name)
            return f"Deleted cell {args['cell']}."
        cell = await docs.edit_cell(d.id, args["cell"], source=args.get("source"),
                                    kind=args.get("type"), by_name=ctx.agent.name)
        if args.get("run"):
            return _ran(await docs.run_cell(d.id, cell["id"], ctx.agent.name))
    except DocError as e:
        raise _err(e) from None
    return f"Updated cell {args['cell']}."


@dtool("notebook_run", "Run a notebook cell (by number) and get its output, or all=true to run "
       "every code cell in order (stops at the first error). Variables persist between cells.",
       obj({"doc": S, "cell": I, "all": B}, ["doc"]), timeout=1800)
async def notebook_run(args: dict, ctx: ToolContext) -> str | ToolOutput:
    d = await _doc(ctx, args["doc"], "notebook")
    try:
        if args.get("all"):
            done = await docs.run_all(d.id, ctx.agent.name)
            if not done:
                return "No code cells to run."
            last = _ran(done[-1])
            return ToolOutput(f"Ran {len(done)} cell(s). Last: {last}", last.images)
        if not args.get("cell"):
            raise ToolError("give a cell number, or all=true")
        return _ran(await docs.run_cell(d.id, args["cell"], ctx.agent.name))
    except DocError as e:
        raise _err(e) from None


# --- Markdown -------------------------------------------------------------------------------


@dtool("md_new", "Create a Markdown document on the Stage (notes, a plan, a report, a spec) "
       "that the user reads and edits with you live. text: its first content; from_file: an "
       ".md or Word (.docx, .doc) file from Files or your workspace to open.",
       obj({"title": S, "text": S, "from_file": S}, ["title"]))
async def md_new(args: dict, ctx: ToolContext) -> str:
    data = {"text": ""}
    if args.get("from_file"):
        try:
            a = await _file(ctx, args["from_file"])
            raw = att.file_path(a).read_bytes()
        except ToolError:
            path = resolve_in_workspace(ctx, args["from_file"])  # e.g. the DESIGN.md just written
            if not path.is_file():
                raise ToolError(f"no file '{args['from_file']}' in Files or your workspace") from None
            raw = path.read_bytes()
        try:
            data = docs.markdown_from_file(raw)
        except DocError as e:
            raise _err(e) from None
    d = await docs.create(ctx.org.id, "markdown", args["title"], data, created_by=ctx.agent.id,
                          by_name=ctx.agent.name)
    if args.get("text"):
        try:
            d = await docs.edit_text(d.id, ctx.agent.name, append=args["text"])
        except DocError as e:
            raise _err(e) from None
    return f"Created Markdown document '{d.title}' ({d.id}): {docs.summary(d)}."


@dtool("md_read", "Read a Markdown document on the Stage (the user may have edited it). "
       "start: the first line to read (for long documents). selection: true for just the passage "
       "the user has highlighted, with the lines it spans.",
       obj({"doc": S, "start": I, "selection": B}, ["doc"]))
async def md_read(args: dict, ctx: ToolContext) -> str:
    if args.get("selection"):
        return await _selected(ctx, "markdown", args["doc"])
    return docs.read_markdown(await _doc(ctx, args["doc"], "markdown"), int(args.get("start") or 1))


@dtool("md_write", "Change a Markdown document the user sees live: find (an exact passage from "
       "md_read) and replace it; or append text at the end; or text to replace everything.",
       obj({"doc": S, "find": S, "replace": S, "append": S, "text": S}, ["doc"]))
async def md_write(args: dict, ctx: ToolContext) -> str:
    d = await _doc(ctx, args["doc"], "markdown")
    try:
        if args.get("text") is not None and not args.get("find") and not args.get("append"):
            d = await docs.set_text(d.id, args["text"], ctx.agent.name)
        elif args.get("find") or args.get("append"):
            d = await docs.edit_text(d.id, ctx.agent.name, find=args.get("find"),
                                     replace=args.get("replace") or "", append=args.get("append"))
        else:
            raise ToolError("give find and replace, append, or text")
    except DocError as e:
        raise _err(e) from None
    return f"Updated '{d.title}' ({docs.summary(d)})."


# --- Word -----------------------------------------------------------------------------------


@dtool("word_new", "Create a Word document (.docx) on the Stage that the user reads and edits "
       "with you live, in a page editor like Word's. from_file: a .docx or .doc from Files or "
       "your workspace to open (formatting kept); text: its first paragraphs ('# ' headings, "
       "'- ' bullets).",
       obj({"title": S, "from_file": S, "text": S}, ["title"]))
async def word_new(args: dict, ctx: ToolContext) -> str:
    from app.services import worddocs

    raw, name = None, ""
    if args.get("from_file"):
        try:
            a = await _file(ctx, args["from_file"])
            raw, name = att.file_path(a).read_bytes(), a.name
        except ToolError:
            path = resolve_in_workspace(ctx, args["from_file"])
            if not path.is_file():
                raise ToolError(f"no file '{args['from_file']}' in Files or your workspace") from None
            raw, name = path.read_bytes(), path.name
    try:
        d = await worddocs.create(ctx.org.id, args["title"], raw, name=name,
                                  created_by=ctx.agent.id, by_name=ctx.agent.name)
        if args.get("text"):
            d = await worddocs.edit(d.id, ctx.agent.name, append=args["text"])
    except DocError as e:
        raise _err(e) from None
    return f"Created Word document '{d.title}' ({d.id}): {docs.summary(d)}."


@dtool("word_read", "Read a Word document on the Stage (the user may have edited it): numbered "
       "paragraphs with their style, tables, and the open comments people left in it (who, "
       "which paragraphs, the text each is on, replies); a paragraph with a comment ends with "
       "[comment <id>]. start: the first paragraph to read. selection: true for just the "
       "passage the user has highlighted, with its paragraph numbers. comments: true for the "
       "comments alone, with who left them and what they are on.",
       obj({"doc": S, "start": I, "selection": B, "comments": B}, ["doc"]))
async def word_read(args: dict, ctx: ToolContext) -> str:
    from app.services import worddocs

    if args.get("selection"):
        return await _selected(ctx, "word", args["doc"])
    if args.get("comments"):
        try:
            return worddocs.comments_only(await _doc(ctx, args["doc"], "word"))
        except DocError as e:
            raise _err(e) from None
    try:
        return worddocs.read(await _doc(ctx, args["doc"], "word"), int(args.get("start") or 1))
    except DocError as e:
        raise _err(e) from None


@dtool("word_write", "Change a Word document the user sees live, keeping its formatting: find "
       "(exact text within one paragraph, from word_read) and replace it (empty deletes it); or "
       "append paragraphs ('# ' headings, '- ' bullets, one per line) at the end, or after "
       "paragraph number `after`. resolve: a comment's id from word_read, to mark it resolved "
       "once you have done what it asks (alone, or with the edit that does it). reply: a "
       "comment's id with text: what to say back to the person who left it.",
       obj({"doc": S, "find": S, "replace": S, "append": S, "after": I, "resolve": S,
            "reply": S, "text": S}, ["doc"]))
async def word_write(args: dict, ctx: ToolContext) -> str:
    from app.services import worddocs

    d = await _doc(ctx, args["doc"], "word")
    try:
        d = await worddocs.edit(d.id, ctx.agent.name, find=args.get("find"),
                                replace=args.get("replace") or "", append=args.get("append"),
                                after=int(args["after"]) if args.get("after") else None,
                                resolve_comment=str(args.get("resolve") or "") or None,
                                reply_to=str(args.get("reply") or "") or None,
                                reply_text=str(args.get("text") or ""))
    except DocError as e:
        raise _err(e) from None
    done = f"; comment {args['resolve']} is resolved" if args.get("resolve") else ""
    if args.get("reply"):
        done += f", and answered what comment {args['reply']} asked"
    return f"Updated '{d.title}' ({docs.summary(d)}){done}."
