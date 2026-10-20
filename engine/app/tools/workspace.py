"""Workspace tools: files and shell in the org's host workspace.

Paths are relative to the agent's workdir and can never leave the org
workspace (which itself must live under PANTHEON_WORKSPACE_ROOTS).
"""

from __future__ import annotations

import asyncio
import difflib
import fnmatch
import hashlib
import os
import re
import signal
import sys
import tempfile
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from app.core import egress, guard
from app.core.config import settings
from app.services import secrets
from app.tools.base import (
    B,
    I,
    S,
    ToolContext,
    ToolError,
    ToolOutput,
    obj,
    resolve_in_workspace,
    tool,
    truncate,
)

SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".next", "dist", "build",
             ".mypy_cache", ".pytest_cache", ".ruff_cache"}
MAX_READ_BYTES = 2_000_000


def _rel(ctx: ToolContext, p: Path) -> str:
    try:
        return str(p.relative_to(ctx.workdir)).replace("\\", "/") or "."
    except ValueError:
        return str(p)


def _ws_rel(ctx: ToolContext, p: Path) -> str:
    try:
        return p.resolve().relative_to(Path(ctx.workspace).resolve()).as_posix()
    except ValueError:
        return p.as_posix()


async def _not_theirs(ctx: ToolContext, p: Path) -> None:
    """Refuse a change to a file another person's flow step owns (flows.fences); a file
    this agent's step writes first becomes the step's."""
    from app.services import flows

    rel = _ws_rel(ctx, p)
    if ctx.writes_under and ctx.writes_under not in Path(rel).parts[:-1]:
        raise ToolError(f"you may write only under {ctx.writes_under}/: the work's own files "
                        "are not yours to change")
    if why := await flows.why_not_yours(ctx.org.id, ctx.agent.id, rel):
        raise ToolError(why)
    await flows.note_made(ctx.org.id, ctx.agent.id, rel)


async def _snapshot_theirs(ctx: ToolContext) -> dict[Path, bytes | None]:
    """The files others' flow steps own, as they are, so a command can't change them (a
    builder's script regenerated every screen, colleagues' included)."""
    from app.services import flows

    _own, fenced = await flows.fences(ctx.org.id, ctx.agent.id)
    if not fenced:
        return {}
    root = Path(ctx.workspace).resolve()

    def scan() -> dict[Path, bytes | None]:
        out: dict[Path, bytes | None] = {}
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                fp = Path(base) / f
                rel = fp.relative_to(root).as_posix()
                if any(flows.claims(rel, pats) for pats, _ in fenced) and \
                        not flows.claims(rel, _own) and fp.stat().st_size <= MAX_READ_BYTES:
                    out[fp] = fp.read_bytes()
                    if len(out) >= 300:
                        return out
        return out

    return await asyncio.to_thread(scan)


_WRITTEN: dict[str, float] = {}  # path -> when a file tool last wrote it


def _wrote(p: Path) -> None:
    _WRITTEN[str(p.resolve())] = time.monotonic()
    while len(_WRITTEN) > 4096:
        _WRITTEN.pop(next(iter(_WRITTEN)))


def _put_back(before: dict[Path, bytes | None], root: Path, since: float) -> list[str]:
    """Restore owned files a command changed or deleted (not those their owner wrote in the
    meantime); their names."""
    restored = []
    for fp, data in before.items():
        if _WRITTEN.get(str(fp.resolve()), 0) > since:
            continue
        try:
            now = fp.read_bytes() if fp.exists() else None
        except OSError:
            continue
        if now != data and data is not None:
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_bytes(data)
            restored.append(fp.relative_to(root).as_posix())
    return restored


def version_of(text: str) -> str:
    """A short tag for a file's content: line edits name it, so they never land on a file
    that changed since it was read."""
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:6]


# Versions agents have seen, so a line edit made against an older one can still land when
# its lines are untouched since (an agent edits, then edits again by the numbers it read).
_SEEN: OrderedDict[tuple[str, str], str] = OrderedDict()


