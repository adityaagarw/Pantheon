"""Files given to agents: uploaded documents and images.

A file belongs to one agent's library (``agent_id``) or is shared with the whole
organization (``agent_id`` NULL). Uploads are stored on disk under
``data/uploads/<org>/<id>/``; the database keeps the metadata and, for
documents, searchable chunks.

* Documents (PDF, Word, Excel, text/code/CSV/Markdown/JSON...) are converted to
  text once, chunked and embedded, so agents can read them (``attachment_read``)
  and search them by meaning (``attachment_search``); the passages most relevant
  to a new message are also recalled into the turn automatically.
* Images are validated, kept as uploaded, and shown to vision-capable models
  (``attachment_view``, or inline when attached to a message).
* Anything else is stored and downloadable, but agents can't read it.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select

from app.core.config import settings
from app.core.db import SessionLocal
from app.events import bus
from app.models import Attachment, AttachmentChunk
from app.services import embeddings

log = logging.getLogger(__name__)

RASTER = {"image/png", "image/jpeg", "image/gif", "image/webp"}
TEXT_EXT = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".xml", ".html", ".htm", ".svg", ".log", ".sql", ".tex", ".bib",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go",
    ".rs", ".rb", ".php", ".swift", ".kt", ".sh", ".bat", ".ps1", ".css", ".scss", ".r",
    ".ipynb", ".env.example", ".gitignore", ".dockerfile",
}
MAX_TEXT_CHARS = 2_000_000
CHUNK_CHARS = 900
CHUNK_OVERLAP = 150
MAX_CHUNKS = 3000
IMAGE_MAX_SIDE = 1568  # what vision models are happy with
MAX_IMAGES_INLINE = 4
RELEVANT = 0.55


class AttachmentError(ValueError):
    pass


def safe_name(name: str) -> str:
    name = Path(name.replace("\\", "/")).name.strip() or "file"
    name = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", name)
    return name[:160]


def to_dict(a: Attachment) -> dict[str, Any]:
    return {"id": a.id, "orgId": a.org_id, "agentId": a.agent_id, "messageId": a.message_id,
            "name": a.name, "mime": a.mime, "size": a.size, "kind": a.kind, "status": a.status,
            "error": a.error, "info": a.info or {}, "shared": a.agent_id is None,
            "uploadedBy": a.uploaded_by,
            "createdAt": a.created_at.isoformat() if a.created_at else None}


def brief(a: Attachment) -> dict[str, Any]:
    """The small form stored in a message's meta."""
    return {"id": a.id, "name": a.name, "kind": a.kind, "mime": a.mime, "size": a.size}


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def folder(a: Attachment) -> Path:
    return settings.uploads_path / a.org_id / a.id


def file_path(a: Attachment) -> Path:
    return folder(a) / "file"


def text_path(a: Attachment) -> Path:
    return folder(a) / "text.txt"


# --- classification & extraction -------------------------------------------------------


