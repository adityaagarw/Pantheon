"""The Stage: pages agents show the user (lessons, 3D scenes, visual explanations).

A page is either a *scene* (a JavaScript module using the bundled Stage
library, a manim-style layer over three.js with KaTeX) or raw *html*. Pages
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

from sqlalchemy import select

from app.core.db import SessionLocal
from app.events import bus
from app.models import StagePage

HERE = Path(__file__).resolve().parent.parent / "stage"
LIB_DIR = HERE / "lib"
VENDOR_DIR = HERE / "vendor"
LIB_URL = "/api/v1/stage-lib"
MAX_SOURCE = 400_000


class StageError(ValueError):
    pass


def to_dict(p: StagePage, *, source: bool = False) -> dict[str, Any]:
    out = {"id": p.id, "orgId": p.org_id, "agentId": p.agent_id, "title": p.title,
           "kind": p.kind, "version": p.version,
           "createdAt": p.created_at.isoformat() if p.created_at else None,
           "updatedAt": p.updated_at.isoformat() if p.updated_at else None}
    if source:
        out["source"] = p.source
    return out


def lib_file(path: str) -> Path | None:
    """A Stage library file (our stage.js, or vendored three.js / KaTeX)."""
    for root in (LIB_DIR, VENDOR_DIR):
        target = (root / path).resolve()
        if root.resolve() in target.parents and target.is_file():
            return target
    return None


def _import_map() -> str:
    return json.dumps({"imports": {
        "three": f"{LIB_URL}/three/three.module.js",
        "three/addons/": f"{LIB_URL}/three/addons/",
        "katex": f"{LIB_URL}/katex/katex.mjs",
        "stage": f"{LIB_URL}/stage.js",
    }})


def _head(title: str) -> str:
    return (f'<meta charset="utf-8"><meta name="viewport" content="width=device-width, '
            f'initial-scale=1"><title>{html_lib.escape(title)}</title>'
            f'<script type="importmap">{_import_map()}</script>'
            # Every Stage export is a global, so pages can use Stage, create, THREE… directly.
            '<script type="module">import * as S from "stage"; Object.assign(globalThis, S);'
            "</script>")


def _script_safe(code: str) -> str:
    return re.sub(r"</(script)", r"<\\/\1", code, flags=re.IGNORECASE)


def render(p: StagePage) -> str:
    if p.kind == "html":
        doc = p.source
        if re.search(r"<head[^>]*>", doc, re.IGNORECASE):
            return re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + _head(p.title), doc, count=1,
                          flags=re.IGNORECASE)
        return f"<!doctype html><html><head>{_head(p.title)}</head><body>{doc}</body></html>"
    return (f"<!doctype html><html><head>{_head(p.title)}</head><body>"
            f'<script type="module">\n{_script_safe(p.source)}\n</script></body></html>')


async def save(org_id: str, agent_id: str | None, title: str, *, code: str = "",
               html: str = "", page_id: str | None = None) -> StagePage:
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
            page = (await session.execute(select(StagePage).where(
                StagePage.org_id == org_id, StagePage.agent_id == agent_id,
                StagePage.title == title.strip()))).scalars().first()
        if page is None:
            page = StagePage(org_id=org_id, agent_id=agent_id, title=title.strip() or "Untitled",
                             kind=kind, source=source)
            session.add(page)
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
    else:
        text = (f"# The user is with you on the Stage\nThey have your page '{a['title']}' "
                f"(page {a['pageId']}) open. Where the lesson is: \"{a['text']}\".")
        if a.get("held"):
            text += (" The lesson is PAUSED because they're asking you something right now: answer "
                     "briefly and conversationally (your reply may be read aloud), check they're "
                     "happy to continue, and the lesson resumes by itself once you've answered. "
                     "If it would help, update the scene with stage_show.")
    return text
