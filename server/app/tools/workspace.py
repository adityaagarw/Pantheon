"""Workspace tools: files and shell in the org's host workspace.

Paths are relative to the agent's workdir and can never leave the org
workspace (which itself must live under PANTHEON_WORKSPACE_ROOTS).
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
import signal
import sys
from pathlib import Path

from app.core.config import settings
from app.services import secrets
from app.tools.base import (
    B,
    I,
    S,
    ToolContext,
    ToolError,
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


@tool(
    "read_file",
    "Read a text file from your workspace. Use offset/limit (line numbers) for big files.",
    obj({"path": S, "offset": I, "limit": I}, ["path"]),
    category="files",
)
async def read_file(args: dict, ctx: ToolContext) -> str:
    p = resolve_in_workspace(ctx, args["path"])
    if not p.is_file():
        raise ToolError(f"no such file: {args['path']}")
    if p.stat().st_size > MAX_READ_BYTES:
        raise ToolError("file is too large to read (>2MB)")
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    offset = max(1, int(args.get("offset") or 1))
    limit = max(1, min(int(args.get("limit") or 400), 2000))
    chunk = lines[offset - 1 : offset - 1 + limit]
    body = "\n".join(f"{i:>5}  {line}" for i, line in enumerate(chunk, start=offset))
    more = len(lines) - (offset - 1 + len(chunk))
    return truncate(body + (f"\n… {more} more lines" if more > 0 else ""))


@tool(
    "write_file",
    "Create or overwrite a file in your workspace (parent folders are created).",
    obj({"path": S, "content": S}, ["path", "content"]),
    category="files",
    side_effects=True,
)
async def write_file(args: dict, ctx: ToolContext) -> str:
    p = resolve_in_workspace(ctx, args["path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    existed = p.exists()
    p.write_text(args["content"], encoding="utf-8")
    from app.services import artifacts

    await artifacts.record(ctx, p)
    return f"{'Updated' if existed else 'Created'} {_rel(ctx, p)} ({len(args['content'])} chars)."


@tool(
    "edit_file",
    """Replace an exact snippet in a file. `old` must match exactly once unless
replace_all is true. Read the file first so the snippet is exact.""",
    obj({"path": S, "old": S, "new": S, "replace_all": B}, ["path", "old", "new"]),
    category="files",
    side_effects=True,
)
async def edit_file(args: dict, ctx: ToolContext) -> str:
    p = resolve_in_workspace(ctx, args["path"])
    if not p.is_file():
        raise ToolError(f"no such file: {args['path']}")
    text = p.read_text(encoding="utf-8")
    count = text.count(args["old"])
    if count == 0:
        raise ToolError("the `old` snippet was not found; read the file and copy it exactly")
    if count > 1 and not args.get("replace_all"):
        raise ToolError(f"the `old` snippet occurs {count} times; add context or set replace_all")
    p.write_text(text.replace(args["old"], args["new"]), encoding="utf-8")
    return f"Edited {_rel(ctx, p)} ({count} replacement{'s' if count > 1 else ''})."


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
    glob = args.get("glob")
    cap = max(1, min(int(args.get("max_results") or 100), 500))

    def scan() -> list[str]:
        hits: list[str] = []
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                if glob and not fnmatch.fnmatch(f, glob):
                    continue
                fp = Path(root) / f
                try:
                    if fp.stat().st_size > MAX_READ_BYTES:
                        continue
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
    return truncate("\n".join(hits)) if hits else "No matches."


@tool(
    "run_command",
    """Run a shell command in your workdir (build, test, git, scripts…). Returns the
exit code and combined output. Long-running servers are not supported; the
command is killed after `timeout` seconds.""",
    obj({"command": S, "timeout": I, "cwd": S}, ["command"]),
    category="shell",
    approval="ask",
    timeout=900,
    side_effects=True,
)
async def run_command(args: dict, ctx: ToolContext) -> str:
    cwd = resolve_in_workspace(ctx, args.get("cwd") or ".")
    timeout = max(1, min(int(args.get("timeout") or settings.shell_timeout_seconds), 900))
    env = {k: v for k, v in os.environ.items() if not k.startswith("PANTHEON_")}
    env["PANTHEON_AGENT"] = ctx.agent.name
    env.update(await secrets.shell_env(ctx.org.id, ctx.agent.id, args["command"]))
    kwargs: dict = {}
    if sys.platform != "win32":
        kwargs["start_new_session"] = True
    proc = await asyncio.create_subprocess_shell(
        args["command"], cwd=str(cwd), env=env, stdin=asyncio.subprocess.DEVNULL,
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
    text = out.decode("utf-8", errors="replace")
    head = f"exit code: {code}" if not timed_out else f"TIMED OUT after {timeout}s (killed)"
    return truncate(f"{head}\n{text}")


def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        if sys.platform == "win32":
            os.system(f"taskkill /F /T /PID {proc.pid} >NUL 2>&1")  # noqa: S605
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass
