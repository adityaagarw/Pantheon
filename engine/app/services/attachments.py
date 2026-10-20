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
* Videos (MP4, MOV, WebM, MKV, AVI) are kept as uploaded, with a poster frame and a small
  copy for models: VIDEO_FPS frames a second, at most VIDEO_MAX_SIDE pixels, the first
  VIDEO_MAX_SECONDS. A model that takes video (Qwen3.8 on Radiance) gets that copy as a
  ``video_url`` part, the server sampling it within its own frame budget; a model that
  only takes images gets VIDEO_STILLS frames from it, with their times.
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

from sqlalchemy import or_, select

from app.core.config import settings
from app.core.db import SessionLocal
from app.events import bus
from app.models import Attachment, AttachmentChunk
from app.services import embeddings, textsearch

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
VIDEO_PLAYABLE = {"video/mp4", "video/webm"}  # what the interface plays inline
VIDEO_FPS = 2  # Radiance samples at 2 a second by default: more would be thrown away
VIDEO_MAX_SIDE = 640
VIDEO_MAX_SECONDS = 600
VIDEO_STILLS = 8
# ISO media brands that are pictures, not video (HEIC, AVIF)
_STILL_BRANDS = {b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1", b"avif", b"avis"}
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


def poster_path(a: Attachment) -> Path:
    return folder(a) / "poster.jpg"


def model_video_path(a: Attachment) -> Path:
    return folder(a) / "model.mp4"


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
    if data[4:8] == b"ftyp" and data[8:12] not in _STILL_BRANDS:
        return "video", "video/quicktime" if data[8:12] == b"qt  " else "video/mp4"
    if data[:4] == b"\x1a\x45\xdf\xa3":  # EBML: WebM or Matroska
        return "video", "video/webm" if b"webm" in data[:64] else "video/x-matroska"
    if data[:4] == b"RIFF" and data[8:12] == b"AVI ":
        return "video", "video/x-msvideo"
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


def _even(n: int) -> int:
    return max(2, n - n % 2)


def prepare_video(src: Path, dest: Path, poster: Path) -> dict[str, Any]:
    """Blocking: the video's info, its poster frame, and the small copy models get."""
    from fractions import Fraction

    import av

    try:
        inp = av.open(str(src))
    except av.FFmpegError:
        raise AttachmentError("this video can't be opened (damaged, or a format "
                              "Pantheon doesn't read)") from None
    with inp:
        if not inp.streams.video:
            raise AttachmentError("the file has no video in it")
        stream = inp.streams.video[0]
        stream.thread_type = "AUTO"
        duration = (inp.duration / 1_000_000 if inp.duration
                    else float(stream.duration * stream.time_base) if stream.duration else 0.0)
        w, h = stream.codec_context.width, stream.codec_context.height
        scale = min(1.0, VIDEO_MAX_SIDE / max(w, h, 1))
        out_w, out_h = _even(round(w * scale)), _even(round(h * scale))
        info: dict[str, Any] = {"width": w, "height": h, "duration": round(duration, 2),
                                "fps": round(float(stream.average_rate or 0), 2),
                                "audio": bool(inp.streams.audio)}
        out = av.open(str(dest), "w", format="mp4")
        enc = out.add_stream("libx264", rate=Fraction(VIDEO_FPS))
        enc.width, enc.height, enc.pix_fmt = out_w, out_h, "yuv420p"
        enc.options = {"crf": "28", "preset": "veryfast"}
        enc.codec_context.time_base = Fraction(1, VIDEO_FPS)
        kept, due, first = 0, 0.0, None
        # The poster: the first frame, then the one a third into the first half-minute
        # (openings are often black or a title).
        poster_at = int(min(duration, 30) / 3 * VIDEO_FPS)
        with out:
            for frame in inp.decode(stream):
                t = frame.time if frame.time is not None else kept / VIDEO_FPS
                if first is None:
                    first = t
                t -= first
                if t > VIDEO_MAX_SECONDS:
                    info["note"] = (f"models see the first {VIDEO_MAX_SECONDS // 60} minutes "
                                    f"of {duration / 60:.0f}")
                    break
                if t + 1e-6 < due:
                    continue
                if kept in (0, poster_at):
                    im = frame.to_image()
                    im.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
                    im.convert("RGB").save(poster, "JPEG", quality=85)
                small = frame.reformat(width=out_w, height=out_h, format="yuv420p")
                small.pts, small.time_base = kept, Fraction(1, VIDEO_FPS)
                for packet in enc.encode(small):
                    out.mux(packet)
                kept += 1
                due = kept / VIDEO_FPS
            for packet in enc.encode():
                out.mux(packet)
        if not kept:
            raise AttachmentError("no frames could be read from this video")
        if not duration:
            info["duration"] = round(kept / VIDEO_FPS, 2)
    info["frames_for_models"] = kept
    return info


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
    name = safe_name(filename)
    kind, mime = classify(name, (mime or "").split(";")[0].strip().lower(), data)
    limit = settings.max_video_bytes if kind == "video" else settings.max_upload_bytes
    if len(data) > limit:
        raise AttachmentError(f"{'videos' if kind == 'video' else 'files'} can be at most "
                              f"{limit // 1_000_000} MB")
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
        if a.kind == "video":
            info = await asyncio.to_thread(prepare_video, file_path(a), model_video_path(a),
                                           poster_path(a))
        else:
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


async def _lead_ids(session, org_id: str) -> list[str]:
    """Agents whose files the whole team sees: anyone who manages someone (a lead's
    files are the team's, so a book given to the lead reaches everyone). Living
    worlds keep every library private."""
    from app.models import Org, Relationship
    from app.services import life

    org = await session.get(Org, org_id)
    if org is None or life.enabled(org):
        return []
    return list((await session.execute(select(Relationship.from_id).where(
        Relationship.org_id == org_id, Relationship.kind == "manages").distinct())).scalars())


async def _scope(session, column_agent, org_id: str, agent_id: str | None):
    owners = [i for i in await _lead_ids(session, org_id) if i != agent_id]
    if agent_id is not None:
        owners.append(agent_id)
    return or_(column_agent.is_(None), column_agent.in_(owners)) if owners else \
        column_agent.is_(None)


async def visible(org_id: str, agent_id: str | None, *, limit: int = 100) -> list[Attachment]:
    """What an agent can use: its own library, its leads' libraries and everything
    shared with the org."""
    async with SessionLocal() as session:
        scope = await _scope(session, Attachment.agent_id, org_id, agent_id)
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


def video_data_url(a: Attachment) -> str:
    """The video's small copy (VIDEO_FPS, VIDEO_MAX_SIDE) as a data: URL for video models."""
    return ("data:video/mp4;base64,"
            + base64.b64encode(model_video_path(a).read_bytes()).decode())


def video_stills(a: Attachment, n: int = VIDEO_STILLS) -> list[tuple[float, str]]:
    """(seconds, JPEG data: URL) of n frames spread over the video, for image-only models."""
    import av

    total = int((a.info or {}).get("frames_for_models") or 1)
    picks = {round(i * (total - 1) / max(n - 1, 1)) for i in range(n)}
    out = []
    with av.open(str(model_video_path(a))) as inp:
        for i, frame in enumerate(inp.decode(inp.streams.video[0])):
            if i in picks:
                buf = io.BytesIO()
                frame.to_image().convert("RGB").save(buf, "JPEG", quality=85)
                out.append((i / VIDEO_FPS, "data:image/jpeg;base64,"
                            + base64.b64encode(buf.getvalue()).decode()))
    return out


async def video_for_model(a: Attachment, model: Any) -> tuple[str, list[str]]:
    """What a model is given for a video: (text, data: URLs). The video itself for a
    model that takes video, frames with their times for one that takes images, else text."""
    head = f"{a.name} ({video_summary(a)})"
    note = (a.info or {}).get("note")
    head += f"; {note}" if note else ""
    if model.video:
        return (f"{head}; the video is attached, {VIDEO_FPS} frames a second at most "
                f"{VIDEO_MAX_SIDE}px (no sound).", [await asyncio.to_thread(video_data_url, a)])
    if model.vision:
        stills = await asyncio.to_thread(video_stills, a)
        times = ", ".join(f"{int(t) // 60}:{int(t) % 60:02d}" for t, _ in stills)
        return (f"{head}; your model doesn't take video, so {len(stills)} frames from it are "
                f"attached, in order, at {times}.", [u for _, u in stills])
    return (f"{head}. Your model can't see images or video, so this video can't be shown to "
            "you; ask for a description, or for its transcript as a document.", [])


def video_summary(a: Attachment) -> str:
    """'0:42, 1920x1080, with sound' for a video's info."""
    info = a.info or {}
    secs = int(info.get("duration") or 0)
    parts = [f"{secs // 60}:{secs % 60:02d}"]
    if info.get("width"):
        parts.append(f"{info['width']}x{info['height']}")
    parts.append("with sound" if info.get("audio") else "no sound")
    return ", ".join(parts)


# --- searching -------------------------------------------------------------------------


async def search(org_id: str, agent_id: str | None, query: str, *, k: int = 5,
                 min_similarity: float = 0.4) -> list[tuple[AttachmentChunk, str, float]]:
    """Hybrid (vector + keyword) passage search: (chunk, file name, similarity)."""
    ranked: dict[int, list[Any]] = {}
    async with SessionLocal() as session:
        scope = await _scope(session, AttachmentChunk.agent_id, org_id, agent_id)
        pool = (await session.execute(select(AttachmentChunk).where(
            AttachmentChunk.org_id == org_id, scope))).scalars().all()
        vec = (await embeddings.embed([query]) or [None])[0]
        if vec is not None:
            hits = textsearch.nearest(pool, vec, lambda c: c.embedding, k * 3)
            for rank, (c, sim) in enumerate(hits):
                if sim >= min_similarity:
                    ranked[c.id] = [c, 1 / (60 + rank), sim]
        # Keyword side: the question's words, best matches first (the vector side
        # handles paraphrases; this catches exact names and numbers).
        rows2 = [c for c, _ in textsearch.keyword(pool, query, lambda c: c.content, k * 3)]
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