def classify(name: str, mime: str, data: bytes) -> tuple[str, str]:
    """(kind, mime) by content, not just by the name the browser reported."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image", "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image", "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image", "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image", "image/webp"
    ext = Path(name).suffix.lower()
    if data[:5] == b"%PDF-":
        return "document", "application/pdf"
    if data[:2] == b"PK":
        if ext in (".docx", ".xlsx"):
            return "document", ("application/vnd.openxmlformats-officedocument."
                                + ("wordprocessingml.document" if ext == ".docx"
                                   else "spreadsheetml.sheet"))
        return "other", mime or "application/zip"
    if ext in TEXT_EXT or mime.startswith("text/") or mime in ("application/json",
                                                                "application/xml"):
        return "document", mime if mime.startswith("text/") else "text/plain"
    if b"\x00" not in data[:4096] and data[:4096]:
        try:
            data[:4096].decode("utf-8")
            return "document", "text/plain"
        except UnicodeDecodeError:
            pass
    return "other", mime or "application/octet-stream"


def _pdf_text(data: bytes) -> tuple[str, dict[str, Any]]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001
            raise AttachmentError("the PDF is password-protected") from None
    pages = []
    for i, page in enumerate(reader.pages, 1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001 - one bad page must not lose the rest
            text = ""
        pages.append(f"[page {i}]\n{text}" if text else f"[page {i}]")
    body = "\n\n".join(pages)
    info: dict[str, Any] = {"pages": len(reader.pages)}
    if not re.search(r"\w", re.sub(r"\[page \d+\]", "", body)):
        info["note"] = "no text found (a scanned PDF?): attach page images instead"
    return body, info


def _docx_text(data: bytes) -> tuple[str, dict[str, Any]]:
    from docx import Document

    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for t, table in enumerate(doc.tables, 1):
        rows = [" | ".join(c.text.strip() for c in row.cells) for row in table.rows]
        parts.append(f"[table {t}]\n" + "\n".join(rows))
    return "\n\n".join(parts), {}


def _xlsx_text(data: bytes) -> tuple[str, dict[str, Any]]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out, total = [], 0
    for ws in wb.worksheets:
        out.append(f"[sheet {ws.title}]")
        for row in ws.iter_rows(values_only=True):
            if all(v is None for v in row):
                continue
            out.append("\t".join("" if v is None else str(v) for v in row))
            total += 1
            if total >= 50_000:
                out.append("(truncated)")
                break
    return "\n".join(out), {"sheets": len(wb.worksheets), "rows": total}


def extract(kind: str, mime: str, data: bytes) -> tuple[str, dict[str, Any]]:
    """Blocking: text and info for a document (call in a thread)."""
    if kind == "image":
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            return "", {"width": im.width, "height": im.height}
    if kind != "document":
        return "", {"note": "this kind of file can't be read by agents"}
    if mime == "application/pdf":
        text, info = _pdf_text(data)
    elif mime.endswith("wordprocessingml.document"):
        text, info = _docx_text(data)
    elif mime.endswith("spreadsheetml.sheet"):
        text, info = _xlsx_text(data)
    else:
        text, info = data.decode("utf-8", errors="replace"), {}
    text = text.replace("\r\n", "\n").replace("\x00", "")
    if len(text) > MAX_TEXT_CHARS:
        text, info["truncated"] = text[:MAX_TEXT_CHARS], True
    info["chars"] = len(text)
    return text, info


def chunk(text: str) -> list[tuple[int, str]]:
    """Passages of ~CHUNK_CHARS, split on paragraph/line/sentence boundaries."""
    out: list[tuple[int, str]] = []
    pos, n = 0, len(text)
    while pos < n and len(out) < MAX_CHUNKS:
        end = min(n, pos + CHUNK_CHARS)
        if end < n:
            window = text[pos + CHUNK_CHARS // 2:end]
            for sep in ("\n\n", "\n", ". ", " "):
                cut = window.rfind(sep)
                if cut >= 0:
                    end = pos + CHUNK_CHARS // 2 + cut + len(sep)
                    break
        piece = text[pos:end].strip()
        if piece:
            out.append((pos, piece))
        if end >= n:
            break
        pos = max(end - CHUNK_OVERLAP, pos + 1)
    return out


# --- saving ----------------------------------------------------------------------------


async def save_upload(org_id: str, agent_id: str | None, filename: str, data: bytes, mime: str = "",
                      *, uploaded_by: str = "user") -> Attachment:
    if not data:
        raise AttachmentError("the file is empty")
    if len(data) > settings.max_upload_bytes:
        raise AttachmentError(f"files can be at most {settings.max_upload_bytes // 1_000_000} MB")
    name = safe_name(filename)
    kind, mime = classify(name, (mime or "").split(";")[0].strip().lower(), data)
    a = Attachment(org_id=org_id, agent_id=agent_id, name=name, mime=mime, size=len(data),
                   sha256=hashlib.sha256(data).hexdigest(), kind=kind,
                   status="processing", uploaded_by=uploaded_by, info={})
    async with SessionLocal() as session:
        session.add(a)
        await session.commit()
    folder(a).mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(file_path(a).write_bytes, data)
    await bus.publish("attachment.created", to_dict(a), org_id=org_id, agent_id=agent_id)
    task = asyncio.create_task(_process(a.id, data), name=f"attachment:{a.id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return a


_tasks: set[asyncio.Task] = set()


async def _process(att_id: str, data: bytes) -> None:
    async with SessionLocal() as session:
        a = await session.get(Attachment, att_id)
    if a is None:
        return
    status, error, info, text = "ready", "", {}, ""
    try:
        text, info = await asyncio.to_thread(extract, a.kind, a.mime, data)
        if text:
            await asyncio.to_thread(text_path(a).write_text, text, "utf-8")
            info["chunks"] = await _index(a, text)
    except AttachmentError as e:
        status, error = "failed", str(e)
    except Exception as e:  # noqa: BLE001 - a corrupt file must not break the app
        log.warning("processing %s failed: %s", a.name, e)
        status, error = "failed", f"couldn't read this file: {type(e).__name__}: {e}"[:400]
    async with SessionLocal() as session:
        row = await session.get(Attachment, att_id)
        if row is None:  # deleted while processing
            return
        row.status, row.error, row.info = status, error, info
        await session.commit()
    await bus.publish("attachment.updated", to_dict(row), org_id=row.org_id, agent_id=row.agent_id)


async def _index(a: Attachment, text: str) -> int:
    pieces = chunk(text)
    if not pieces:
        return 0
    vectors: list[list[float]] | None = None
    if embeddings.enabled():
        vectors = []
        for i in range(0, len(pieces), 64):
            batch = await embeddings.embed([p for _, p in pieces[i:i + 64]])
            if batch is None:
                vectors = None
                break
            vectors.extend(batch)
    async with SessionLocal() as session:
        for i, (start, content) in enumerate(pieces):
            session.add(AttachmentChunk(
                attachment_id=a.id, org_id=a.org_id, agent_id=a.agent_id, idx=i, start=start,
                content=content, embedding=vectors[i] if vectors else None))
        await session.commit()
    return len(pieces)


async def get(att_id: str) -> Attachment | None:
    async with SessionLocal() as session:
        return await session.get(Attachment, att_id)


async def visible(org_id: str, agent_id: str | None, *, limit: int = 100) -> list[Attachment]:
    """What an agent can use: its own library plus everything shared with the org."""
    scope = Attachment.agent_id.is_(None) if agent_id is None else or_(
        Attachment.agent_id.is_(None), Attachment.agent_id == agent_id)
    async with SessionLocal() as session:
        return list((await session.execute(select(Attachment).where(
            Attachment.org_id == org_id, scope).order_by(Attachment.created_at.desc())
            .limit(limit))).scalars())


async def for_org(org_id: str, agent_id: str | None = None, scope: str = "all") -> list[Attachment]:
    async with SessionLocal() as session:
        stmt = select(Attachment).where(Attachment.org_id == org_id)
        if scope == "shared":
            stmt = stmt.where(Attachment.agent_id.is_(None))
        elif agent_id:
            stmt = stmt.where(Attachment.agent_id == agent_id)
        return list((await session.execute(stmt.order_by(Attachment.created_at.desc()))).scalars())


async def link_to_message(att_ids: list[str], org_id: str, message_id: str) -> None:
    async with SessionLocal() as session:
        for a in (await session.execute(select(Attachment).where(
                Attachment.id.in_(att_ids), Attachment.org_id == org_id))).scalars():
            a.message_id = message_id
        await session.commit()


async def resolve_ids(org_id: str, ids: list[str]) -> list[Attachment]:
    """The user's attachment ids for a message, verified to belong to this org."""
    ids = list(dict.fromkeys(str(i) for i in ids))[:20]
    if not ids:
        return []
    async with SessionLocal() as session:
        rows = (await session.execute(select(Attachment).where(
            Attachment.id.in_(ids), Attachment.org_id == org_id))).scalars().all()
    found = {a.id: a for a in rows}
    missing = [i for i in ids if i not in found]
    if missing:
        raise AttachmentError(f"unknown attachment: {missing[0]}")
    return [found[i] for i in ids]