def remember(p: Path, text: str, agent_id: str = "") -> str:
    v = version_of(text)
    _SEEN[(str(p), v)] = text
    _SEEN.move_to_end((str(p), v))
    while len(_SEEN) > 256:
        _SEEN.popitem(last=False)
    if agent_id:
        _LAST_SEEN[(agent_id, str(p))] = v
        while len(_LAST_SEEN) > 2048:
            _LAST_SEEN.pop(next(iter(_LAST_SEEN)))
    return v


# The version of each file each agent last read or wrote. An edit "from memory" failed most
# often on a file a colleague had changed since (a timed run: all 5 failed edits), and the
# error only said "not found"; now it says why, so the next step is a read, not a guess.
_LAST_SEEN: dict[tuple[str, str], str] = {}


def _why_missing(ctx: ToolContext, p: Path, version: str) -> str:
    seen = _LAST_SEEN.get((ctx.agent.id, str(p)))
    if seen is None:
        return (f" You haven't read {_rel(ctx, p)} (version {version}) yet: read the part you "
                "are changing, then edit from what you read.")
    if seen != version:
        return (f" {_rel(ctx, p)} changed since you last read it (you saw version {seen}; it is "
                f"{version} now, changed by a colleague or a command): read it again before "
                "editing.")
    return ""


def _carry_lines(then: str, now: str, spec: str) -> str | None:
    """Line range `spec` of `then`, as it is numbered in `now`; None if those lines changed."""
    m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", spec)
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2) or m.group(1))
    sm = difflib.SequenceMatcher(None, then.split("\n"), now.split("\n"), autojunk=False)
    for tag, i1, i2, j1, _j2 in sm.get_opcodes():
        if tag == "equal" and i1 <= a - 1 and b <= i2:
            return f"{a + j1 - i1}-{b + j1 - i1}"
    return None


@tool(
    "read_file",
    "Read a text file from your workspace, with line numbers and the file's version (for "
    "line edits). Read the part you need with offset/limit (line numbers); reading several "
    "files at once is faster than one by one. An image (.png, .jpg, .webp, .gif) is shown "
    "to you, e.g. a screenshot you took.",
    obj({"path": S, "offset": I, "limit": I}, ["path"]),
    category="files",
)
async def read_file(args: dict, ctx: ToolContext) -> str:
    p = resolve_in_workspace(ctx, args["path"])
    if not p.is_file():
        raise ToolError(f"no such file: {args['path']}")
    if p.suffix.lower() in IMAGES:
        return await asyncio.to_thread(_image, p, _rel(ctx, p))
    if p.stat().st_size > MAX_READ_BYTES:
        raise ToolError("file is too large to read (>2MB)")
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    offset = max(1, int(args.get("offset") or 1))
    limit = max(1, min(int(args.get("limit") or 400), 2000))
    chunk = lines[offset - 1 : offset - 1 + limit]
    head = (f"{_rel(ctx, p)} · lines {offset}-{offset - 1 + len(chunk)} of {len(lines)} · "
            f"version {remember(p, text, ctx.agent.id)}")
    body = "\n".join(f"{i:>5}  {line}" for i, line in enumerate(chunk, start=offset))
    more = len(lines) - (offset - 1 + len(chunk))
    return truncate(head + "\n" + body + (
        f"\n… {more} more lines (offset={offset + len(chunk)} to go on)" if more > 0 else ""))


IMAGES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
IMAGE_WIDTH, IMAGE_HEIGHT = 1600, 6000  # larger images are scaled down to fit


def _image(p: Path, name: str) -> ToolOutput:
    import base64
    import io

    from PIL import Image

    try:
        with Image.open(p) as im:
            w, h = im.size
            im = im.convert("RGB")
            # By width first: a tall full-page screenshot stays wide enough to read.
            scale = min(1.0, IMAGE_WIDTH / w, IMAGE_HEIGHT / h)
            if scale < 1:
                im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=70)
    except Exception as e:  # noqa: BLE001 - a broken image is the agent's to know about
        raise ToolError(f"couldn't open {name} as an image: {e}") from None
    url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    note = f" (shown at {scale:.0%})" if scale < 1 else ""
    return ToolOutput(f"{name} · image {w}×{h}{note}", [url])


@tool(
    "write_file",
    "Create or overwrite a file in your workspace (parent folders are created).",
    obj({"path": S, "content": S}, ["path", "content"]),
    category="files",
    side_effects=True,
)
async def write_file(args: dict, ctx: ToolContext) -> str:
    p = resolve_in_workspace(ctx, args["path"])
    guard.check(args["content"], [ctx.workspace])
    await _not_theirs(ctx, p)
    p.parent.mkdir(parents=True, exist_ok=True)
    existed = p.exists()
    p.write_text(args["content"], encoding="utf-8")
    _wrote(p)
    remember(p, args["content"], ctx.agent.id)
    from app.services import artifacts

    await artifacts.record(ctx, p)
    return f"{'Updated' if existed else 'Created'} {_rel(ctx, p)} ({len(args['content'])} chars)."


_EDIT = {"type": "object", "properties": {
    "old": {"type": "string", "description": "Text to replace, copied from the file"},
    "lines": {"type": "string", "description": "Or a line range to replace, e.g. \"40-52\" "
              "(needs `version`)"},
    "new": {"type": "string", "description": "Replacement text"}}, "required": ["new"]}


@tool(
    "edit_file",
    """Change a file. Give `old` (text copied from the file; it must occur once unless
replace_all) and `new`, or put several changes in `edits` (each matched against the file as
it was, so they must not overlap). A change can also replace a line range ("lines": "40-52")
when you pass the `version` that read_file showed. Small differences in trailing spaces,
quotes and dashes are tolerated; if `old` isn't found you get the closest passage. A
successful edit is applied: don't read the file again to check it.""",
    obj({"path": S, "old": S, "new": S, "replace_all": B, "version": S,
         "edits": {"type": "array", "items": _EDIT}}, ["path"]),
    category="files",
    side_effects=True,
)
async def edit_file(args: dict, ctx: ToolContext) -> str:
    p = resolve_in_workspace(ctx, args["path"])
    edits = list(args.get("edits") or [])
    if "old" in args or "new" in args:
        edits.append({"old": args.get("old"), "new": args.get("new")})
    if not edits:
        raise ToolError("nothing to change: give old and new, or edits")
    for e in edits:
        if not isinstance(e, dict) or not isinstance(e.get("new"), str):
            raise ToolError("each change needs `new` (and `old` or `lines`)")
        guard.check(e["new"], [ctx.workspace])
    if not p.is_file():
        raise ToolError(f"no such file: {args['path']}")
    await _not_theirs(ctx, p)
    raw = p.read_text(encoding="utf-8")
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    version = version_of(raw)
    spans: list[tuple[int, int, str]] = []  # character spans in `text`, replaced bottom-up
    failed: list[str] = []  # changes that couldn't be placed; the others still go in
    notes: list[str] = []
    for i, e in enumerate(edits):
        which = f"edits[{i}]" if len(edits) > 1 else "the change"
        if e.get("lines"):
            lines, given = str(e["lines"]), str(args.get("version") or "")
            if given != version:
                then = _SEEN.get((str(p), given))
                moved = _carry_lines(then.replace("\r\n", "\n"), text, lines) if then else None
                if moved is None:
                    failed.append(f"{which}: line edits need the file's current version "
                                  f"({version}); {_rel(ctx, p)} changed since you read it "
                                  f"{'(in those lines) ' if then else ''}or no version was "
                                  "given. Read the lines again.")
                    continue
                if moved.split("-")[0] != lines.split("-")[0].strip():
                    notes.append(f"{which}: lines {lines.strip()} of version {given} are lines "
                                 f"{moved} now, and were changed there")
                lines = moved
            spans.append(_line_span(text, lines, e["new"], which))
            continue
        old = (e.get("old") or "").replace("\r\n", "\n")
        if not old:
            raise ToolError(f"{which}: give `old` (text from the file) or `lines`")
        count = text.count(old)
        if count > 1 and args.get("replace_all") and len(edits) == 1:
            out = text.replace(old, e["new"])
            out = out.replace("\n", "\r\n") if crlf else out
            p.write_text(out, encoding="utf-8")
            _wrote(p)
            return (f"Edited {_rel(ctx, p)} ({count} replacements) · version "
                    f"{remember(p, out, ctx.agent.id)}.")
        if count > 1:
            failed.append(f"{which}: `old` occurs {count} times; add surrounding lines so it "
                          "is unique, or set replace_all")
            continue
        if count == 1:
            start = text.index(old)
            spans.append((start, start + len(old), e["new"]))
            continue
        # The span covers whole lines without the newlines around them; drop those `new`
        # carries because `old` had them.
        new = e["new"]
        new = new.removeprefix("\n") if old.startswith("\n") else new
        new = new.removesuffix("\n") if old.endswith("\n") else new
        span = _fuzzy_span(text, old)
        if span is not None:
            spans.append((*span, new))
            continue
        if len(new.strip()) >= 20 and text.count(new.strip("\n")) == 1:
            notes.append(f"{which} was already in the file (the new text is there), so it was "
                         "skipped")
            continue
        near = _nearest(text, old)
        if near and near.sure:
            # Off by a character or two (a stale copy, a typo): this is plainly the passage
            # meant, so change it and say what was different.
            spans.append((near.start, near.end, new))
            notes.append(f"{which} matched lines {near.first}-{near.last} {near.ratio:.0%}, not "
                         f"exactly, and was applied there; your `old` differed in:\n{near.diff}")
            continue
        failed.append(f"{which}: `old` was not found in {_rel(ctx, p)}."
                      + (_why_missing(ctx, p, version) + (_closest(near) if near else "")
                         or _closest(near)))
    spans.sort()
    for (_a0, a1, _), (b0, _b1, _) in zip(spans, spans[1:], strict=False):
        if b0 < a1:
            raise ToolError("two changes overlap; merge them into one")
    if failed and not spans:
        raise ToolError("\n".join([*failed, *notes]) + (
            "\nNothing was changed." if len(edits) > 1 else ""))
    final = version_of(raw)
    if spans:
        out = text
        for start, end, new in reversed(spans):
            out = out[:start] + new + out[end:]
        final_text = out.replace("\n", "\r\n") if crlf else out
        p.write_text(final_text, encoding="utf-8")
        _wrote(p)
        final = remember(p, final_text, ctx.agent.id)
    where = ", ".join(f"line {text.count(chr(10), 0, s) + 1}" for s, _, _ in spans)
    head = (f"Edited {_rel(ctx, p)}: {len(spans)} change{'s' * (len(spans) != 1)} ({where})"
            if spans else f"{_rel(ctx, p)} is unchanged")
    tail = "".join(f"\n- {n}" for n in notes)
    if failed:
        tail += (f"\nNOT applied ({len(failed)} of {len(edits)}; the rest are in, so send only "
                 "these again):\n" + "\n".join(failed))
    return f"{head} · version {final}.{tail}"


