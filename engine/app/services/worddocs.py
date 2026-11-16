"""Word documents on the Stage: a real .docx the user edits in the browser and agents read
and change with tools. The file lives beside the database (data/stage/<org>/<doc>.docx); the
StageDoc row holds its name and version, so saves and agent edits go through one lock.

A .doc opens as .docx: through LibreOffice when it's installed (formatting kept), else from
the text and tables services/worddoc reads.
"""

from __future__ import annotations

import io
import re
import secrets
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from docx import Document
from docx.comments import Comments
from docx.document import Document as DocxDocument
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml import etree

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import StageDoc
from app.services import stagedocs as docs
from app.services import worddoc
from app.services.stagedocs import Conflict, DocError

MAX_BYTES = 50 * 1024 * 1024
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def path(d: StageDoc) -> Path:
    return settings.data_path / "stage" / d.org_id / f"{d.id}.docx"


def _check(raw: bytes) -> None:
    if len(raw) > MAX_BYTES:
        raise DocError("a Word document can be at most 50 MB")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if "word/document.xml" not in z.namelist():
                raise DocError("not a Word document (.docx)")
    except zipfile.BadZipFile:
        raise DocError("not a Word document (.docx)") from None


def _soffice() -> str | None:
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    for p in (r"C:\Program Files\LibreOffice\program\soffice.exe",
              "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if Path(p).exists():
            return p
    return None


def docx_from_doc(raw: bytes) -> bytes:
    """A .doc as .docx: LibreOffice's conversion if it's here, else the text and tables."""
    office = _soffice()
    if office:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.doc"
            src.write_bytes(raw)
            try:
                subprocess.run([office, "--headless", "--convert-to", "docx", "--outdir", tmp,
                                str(src)], capture_output=True, timeout=120, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
            out = Path(tmp) / "in.docx"
            if out.exists():
                return out.read_bytes()
    try:
        text, _ = worddoc.text(raw)
    except worddoc.DocError as e:
        raise DocError(str(e)) from None
    doc = Document()
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        j = i
        while j < len(lines) and " | " in lines[j]:
            j += 1
        if j - i >= 2:  # rows of a table (see worddoc._clean)
            rows = [re.split(r" \| ?", ln.rstrip(" |")) for ln in lines[i:j]]
            table = doc.add_table(rows=len(rows), cols=max(len(r) for r in rows))
            table.style = "Table Grid"
            for r, row in enumerate(rows):
                for c, cell in enumerate(row):
                    table.cell(r, c).text = cell.strip()
            i = j
            continue
        if lines[i].strip():
            doc.add_paragraph(lines[i].strip())
        i += 1
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def blank() -> bytes:
    buf = io.BytesIO()
    Document().save(buf)
    return buf.getvalue()


async def create(org_id: str, title: str, raw: bytes | None = None, *, name: str = "",
                 created_by: str = "user", by_name: str = "user") -> StageDoc:
    """A Word document from a .docx or .doc's bytes, or a blank one."""
    if raw is not None and raw[:8] == worddoc.MAGIC:
        raw = docx_from_doc(raw)
    raw = raw if raw is not None else blank()
    _check(raw)
    d = await docs.create(org_id, "word", title, {"name": name or f"{title}.docx", "size": len(raw)},
                          created_by=created_by, by_name=by_name)
    path(d).parent.mkdir(parents=True, exist_ok=True)
    path(d).write_bytes(raw)
    return d


def read_bytes(d: StageDoc) -> bytes:
    f = path(d)
    if not f.exists():
        raise DocError("the document's file is missing")
    return f.read_bytes()


async def save(doc_id: str, raw: bytes, by_name: str, base: int | None = None,
               ) -> StageDoc:
    """The whole file, from the editor. ``base`` is the version it started from: if someone
    else changed the document since, nothing is saved and Conflict is raised."""
    _check(raw)
    async with docs._locks[doc_id]:
        async with SessionLocal() as session:
            d = await session.get(StageDoc, doc_id)
            if d is None or d.kind != "word":
                raise DocError("Word document not found")
            if base is not None and base != d.version:
                raise Conflict(f"'{d.title}' was changed by {d.updated_by} meanwhile")
            path(d).write_bytes(raw)
            d.data = {**(d.data or {}), "size": len(raw)}
            d.version += 1
            d.updated_by = by_name
            await session.commit()
            await session.refresh(d)
    await docs._publish(d, source=by_name)
    return d


# --- what agents see and change ---------------------------------------------------------------


def _blocks(doc: DocxDocument) -> Iterator[Paragraph | Table]:
    for el in doc.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            yield Paragraph(el, doc)
        elif tag == "tbl":
            yield Table(el, doc)


def _paragraphs(doc: DocxDocument) -> list[Paragraph]:
    """The body's paragraphs as word_read numbers them (tables aside)."""
    return [b for b in _blocks(doc) if isinstance(b, Paragraph)]


# Comments: their text is in word/comments.xml; where they sit is marked in the body
# (commentRangeStart/End); whether they are resolved, and which comment a reply answers, is
# in word/commentsExtended.xml, keyed by the paraId of the comment's last paragraph.
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
_W15 = "{http://schemas.microsoft.com/office/word/2012/wordml}"
_RT_COMMENTS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
_RT_EXTENDED = "http://schemas.microsoft.com/office/2011/relationships/commentsExtended"
_CT_EXTENDED = ("application/vnd.openxmlformats-officedocument.wordprocessingml."
                "commentsExtended+xml")


@dataclass
class Comment:
    id: str
    author: str
    text: str
    para_id: str = ""  # its last paragraph's w14:paraId, the key in commentsExtended
    done: bool = False
    parent: str = ""  # the id of the comment this one replies to
    paragraphs: list[int] = field(default_factory=list)  # word_read numbers it is anchored on
    in_table: bool = False
    anchor: str = ""  # the text it is anchored to


def _related(doc: DocxDocument, reltype: str):
    for rel in doc.part.rels.values():
        if rel.reltype == reltype and not rel.is_external:
            return rel.target_part
    return None


def _extended(doc: DocxDocument) -> tuple[Any, Any] | None:
    """The commentsExtended part and its parsed XML, if the document has one."""
    part = _related(doc, _RT_EXTENDED)
    if part is None:
        return None
    return part, etree.fromstring(part.blob)


def comments(doc: DocxDocument) -> list[Comment]:
    """Every comment in the document, in the order it appears, with where it is anchored."""
    part = _related(doc, _RT_COMMENTS)
    if part is None:
        return []
    found: dict[str, Comment] = {}
    for el in part.element.iter(f"{_W}comment"):
        paras = list(el.iter(f"{_W}p"))
        text = "\n".join("".join(t.text or "" for t in p.iter(f"{_W}t")) for p in paras).strip()
        cid = el.get(f"{_W}id") or ""
        found[cid] = Comment(id=cid, author=el.get(f"{_W}author") or "someone", text=text,
                             para_id=(paras[-1].get(f"{_W14}paraId") or "") if paras else "")
    if ext := _extended(doc):
        by_para = {c.para_id: c for c in found.values() if c.para_id}
        for ex in ext[1].iter(f"{_W15}commentEx"):
            c = by_para.get(ex.get(f"{_W15}paraId") or "")
            if c is None:
                continue
            c.done = ex.get(f"{_W15}done") == "1"
            if parent := by_para.get(ex.get(f"{_W15}paraIdParent") or ""):
                c.parent = parent.id
    # Where each one sits, walking the body in order: paragraphs as word_read numbers them.
    body = doc.element.body
    n, here, open_ = 0, 0, set()
    anchors: dict[str, list[str]] = {}
    for el in body.iter(f"{_W}p", f"{_W}commentRangeStart", f"{_W}commentRangeEnd",
                        f"{_W}commentReference", f"{_W}t", f"{_W}tab"):
        tag = el.tag
        if tag == f"{_W}p":
            if el.getparent() is body:
                n += 1
                here = n
            elif not any(a.tag == f"{_W}p" for a in el.iterancestors()):
                here = 0  # in a table
            for cid in open_:  # a range running on into this paragraph
                _place(found.get(cid), here)
                if anchors.get(cid):
                    anchors[cid].append("\n")
            continue
        cid = el.get(f"{_W}id") or ""
        if tag == f"{_W}commentRangeStart":
            open_.add(cid)
            anchors.setdefault(cid, [])
            _place(found.get(cid), here)
        elif tag == f"{_W}commentRangeEnd":
            open_.discard(cid)
        elif tag == f"{_W}commentReference":
            _place(found.get(cid), here)  # a comment on a point, with no range
        else:
            piece = (el.text or "") if tag == f"{_W}t" else "\t"
            for cid in open_:
                anchors[cid].append(piece)
    for cid, pieces in anchors.items():
        if c := found.get(cid):
            c.anchor = " ".join("".join(pieces).split())
    return list(found.values())


def _place(c: Comment | None, here: int) -> None:
    if c is None:
        return
    if not here:
        c.in_table = True
    elif here not in c.paragraphs:
        c.paragraphs.append(here)


def _where(c: Comment) -> str:
    if c.paragraphs:
        a, b = c.paragraphs[0], c.paragraphs[-1]
        return f"paragraph {a}" if a == b else f"paragraphs {a}-{b}"
    return "a table" if c.in_table else "no text (what it was on has been deleted)"


def describe_comments(found: list[Comment]) -> str:
    """The open comments as word_read shows them: where, on what, who, and the replies."""
    tops = [c for c in found if not c.parent and not c.done]
    if not tops:
        done = sum(1 for c in found if not c.parent and c.done)
        return f"No open comments ({done} resolved)." if done else ""
    replies: dict[str, list[Comment]] = {}
    for c in found:
        if c.parent:
            replies.setdefault(c.parent, []).append(c)
    out = [f"Comments ({len(tops)} open; word_write reply answers one, word_write resolve "
           "closes it once it is dealt with):"]
    for c in tops:
        on = f' on "{_cut(c.anchor)}"' if c.anchor else ""
        out.append(f"[comment {c.id}] {c.author}, at {_where(c)}{on}: {c.text}")
        for r in replies.get(c.id, []):
            out.append(f"    reply from {r.author}: {r.text}")
    return "\n".join(out)


def _cut(text: str, most: int = 300) -> str:
    return text if len(text) <= most else f"{text[:most]}…"


def _initials(name: str) -> str:
    return "".join(part[:1] for part in name.split()[:3]).upper() or "AI"


def _para_ids(doc: DocxDocument) -> set[str]:
    """Every w14:paraId in the file, body and comments together."""
    return {el.get(f"{_W14}paraId") for el in doc.element.body.iter()} | {
        el.get(f"{_W14}paraId") for part in (
            [_related(doc, _RT_COMMENTS), _related(doc, _RT_EXTENDED)]
        ) if part is not None and part.blob for el in etree.fromstring(part.blob).iter()
    }


def _new_para_id(doc: DocxDocument) -> str:
    """A paragraph id for a new comment: 8 hex digits under 0x80000000, unused in this file."""
    used = _para_ids(doc)
    for _ in range(200):
        if (pid := f"{secrets.randbelow(0x7FFFFFFE) + 1:08X}") not in used:
            return pid
    raise DocError("this document is out of paragraph ids for another comment")


def _threads_part(doc: DocxDocument) -> tuple[Any, Any]:
    """commentsExtended and its XML — the part where Word records which comment answers
    which. A document nobody has ever replied in has none, so it is written here."""
    if ext := _extended(doc):
        return ext
    root = etree.Element(f"{_W15}commentsEx",
                         nsmap={"w": _W[1:-1], "w14": _W14[1:-1], "w15": _W15[1:-1]})
    part = Part(PackURI("/word/commentsExtended.xml"), _CT_EXTENDED, b"", doc.part.package)
    doc.part.relate_to(part, _RT_EXTENDED)
    return part, root


def _answer_range(doc: DocxDocument, asked_id: str, answer_id: str) -> None:
    """Mark the answer on the same words as the question. Word only shows a comment the body
    refers to, so a threaded reply gets its own range around its parent's range."""
    body = doc.element.body
    start = next((el for el in body.iter(f"{_W}commentRangeStart")
                  if el.get(f"{_W}id") == asked_id), None)
    end = next((el for el in body.iter(f"{_W}commentRangeEnd")
                if el.get(f"{_W}id") == asked_id), None)
    if start is None or end is None:  # its text was deleted: the thread link is enough
        return
    head = etree.Element(f"{_W}commentRangeStart")
    head.set(f"{_W}id", answer_id)
    start.addnext(head)
    tail = etree.Element(f"{_W}commentRangeEnd")
    tail.set(f"{_W}id", answer_id)
    run = etree.SubElement(end.getparent(), f"{_W}r")
    etree.SubElement(run, f"{_W}commentReference").set(f"{_W}id", answer_id)
    end.addprevious(tail)
    end.addprevious(run)


def reply(doc: DocxDocument, comment_id: str, text: str, author: str) -> Comment:
    """Answer a comment in its thread: the answer is a comment of its own, and commentsExtended
    says which one it answers, so Word and word_read show it underneath rather than as new
    feedback. Answers go under the first comment of a thread, which is how they are read back."""
    found = {c.id: c for c in comments(doc)}
    asked = found.get(str(comment_id).removeprefix("comment").strip())
    if asked is None:
        open_ = ", ".join(i for i, c in found.items() if not c.parent and not c.done)
        raise DocError(f"there's no comment {comment_id} to answer"
                       + (f" (open ones: {open_})" if open_ else " (there are none)"))
    if not text.strip():
        raise DocError("say something back: text is the answer to the comment")
    part = _related(doc, _RT_COMMENTS)
    if part is None:
        raise DocError("this document has no comments to answer")
    top = found.get(asked.parent) or asked
    made = Comments(part.element, part).add_comment(text=text.strip(), author=author,
                                                    initials=_initials(author))
    mine = _new_para_id(doc)
    made.paragraphs[-1]._p.set(f"{_W14}paraId", mine)
    theirs = top.para_id
    if not theirs:  # a comment written without paragraph ids needs one to be answered under
        holder = next((el for el in part.element.iter(f"{_W}comment")
                       if el.get(f"{_W}id") == top.id), None)
        rows = list(holder.iter(f"{_W}p")) if holder is not None else []
        if not rows:
            raise DocError(f"comment {comment_id} has no text to answer under")
        theirs = _new_para_id(doc)
        rows[-1].set(f"{_W14}paraId", theirs)
    ext = _threads_part(doc)
    entry = etree.SubElement(ext[1], f"{_W15}commentEx")
    entry.set(f"{_W15}paraId", mine)
    entry.set(f"{_W15}paraIdParent", theirs)
    entry.set(f"{_W15}done", "1")  # an answer is not itself a request to act on
    ext[0]._blob = etree.tostring(ext[1], xml_declaration=True, encoding="UTF-8", standalone=True)
    _answer_range(doc, top.id, str(made.comment_id))
    return Comment(id=str(made.comment_id), author=author, text=text.strip(), para_id=mine,
                   done=True, parent=top.id, paragraphs=list(top.paragraphs),
                   in_table=top.in_table)


def resolve(doc: DocxDocument, comment_id: str) -> Comment:
    """Mark a comment resolved, as Word does. A document without commentsExtended (one that
    has never had a comment resolved or answered) has nowhere to record that, so there the
    comment and its marks in the text are removed instead."""
    found = {c.id: c for c in comments(doc)}
    c = found.get(str(comment_id).removeprefix("comment").strip())
    if c is None or c.parent:
        open_ = ", ".join(i for i, x in found.items() if not x.parent and not x.done)
        raise DocError(f"there's no comment {comment_id} to resolve"
                       + (f" (open ones: {open_})" if open_ else " (none are open)"))
    ext = _extended(doc)
    entry = None
    if ext and c.para_id:
        entry = next((e for e in ext[1].iter(f"{_W15}commentEx")
                      if e.get(f"{_W15}paraId") == c.para_id), None)
    if entry is not None:
        entry.set(f"{_W15}done", "1")
        ext[0]._blob = etree.tostring(ext[1], xml_declaration=True, encoding="UTF-8",
                                      standalone=True)
    else:
        gone = {c.id} | {r.id for r in found.values() if r.parent == c.id}
        part = _related(doc, _RT_COMMENTS)
        for el in list(part.element.iter(f"{_W}comment")):
            if el.get(f"{_W}id") in gone:
                el.getparent().remove(el)
        for el in list(doc.element.body.iter(f"{_W}commentRangeStart", f"{_W}commentRangeEnd",
                                              f"{_W}commentReference")):
            if el.get(f"{_W}id") in gone:
                holder = el.getparent()
                if el.tag == f"{_W}commentReference" and holder.tag == f"{_W}r":
                    el, holder = holder, holder.getparent()  # the run that only held it
                holder.remove(el)
    c.done = True
    return c


def read(d: StageDoc, start: int = 1, count: int = 300) -> str:
    """Numbered paragraphs (with their style) and tables, for word_read, then the open
    comments, each marked on the paragraphs it is anchored to."""
    doc = Document(io.BytesIO(read_bytes(d)))
    found = comments(doc)
    marks: dict[int, list[str]] = {}
    for c in found:
        if not c.parent and not c.done:
            for at in c.paragraphs:
                marks.setdefault(at, []).append(c.id)
    out, n, t = [], 0, 0
    start = max(1, start)
    for b in _blocks(doc):
        if isinstance(b, Table):
            t += 1
            if start - 1 <= n < start + count:  # among the paragraphs shown
                rows = ["| " + " | ".join(c.text.strip().replace("\n", " ") for c in r.cells) + " |"
                        for r in b.rows]
                out.append(f"[table {t}]\n" + "\n".join(rows))
            continue
        n += 1
        if n < start or n >= start + count:
            continue
        style = b.style.name if b.style is not None else ""
        tag = f"({style}) " if style and style != "Normal" else ""
        noted = "".join(f" [comment {i}]" for i in marks.get(n, []))
        out.append(f"[{n}] {tag}{b.text}{noted}")
    head = f"Word document '{d.title}' ({d.id}), {n} paragraphs"
    if start > 1 or n >= start + count:
        head += f"; paragraphs {start}-{min(n, start + count - 1)}"
        if n >= start + count:
            out.append(f"(more below: start={start + count})")
    if notes := describe_comments(found):
        out.append("\n" + notes)
    return head + ":\n\n" + "\n".join(out) if out else head + " (empty)."


def comments_only(d: StageDoc) -> str:
    """Just the comments of a document and where they sit — for when the user says "look at my
    comments" and the text itself is not what they are asking about."""
    found = comments(Document(io.BytesIO(read_bytes(d))))
    if notes := describe_comments(found):
        return f"Word document '{d.title}' ({d.id}):\n{notes}"
    return f"Word document '{d.title}' ({d.id}): nobody has left a comment on it."


def _even(text: str) -> str:
    """Text to compare against: nbsp and zero-width characters gone, whitespace collapsed."""
    return " ".join(text.replace("\u00a0", " ").replace("\u200b", "").split())


def where_is(d: StageDoc, passage: str) -> tuple[int, int] | None:
    """Which paragraphs the user's selection covers, as word_read numbers them. The editor
    reports a selection as text, so it is found again in the file to be numbered."""
    texts = [_even(p.text) for p in _paragraphs(Document(io.BytesIO(read_bytes(d))))]
    rows = [ln for ln in (_even(ln) for ln in passage.splitlines()) if ln]
    if not rows:
        return None
    a = 0
    for ln in rows:  # the first line that is really somewhere in the document
        if len(ln) < 4:
            continue
        if hit := [i for i, t in enumerate(texts) if ln[:60] in t]:
            a = hit[0] + 1
            break
    if not a:
        return None
    b = a
    for ln in reversed(rows[1:]):  # the last line still in it (a long one arrives cut short)
        if len(ln) < 4:
            continue
        if hit := [i for i, t in enumerate(texts) if i + 1 >= a and ln[-60:] in t]:
            b = hit[-1] + 1
            break
    return a, b


def _replace_in(p: Paragraph, find: str, replace: str) -> bool:
    """Replace ``find`` in a paragraph's runs, keeping each run's formatting."""
    text = "".join(r.text for r in p.runs)
    i = text.find(find)
    if i < 0:
        return False
    end, pos, done = i + len(find), 0, False
    for r in p.runs:
        t = r.text
        a, b = pos, pos + len(t)
        pos = b
        if b <= i or a >= end:
            continue
        s, e = max(i, a) - a, min(end, b) - a
        r.text = t[:s] + ("" if done else replace) + t[e:]
        done = True
    return True


def _style(doc: DocxDocument, name: str) -> str | None:
    try:
        doc.styles[name]
        return name
    except KeyError:
        return None


def _new_paragraphs(doc: DocxDocument, text: str) -> list[Paragraph]:
    """Paragraphs for some text, one per line: '# ' headings and '- ' / '1. ' list items."""
    made = []
    for line in text.split("\n"):
        style, body = None, line
        if m := re.match(r"(#{1,6})\s+(.*)", line):
            style, body = _style(doc, f"Heading {len(m.group(1))}"), m.group(2)
        elif m := re.match(r"[-*]\s+(.*)", line):
            style, body = _style(doc, "List Bullet"), m.group(1)
            body = body if style else f"• {body}"
        elif m := re.match(r"\d+[.)]\s+(.*)", line):
            style, body = _style(doc, "List Number"), m.group(1)
            body = body if style else line
        made.append(doc.add_paragraph(body, style=style))
    return made


async def edit(doc_id: str, by_name: str, *, find: str | None = None, replace: str = "",
               append: str | None = None, after: int | None = None,
               resolve_comment: str | None = None, reply_to: str | None = None,
               reply_text: str = "") -> StageDoc:
    """An agent's change: replace one exact passage (within a paragraph), add text after
    paragraph ``after``, or add to the end; and/or resolve or answer a comment. Formatting
    elsewhere is untouched."""
    async with docs._locks[doc_id]:
        async with SessionLocal() as session:
            d = await session.get(StageDoc, doc_id)
            if d is None or d.kind != "word":
                raise DocError("Word document not found")
            doc = Document(io.BytesIO(read_bytes(d)))
            paras = _paragraphs(doc)
            cells = [p for t in doc.tables for row in t.rows for c in row.cells for p in c.paragraphs]
            if find:
                hits = [p for p in paras + cells if find in p.text]
                if not hits:
                    raise DocError("that text isn't in the document (it must sit within one "
                                   "paragraph; word_read shows them)")
                if len(hits) > 1:
                    raise DocError(f"that text is in {len(hits)} paragraphs; include more of it "
                                   "so it's unique")
                p = hits[0]
                if not _replace_in(p, find, replace):
                    raise DocError("that text is inside a link or field, which can't be edited")
                if not p.text.strip() and p in paras:
                    p._p.getparent().remove(p._p)
            elif append:
                made = _new_paragraphs(doc, append.rstrip("\n"))
                if after is not None:
                    if not 1 <= after <= len(paras):
                        raise DocError(f"there's no paragraph {after} (there are {len(paras)})")
                    anchor = paras[after - 1]._p
                    for p in made:
                        anchor.addnext(p._p)
                        anchor = p._p
            elif not (resolve_comment or reply_to):
                raise DocError("give find (with replace), append, resolve, or reply")
            if resolve_comment:
                resolve(doc, resolve_comment)
            if reply_to:
                reply(doc, reply_to, reply_text, by_name)
            buf = io.BytesIO()
            doc.save(buf)
            path(d).write_bytes(buf.getvalue())
            d.data = {**(d.data or {}), "size": buf.tell()}
            d.version += 1
            d.updated_by = by_name
            await session.commit()
            await session.refresh(d)
    await docs._publish(d, source=by_name)
    return d


def cleanup(d: StageDoc) -> None:
    path(d).unlink(missing_ok=True)