async def delete(att_id: str) -> None:
    async with SessionLocal() as session:
        a = await session.get(Attachment, att_id)
        if a is None:
            raise AttachmentError("file not found")
        await session.delete(a)
        await session.commit()
    await asyncio.to_thread(shutil.rmtree, folder(a), True)
    await bus.publish("attachment.deleted", {"id": att_id}, org_id=a.org_id, agent_id=a.agent_id)


async def delete_for_agent(agent_id: str) -> None:
    async with SessionLocal() as session:
        ids = (await session.execute(select(Attachment.id).where(
            Attachment.agent_id == agent_id))).scalars().all()
    for i in ids:
        await delete(i)


def delete_org_files(org_id: str) -> None:
    shutil.rmtree(settings.uploads_path / org_id, ignore_errors=True)


# --- reading ---------------------------------------------------------------------------


def read_text(a: Attachment, offset: int = 0, length: int = 6000) -> tuple[str, int]:
    """(slice, total chars) of a document's extracted text."""
    p = text_path(a)
    if not p.is_file():
        return "", 0
    text = p.read_text("utf-8")
    offset = max(0, offset)
    return text[offset:offset + max(200, min(length, 20000))], len(text)


def image_data_url(a: Attachment) -> str:
    """The image as a JPEG/PNG data: URL sized for vision models."""
    from PIL import Image

    with Image.open(file_path(a)) as im:
        im.load()
        if max(im.size) > IMAGE_MAX_SIDE:
            im.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
        buf = io.BytesIO()
        if im.mode in ("RGBA", "LA", "P"):
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", rgba.size, "white")
            bg.paste(rgba, mask=rgba.split()[-1])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# --- searching -------------------------------------------------------------------------