_FUZZ = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                       "–": "-", "—": "-", "−": "-", " ": " ",
                       " ": " ", " ": " "})


def _norm(line: str, indent: bool) -> str:
    line = line.translate(_FUZZ).rstrip()
    return line if indent else line.lstrip()


def _fuzzy_span(text: str, old: str) -> tuple[int, int] | None:
    """Where `old` is in `text` up to trailing spaces, typographic quotes and dashes (then
    also indentation), as whole lines; None unless exactly one place matches."""
    lines = text.split("\n")
    want = old.strip("\n").split("\n")
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)
    for indent in (True, False):
        target = [_norm(x, indent) for x in want]
        have = [_norm(x, indent) for x in lines]
        hits = [i for i in range(len(lines) - len(want) + 1) if have[i:i + len(want)] == target]
        if len(hits) == 1:
            i = hits[0]
            return starts[i], starts[i + len(want)] - 1
        if len(hits) > 1:
            return None
    return None


@dataclass
class _Near:
    ratio: float
    first: int  # 1-based lines first..last
    last: int
    start: int  # character span of those lines in the text, without the final newline
    end: int
    shown: str
    diff: str
    sure: bool  # close enough, and clearly ahead of any other place, to apply


SURE = 0.95  # how alike a passage must be to be changed although `old` isn't exact
CLEAR = 0.05  # ...and how far ahead of the next most alike place


