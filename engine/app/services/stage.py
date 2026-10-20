"""The Stage: pages agents show the user (lessons, 3D scenes, visual explanations).

A page is either a *scene* (a JavaScript module using the bundled Stage
library, a manim-style layer over three.js with KaTeX) or raw *html*. Either can import
the library packs its author may use (app/packs.py). Pages
are served from the backend inside a CSP sandbox (an opaque origin), so their
scripts can never act as the user; the only channel back is
``pantheon.send(...)``, which the Pantheon UI forwards to the page's agent as
a message.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select, update

from app import packs
from app.core.db import SessionLocal
from app.events import bus
from app.models import Agent, Org, StagePage

LIB_DIR = Path(__file__).resolve().parent.parent / "stage" / "lib"
LIB_URL = "/api/v1/stage-lib"
MAX_SOURCE = 400_000
ARCHIVE = "archive"


class StageError(ValueError):
    pass


def to_dict(p: StagePage, *, source: bool = False) -> dict[str, Any]:
    out = {"id": p.id, "orgId": p.org_id, "agentId": p.agent_id, "title": p.title,
           "kind": p.kind, "version": p.version, "folder": p.folder or "",
           "createdAt": p.created_at.isoformat() if p.created_at else None,
           "updatedAt": p.updated_at.isoformat() if p.updated_at else None}
    if source:
        out["source"] = p.source
    return out


def lib_file(path: str) -> Path | None:
    """A Stage library file: the Stage's own (stage.js, art.js, fonts, icons, people), or
    one from a library pack, at <library id>/<file> (app/packs.py)."""
    target = (LIB_DIR / path).resolve()
    if LIB_DIR.resolve() in target.parents and target.is_file():
        return target
    lib_id, _, rel = path.partition("/")
    return packs.library_file(lib_id, rel) if rel else None


async def libraries_for(org_id: str, agent_id: str | None) -> list[packs.Library]:
    """The libraries an agent's pages may import: its own choice, else its org's."""
    async with SessionLocal() as session:
        org = await session.get(Org, org_id)
        agent = await session.get(Agent, agent_id) if agent_id else None
    return packs.chosen(packs.selection_for(org.settings if org else None,
                                            agent.libraries if agent else None))


def _import_map(libs: list[packs.Library]) -> str:
    return json.dumps({"imports": {
        "stage": f"{LIB_URL}/stage.js",
        "stage/art": f"{LIB_URL}/art.js",
        # Library packs, served from here so pages work offline and package as-is.
        **packs.import_map(libs, LIB_URL),
    }})


def _head(title: str, libs: list[packs.Library]) -> str:
    return (f'<meta charset="utf-8"><meta name="viewport" content="width=device-width, '
            f'initial-scale=1"><title>{html_lib.escape(title)}</title>'
            # Inter, Fraunces and Space Grotesk; a face downloads only when text uses it.
            f'<link rel="stylesheet" href="{LIB_URL}/fonts/fonts.css">'
            f'<script type="importmap">{_import_map(libs)}</script>'
            # Every Stage export is a global, so pages can use Stage, create, THREE… directly.
            '<script type="module">import * as S from "stage"; Object.assign(globalThis, S);'
            "</script>")


def _script_safe(code: str) -> str:
    return re.sub(r"</(script)", r"<\\/\1", code, flags=re.IGNORECASE)


def render(p: StagePage, libs: list[packs.Library]) -> str:
    """The page as a document, with an import map of the libraries it may use
    (libraries_for its author)."""
    head = _head(p.title, libs)
    if p.kind == "html":
        doc = p.source
        if re.search(r"<head[^>]*>", doc, re.IGNORECASE):
            return re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + head, doc, count=1,
                          flags=re.IGNORECASE)
        return f"<!doctype html><html><head>{head}</head><body>{doc}</body></html>"
    return (f"<!doctype html><html><head>{head}</head><body>"
            f'<script type="module">\n{_script_safe(p.source)}\n</script></body></html>')


def title_key(title: str) -> str:
    return " ".join(re.sub(r"[\W_]+", " ", title.casefold()).split())


async def save(org_id: str, agent_id: str | None, title: str, *, code: str = "",
               html: str = "", page_id: str | None = None, folder: str = "") -> StagePage:
    if bool(code.strip()) == bool(html.strip()):
        raise StageError("give either code (a Stage scene) or html, not both")
    source = code or html
    if len(source) > MAX_SOURCE:
        raise StageError(f"the page is too large ({len(source)} characters)")
    kind = "scene" if code else "html"
    async with SessionLocal() as session:
        page = None
        if page_id:
            page = await session.get(StagePage, page_id)
            if page is None or page.org_id != org_id:
                raise StageError(f"no stage page {page_id}")
        elif title.strip():
            # The org's page with this title, whoever made it (an editor restyling a lesson
            # updates it instead of adding a copy); one's own first, then the newest. The
            # page keeps its author, who answers learners' questions on it. Archived pages
            # stay as they were.
            # Titles match loosely (case, dashes, dots and spacing), so a re-show with
            # "Schedule - Studio" for "Schedule — Studio" updates the page instead of
            # adding a near-copy. A page saved straight into the archive matches there.
            want = title_key(title)
            in_folder = StagePage.folder == folder if folder else StagePage.folder != ARCHIVE
            page = next((p for p in (await session.execute(select(StagePage).where(
                StagePage.org_id == org_id, in_folder).order_by(
                (StagePage.agent_id == agent_id).desc(), StagePage.updated_at.desc())
            )).scalars() if title_key(p.title) == want), None)
        if page is None:
            page = StagePage(org_id=org_id, agent_id=agent_id, title=title.strip() or "Untitled",
                             kind=kind, source=source, folder=folder)
            session.add(page)
        elif page.kind == kind and page.source == source:
            return page  # the same page again (every check of an unchanged file): no new version
        else:
            page.title = title.strip() or page.title
            page.kind, page.source = kind, source
            page.version = (page.version or 1) + 1
        await session.commit()
    await bus.publish("stage.updated", to_dict(page), org_id=org_id, agent_id=agent_id)
    return page


async def for_org(org_id: str) -> list[StagePage]:
    async with SessionLocal() as session:
        return list((await session.execute(select(StagePage).where(
            StagePage.org_id == org_id).order_by(StagePage.updated_at.desc()))).scalars())


async def move(page_id: str, folder: str) -> StagePage:
    """Put a page in the archive ("archive") or back on the Stage list ("")."""
    if folder not in ("", ARCHIVE):
        raise StageError(f"no folder '{folder}' (use '{ARCHIVE}' or '')")
    async with SessionLocal() as session:
        # Moving isn't an edit: the page keeps its updated time and place in the order.
        await session.execute(update(StagePage).where(StagePage.id == page_id).values(
            folder=folder, updated_at=StagePage.updated_at))
        await session.commit()
        p = await session.get(StagePage, page_id)
        if p is None:
            raise StageError("page not found")
    await bus.publish("stage.updated", {**to_dict(p), "moved": True}, org_id=p.org_id,
                      agent_id=p.agent_id)
    return p


async def get(page_id: str) -> StagePage | None:
    async with SessionLocal() as session:
        return await session.get(StagePage, page_id)


async def delete(page_id: str) -> None:
    async with SessionLocal() as session:
        p = await session.get(StagePage, page_id)
        if p is None:
            raise StageError("page not found")
        await session.delete(p)
        await session.commit()
    await bus.publish("stage.deleted", {"id": page_id}, org_id=p.org_id)


# --- teach-along: what the user is looking at right now --------------------------------

ATTENTION_TTL = 15 * 60
_attention: dict[str, dict[str, Any]] = {}


def set_attention(agent_id: str, view: dict[str, Any]) -> None:
    """The user is watching one of this agent's things on the Stage.

    ``view`` is one of:
      {"kind": "page", "pageId", "title", "text", "held"}
      {"kind": "browser", "title", "url"}
      {"kind": "whiteboard", "title"}
    """
    import time

    _attention[agent_id] = {**view, "at": time.monotonic()}


def clear_attention(agent_id: str) -> None:
    _attention.pop(agent_id, None)


def attention(agent_id: str) -> dict[str, Any] | None:
    import time

    a = _attention.get(agent_id)
    if a and time.monotonic() - a["at"] < ATTENTION_TTL:
        return a
    return None


def attention_prompt(agent_id: str) -> str:
    a = attention(agent_id)
    if a is None:
        return ""
    kind = a.get("kind", "page")
    if kind == "browser":
        where = a.get("title") or a.get("url") or "a page"
        text = f"# The user is with you on the Stage\nThey're watching your browser ({where})."
        if a.get("url"):
            text += f" The URL is {a['url']}."
    elif kind == "whiteboard":
        text = (f"# The user is with you on the Stage\nThey're looking at the whiteboard "
                f"'{a.get('title') or 'the board'}' with you; you can add to it with the "
                "whiteboard tools.")
    elif kind in ("sheet", "pdf", "notebook", "markdown"):
        what = {"sheet": "spreadsheet", "pdf": "PDF", "notebook": "notebook",
                "markdown": "Markdown document"}[kind]
        tools = {"sheet": "sheet_read and sheet_write", "pdf": "pdf_read and pdf_show",
                 "notebook": "notebook_read, notebook_add_cell and notebook_run",
                 "markdown": "md_read and md_write"}[kind]
        text = (f"# The user is with you on the Stage\nThey have the {what} "
                f"'{a.get('title')}' ({a.get('docId')}) open")
        text += f": {a['text']}." if a.get("text") else "."
        text += f" Work on it with {tools}; they see your changes live."
    else:
        text = (f"# The user is with you on the Stage\nThey have your page '{a['title']}' "
                f"(page {a['pageId']}) open. Where the lesson is: \"{a['text']}\".")
        if a.get("held"):
            text += (" The lesson is PAUSED because they're asking you something right now: answer "
                     "briefly and conversationally (your reply may be read aloud), check they're "
                     "happy to continue, and the lesson resumes by itself once you've answered. "
                     "If it would help, update the scene with stage_show.")
    return text