async def search(org_id: str, agent_id: str | None, query: str, *, k: int = 5,
                 min_similarity: float = 0.4) -> list[tuple[AttachmentChunk, str, float]]:
    """Hybrid (vector + keyword) passage search: (chunk, file name, similarity)."""
    scope = AttachmentChunk.agent_id.is_(None) if agent_id is None else or_(
        AttachmentChunk.agent_id.is_(None), AttachmentChunk.agent_id == agent_id)
    ranked: dict[int, list[Any]] = {}
    async with SessionLocal() as session:
        vec = (await embeddings.embed([query]) or [None])[0]
        if vec is not None:
            rows = (await session.execute(
                select(AttachmentChunk, AttachmentChunk.embedding.cosine_distance(vec).label("d"))
                .where(AttachmentChunk.org_id == org_id, scope,
                       AttachmentChunk.embedding.is_not(None)).order_by("d").limit(k * 3))).all()
            for rank, r in enumerate(rows):
                sim = 1 - float(r.d)
                if sim >= min_similarity:
                    ranked[r.AttachmentChunk.id] = [r.AttachmentChunk, 1 / (60 + rank), sim]
        # Keyword side: any of the question's words, best matches first (the vector
        # side handles paraphrases; this catches exact names and numbers).
        words = list(dict.fromkeys(re.findall(r"[a-z0-9]{3,}", query.lower())))[:12]
        rows2: list[AttachmentChunk] = []
        if words:
            doc = func.to_tsvector("simple", AttachmentChunk.content)
            q = func.to_tsquery("simple", " | ".join(words))
            rows2 = list((await session.execute(
                select(AttachmentChunk).where(AttachmentChunk.org_id == org_id, scope,
                                              doc.op("@@")(q))
                .order_by(func.ts_rank(doc, q).desc()).limit(k * 3))).scalars().all())
        for rank, c in enumerate(rows2):
            entry = ranked.setdefault(c.id, [c, 0.0, 0.0])
            entry[1] += 1 / (60 + rank)
        best = sorted(ranked.values(), key=lambda e: e[1], reverse=True)[:k]
        names = dict((await session.execute(select(Attachment.id, Attachment.name).where(
            Attachment.id.in_({e[0].attachment_id for e in best} or {""})))).all())
    return [(c, names.get(c.attachment_id, "?"), sim) for c, _, sim in best]


async def relevant(org_id: str, agent_id: str, text: str, k: int = 3) -> list[tuple[str, str]]:
    """Passages from the agent's files worth bringing into a turn about ``text``."""
    if not text.strip() or not embeddings.enabled():
        return []
    hits = await search(org_id, agent_id, text[:2000], k=k, min_similarity=RELEVANT)
    return [(name, c.content) for c, name, sim in hits if sim >= RELEVANT]