def _nearest(text: str, old: str) -> _Near | None:
    """The passage most like `old` (same number of lines), or None if nothing is similar."""
    lines = text.split("\n")
    want = old.strip("\n").split("\n")
    n = max(1, len(want))
    if len(lines) < n:
        return None
    probe = "\n".join(_norm(x, False) for x in want)
    first = _norm(want[0], False)
    scored: list[tuple[float, int]] = []
    for i in range(len(lines) - n + 1):
        if first and difflib.SequenceMatcher(None, _norm(lines[i], False), first).quick_ratio() < 0.5:
            continue
        r = difflib.SequenceMatcher(None, "\n".join(_norm(x, False) for x in lines[i:i + n]),
                                    probe).ratio()
        scored.append((r, i))
    if not scored:
        return None
    best, at = max(scored)
    if best < 0.5:
        return None
    # The runner-up must be somewhere else, not the same passage shifted by a line.
    rest = [r for r, i in scored if abs(i - at) >= n]
    second = max(rest, default=0.0)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)
    found = lines[at:at + n]
    diff = "\n".join(x for x in difflib.unified_diff(want, found, "your old", "the file",
                                                       n=0, lineterm="")
                     if not x.startswith(("---", "+++", "@@")))[:800]
    big_enough = n >= 2 or len(old.strip()) >= 30  # a short line is too easy to mistake
    return _Near(best, at + 1, at + n, starts[at], starts[at + n] - 1,
                 "\n".join(f"{at + k + 1:>5}  {x}" for k, x in enumerate(found[:40])), diff,
                 best >= SURE and best - second >= CLEAR and big_enough)


def _closest(near: _Near | None) -> str:
    """The passage most like `old`, with line numbers, so the next try can copy it exactly."""
    if near is None:
        return " Nothing similar is there: read the file and copy the passage exactly."
    return (f" The closest passage ({near.ratio:.0%} alike) is lines {near.first}-{near.last}:\n"
            f"{near.shown}\nCopy from it exactly, or replace those lines with \"lines\" and the "
            "version.")


def _line_span(text: str, spec: str, new: str, which: str) -> tuple[int, int, str]:
    m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", spec)
    lines = text.split("\n")
    if not m:
        raise ToolError(f"{which}: lines must look like \"40-52\" or \"40\"")
    a, b = int(m.group(1)), int(m.group(2) or m.group(1))
    if not 1 <= a <= b <= len(lines):
        raise ToolError(f"{which}: lines {a}-{b} are outside the file (1-{len(lines)})")
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)
    # The span ends before line b's newline, which stays: `new` brings no trailing newline.
    return starts[a - 1], starts[b] - 1, new.removesuffix("\n")


@tool("list_dir", "List a directory in your workspace (default: your workdir).",
      obj({"path": S, "recursive": B}), category="files")
async def list_dir(args: dict, ctx: ToolContext) -> str:
    p = resolve_in_workspace(ctx, args.get("path") or ".")
    if not p.is_dir():
        raise ToolError(f"not a directory: {args.get('path')}")
    out: list[str] = []
    if args.get("recursive"):
        for root, dirs, files in os.walk(p):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for f in sorted(files):
                out.append(_rel(ctx, Path(root) / f))
                if len(out) >= 500:
                    break
            if len(out) >= 500:
                out.append("… (truncated at 500 entries)")
                break
    else:
        for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            out.append(_rel(ctx, child) + ("/" if child.is_dir() else ""))
    return "\n".join(out) or "(empty)"


@tool(
    "search_files",
    "Search file contents in your workspace with a regular expression.",
    obj({"pattern": S, "path": S, "glob": {"type": "string", "description": "e.g. *.py"},
         "max_results": I}, ["pattern"]),
    category="files",
)
async def search_files(args: dict, ctx: ToolContext) -> str:
    try:
        rx = re.compile(args["pattern"])
    except re.error as e:
        raise ToolError(f"invalid regex: {e}") from None
    base = resolve_in_workspace(ctx, args.get("path") or ".")
    if not base.exists():
        raise ToolError(f"no such file or folder: {args.get('path')}")
    glob = (args.get("glob") or "").replace("\\", "/")
    cap = max(1, min(int(args.get("max_results") or 100), 500))
    searched = [0]

    def wanted(fp: Path) -> bool:
        # "*.css" matches by name anywhere; "parts/*.css" or "**/x.js" by the path from the
        # search folder (or the workspace).
        if not glob:
            return True
        if "/" not in glob:
            return fnmatch.fnmatch(fp.name, glob) or any(
                fnmatch.fnmatch(fp.name, g) for g in _braces(glob))
        rel = [fp.relative_to(base).as_posix(), _rel(ctx, fp)]
        pats = [glob, glob.removeprefix("**/"), *_braces(glob)]
        return any(fnmatch.fnmatch(r, g) for r in rel for g in pats)

    def scan() -> list[str]:
        hits: list[str] = []
        if base.is_file():
            walk = [(str(base.parent), [], [base.name])]
        else:
            walk = os.walk(base)
        for root, dirs, files in walk:
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                fp = Path(root) / f
                if fp != base and not wanted(fp):
                    continue
                try:
                    if fp.stat().st_size > MAX_READ_BYTES:
                        continue
                    searched[0] += 1
                    with fp.open(encoding="utf-8", errors="strict") as fh:
                        for n, line in enumerate(fh, 1):
                            if rx.search(line):
                                hits.append(f"{_rel(ctx, fp)}:{n}: {line.rstrip()[:300]}")
                                if len(hits) >= cap:
                                    return hits
                except (UnicodeDecodeError, OSError):
                    continue
        return hits

    hits = await asyncio.to_thread(scan)
    if hits:
        return truncate("\n".join(hits))
    where = _rel(ctx, base) if base.is_file() else \
        f"{searched[0]} files under {_rel(ctx, base) or '.'}" + (f" matching {glob}" if glob else "")
    return f"No matches in {where}."


def _braces(glob: str) -> list[str]:
    """'*.{html,md}' as ['*.html', '*.md'] (fnmatch has no braces)."""
    m = re.search(r"\{([^{}]*)\}", glob)
    if not m:
        return []
    return [glob[:m.start()] + alt + glob[m.end():] for alt in m.group(1).split(",")]


def _mtimes(root: Path) -> dict[str, int]:
    """Workspace files and when each last changed (at most 20,000)."""
    out: dict[str, int] = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            fp = Path(base) / f
            try:
                out[fp.relative_to(root).as_posix()] = fp.stat().st_mtime_ns
            except OSError:
                continue
            if len(out) >= 20_000:
                return out
    return out


def _changed(root: Path, before: dict[str, int], since: float) -> list[str]:
    """Files a command made or changed: different from `before`, and not written by a
    file tool meanwhile (someone else's edit at the same moment)."""
    return [rel for rel, m in _mtimes(root).items() if before.get(rel) != m
            and _WRITTEN.get(str((root / rel).resolve()), 0) <= since][:200]


# Which shell runs a command on Windows. Agents mixed the two (`&&` in PowerShell, `@echo
# off` in a script PowerShell ran, bash heredocs): 10 of a run's 71 commands failed on
# syntax. A command now runs in the shell it's written for, and says which.
_PS_WORDS = re.compile(r"(?i)\b(get|set|new|remove|select|where|foreach|test|invoke|write|out|"
                       r"copy|move|start|stop|measure|convertto|convertfrom)-[a-z]+\b|\$env:|"
                       r"\$_\b|\$\(|\$[a-z_]\w*\s*=")
_BATCH_WORDS = re.compile(r"(?im)^\s*(@?echo\s+off|@?rem\b|set\s+\"?\w+=|if\s+(not\s+)?exist\b|"
                          r"goto\b|call\b|setlocal\b|for\s+%%)|%\w+%|%~")


def windows_shell(command: str) -> str:
    """'cmd', 'powershell' or 'batch' (several cmd lines, run as a .cmd file)."""
    multi = "\n" in command.strip()
    if _BATCH_WORDS.search(command) and not _PS_WORDS.search(command):
        return "batch" if multi else "cmd"
    if multi or _PS_WORDS.search(command):
        return "powershell"
    return "cmd"


_WRONG_SHELL = re.compile(
    r"is not a valid statement separator|Unexpected token|Missing file specification after "
    r"redirection|The ampersand \(&\) character is not allowed|is not recognized as (an "
    r"internal or external command|the name of a cmdlet)|Invalid switch|The syntax of the "
    r"command is incorrect|was unexpected at this time")


@tool(
    "run_command",
    """Run a shell command in your workdir (build, test, git, scripts…). Returns the
exit code and combined output. Long-running servers are not supported; the
command is killed after `timeout` seconds."""
    + ("""
On Windows the command runs in the shell it's written for, and the result says which: one
line in cmd.exe (&&, %VAR%, dir); PowerShell when it uses cmdlets or $variables
(Get-ChildItem, $env:X), or has several lines; several cmd lines (@echo off, set, %VAR%)
as a .cmd batch file. There is no bash: no heredocs (<<), no `export`. For more than a
line or two of Python or JavaScript, write a .py/.js file and run it.""" if sys.platform == "win32" else ""),
    obj({"command": S, "timeout": I, "cwd": S}, ["command"]),
    category="shell",
    approval="ask",
    timeout=900,
    side_effects=True,
)
async def run_command(args: dict, ctx: ToolContext) -> str:
    cwd = resolve_in_workspace(ctx, args.get("cwd") or ".")
    guard.check(args["command"], [ctx.workspace], cwd=cwd)
    if entry := egress.blocked_command(ctx.org, args["command"]):
        raise ToolError(f"{entry} is blocked for this organization (settings.blocked_hosts)")
    if tool_for := only_reads_files(args["command"]):
        raise ToolError(f"This command only reads files: use {tool_for} instead (no approval, "
                        "line numbers and the file's version for edits). Keep run_command for "
                        "building, testing and running things.")
    timeout = max(1, min(int(args.get("timeout") or settings.shell_timeout_seconds), 900))
    env = {k: v for k, v in os.environ.items() if not k.startswith("PANTHEON_")}
    env["PANTHEON_AGENT"] = ctx.agent.name
    env.update(await secrets.shell_env(ctx.org.id, ctx.agent.id, args["command"]))
    kwargs: dict = {}
    if sys.platform != "win32":
        kwargs["start_new_session"] = True
    from app.services import flows

    command, script, shell = args["command"], None, ""
    if sys.platform == "win32":
        shell = windows_shell(command)
        # cmd.exe runs only the first line (a multi-line `python -c "…"` silently does
        # nothing), so several lines run as a script: PowerShell keeps quoted lines together.
        if shell == "powershell":
            fd, script = tempfile.mkstemp(suffix=".ps1", prefix="pantheon-")
            with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
                f.write(command)
            command = f'powershell -NoProfile -ExecutionPolicy Bypass -File "{script}"'
        elif shell == "batch":
            fd, script = tempfile.mkstemp(suffix=".cmd", prefix="pantheon-")
            with os.fdopen(fd, "w", encoding="utf-8", newline="\r\n") as f:
                f.write(command)
            command = f'cmd /d /c "{script}"'
    root = Path(ctx.workspace).resolve()
    in_step = await flows.running_step_for(ctx.agent.id, ctx.org.id) is not None
    times = await asyncio.to_thread(_mtimes, root) if in_step else {}
    theirs = await _snapshot_theirs(ctx)
    began = time.monotonic()
    proc = await asyncio.create_subprocess_shell(
        command, cwd=str(cwd), env=env, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **kwargs,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        code = proc.returncode
        timed_out = False
    except TimeoutError:
        timed_out = True
        _kill(proc)
        out, code = b"", None
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), 5)
        except (TimeoutError, Exception):  # noqa: BLE001
            pass
    if script:
        Path(script).unlink(missing_ok=True)
    text = out.decode("utf-8", errors="replace")
    head = f"exit code: {code}" if not timed_out else f"TIMED OUT after {timeout}s (killed)"
    if shell:
        head += f" (ran in {'cmd.exe as a .cmd file' if shell == 'batch' else shell})"
    if shell and code and _WRONG_SHELL.search(text):
        text += ("\n[That looks like syntax for another shell. This ran in "
                 + ("PowerShell: use ; or `if ($?) { … }` instead of &&, $env:X instead of %X%, "
                    "and no @echo or <<." if shell == "powershell" else
                    "cmd.exe: use && and %X%, no cmdlets, $variables or <<.")
                 + " For more than a line or two of code, write a .py/.js file and run it.]")
    back: list[str] = []
    if theirs and (back := await asyncio.to_thread(_put_back, theirs, root, began)):
        text += (f"\n[Pantheon put back {', '.join(back[:8])}"
                 + (f" and {len(back) - 8} more" if len(back) > 8 else "")
                 + ": the command changed files that other people's flow steps own. Make only "
                 "your step's files, and put what theirs need in your step's result.]")
    if in_step:  # the step's record of what it changed includes what its commands changed
        changed = [r for r in await asyncio.to_thread(_changed, root, times, began)
                   if r not in back]
        if changed:
            await flows.note_made(ctx.org.id, ctx.agent.id, changed, claim=False)
    return truncate(f"{head}\n{text}")


# Shell commands that only do what a file tool does. A timed run used the shell 94 times,
# mostly findstr, dir and type on the agents' own files, though the prompt said not to; omp
# blocks such commands outright. Only a command made of nothing else is refused: a build
# that also lists a folder runs.
_READERS = {
    "read_file": ("cat", "type", "more", "head", "tail", "less", "get-content", "gc"),
    "search_files": ("grep", "rg", "findstr", "select-string", "sls", "ag", "ack"),
    "list_dir": ("ls", "dir", "tree", "get-childitem", "gci"),
}
_NEUTRAL = ("cd", "chdir", "echo", "@echo", "rem", "@rem", "cls", "set-location", "pushd", "popd")


def only_reads_files(command: str) -> str:
    """The file tool to use instead, when every step of `command` only reads files."""
    steps = [s.strip() for s in re.split(r"&&|\|\||;|(?<!\|)&(?!&)|\r?\n", command) if s.strip()]
    tools: list[str] = []
    for step in steps:
        for part in step.split("|"):  # `type x | node` does work; `dir | findstr x` doesn't
            first = part.strip().split(maxsplit=1)
            word = first[0].lower().strip('"').removesuffix(".exe") if first else ""
            if not word or word in _NEUTRAL:
                continue
            tool_for = next((t for t, words in _READERS.items() if word in words), "")
            if not tool_for:
                return ""  # a step that does real work: the command runs
            tools.append(tool_for)
    return " / ".join(dict.fromkeys(tools))


def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        if sys.platform == "win32":
            os.system(f"taskkill /F /T /PID {proc.pid} >NUL 2>&1")  # noqa: S605
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass
