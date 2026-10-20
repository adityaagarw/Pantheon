"""What Kronos can do on this computer (Pantheon Host, built into the app): files in the
folders the user allowed, and changes to Pantheon's own code in git worktrees that are
checked before they're merged.

Kronos can always read Pantheon's source (except secrets) when Pantheon runs from it, and
changes code only in worktrees under .kronos/work. Anything else must be in the allowed
folders (Settings → This computer; app/host/__init__.py keeps the list). An installed
Pantheon has no source to change, so the code_* tools say so.
"""

from __future__ import annotations

import fnmatch
import json
import os
import platform
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path

from app.host import REPO, allowed_roots, default_root

WORK = (REPO or Path.home()) / ".kronos" / "work"
CHANGES = (REPO or Path.home()) / ".kronos" / "changes.json"

SKIP_DIRS = {".git", "node_modules", ".venv", ".next", "__pycache__", ".kronos", ".pytest_cache",
             ".ruff_cache", ".mypy_cache", "dist", "build"}
# Never readable, wherever they are: keys, tokens, env files.
SECRET_PARTS = [("engine", ".secrets"), ("engine", ".env")]
OUTPUT_CAP = 24_000
_lock = threading.Lock()


class Refused(Exception):
    """An operation the runner won't do; the message goes back to Kronos."""


# --- paths ----------------------------------------------------------------------------------


def inside(p: Path, root: Path) -> bool:
    try:
        p.relative_to(root)
        return True
    except ValueError:
        return False


def resolve(path: str) -> Path:
    if not path:
        raise Refused("path is required")
    p = Path(path).expanduser()
    base = REPO if REPO else (allowed_roots() or [Path.home()])[0]
    p = (p if p.is_absolute() else base / p).resolve()
    if not (REPO and inside(p, REPO)) and not any(inside(p, r) for r in allowed_roots()):
        raise Refused(f"{p} is outside the folders you may use (the user can allow one in "
                      "Settings → This computer)")
    return p


def need_repo() -> Path:
    if REPO is None:
        raise Refused("Pantheon isn't running from its source code here (it's installed), so "
                      "there's no code to change: code changes work when it runs from a "
                      "checkout of its repository")
    return REPO


def is_secret(p: Path) -> bool:
    name = p.name.lower()
    if name == ".env" or (name.startswith(".env.") and not name.endswith((".example", ".sample"))):
        return True
    if REPO and inside(p, REPO):
        rel = p.relative_to(REPO).parts
        return any(rel[:len(s)] == s for s in SECRET_PARTS)
    return False


def readable(path: str) -> Path:
    p = resolve(path)
    if is_secret(p):
        raise Refused(f"{p.name} holds secrets; Kronos never reads it")
    return p


def writable(path: str) -> Path:
    p = readable(path)
    if REPO and inside(p, REPO):
        if not inside(p, WORK) or p.parent == WORK:
            raise Refused("the live Pantheon repo is read-only to you: start a change with "
                          "code_start and edit the files in its worktree")
        if ".git" in p.relative_to(WORK).parts:
            raise Refused("git internals are off limits")
    return p


def cap(text: str, limit: int = OUTPUT_CAP) -> str:
    if len(text) <= limit:
        return text
    return text[: limit // 3] + f"\n…[{len(text) - limit} characters omitted]…\n" + text[-limit * 2 // 3:]


def sh(cmd: list[str] | str, cwd: Path, timeout: float = 600, env: dict | None = None
       ) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, cwd=str(cwd), shell=isinstance(cmd, str), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           env={**os.environ, **(env or {})}, check=False)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + (e.stderr or "")
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        return 124, out + f"\n[timed out after {int(timeout)} s]"
    except FileNotFoundError as e:
        return 127, str(e)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def git(*args: str, cwd: Path | None = None, timeout: float = 120) -> str:
    code, out = sh(["git", *args], cwd or need_repo(), timeout)
    if code:
        raise Refused(f"git {' '.join(args)} failed: {out.strip()[-1500:]}")
    return out.strip()


# --- file operations ------------------------------------------------------------------------


def op_info(args: dict) -> str:
    try:
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
    except Refused:
        branch = "?"
    changes = [c for c in load_changes() if c["status"] == "open"]
    return json.dumps({
        "repo": str(REPO) if REPO else None, "branch": branch, "allowed": [str(r) for r in allowed_roots()],
        "os": f"{platform.system()} {platform.release()}", "python": platform.python_version(),
        "openChanges": [{k: c[k] for k in ("id", "title", "path")} for c in changes],
    }, indent=1)


def op_read(args: dict) -> str:
    p = readable(args["path"])
    if p.is_dir():
        raise Refused(f"{p} is a folder; use host_list")
    data = p.read_bytes()
    if b"\0" in data[:4096]:
        raise Refused(f"{p.name} is a binary file ({len(data)} bytes)")
    lines = data.decode("utf-8", "replace").splitlines()
    start = max(int(args.get("offset") or 1), 1)
    limit = min(int(args.get("limit") or 400), 2000)
    chunk = lines[start - 1: start - 1 + limit]
    body = "\n".join(f"{i:>5}\t{line}" for i, line in enumerate(chunk, start))
    more = len(lines) - (start - 1 + len(chunk))
    return cap(body + (f"\n…{more} more lines (use offset)" if more > 0 else ""))


def op_list(args: dict) -> str:
    p = readable(args["path"])
    if not p.is_dir():
        raise Refused(f"{p} is not a folder")
    rows = []
    for e in sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))[:500]:
        rows.append(f"{e.name}/" if e.is_dir() else f"{e.name}  ({e.stat().st_size} bytes)")
    return f"{p}\n" + "\n".join(rows)


def op_search(args: dict) -> str:
    root = readable(args.get("path") or str(default_root()))
    try:
        rx = re.compile(args["pattern"])
    except re.error as e:
        raise Refused(f"bad regular expression: {e}") from None
    glob = args.get("glob") or ""
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            f = Path(dirpath) / name
            rel = f.relative_to(root).as_posix()
            if glob and not (fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(name, glob)):
                continue
            if is_secret(f):
                continue
            try:
                if f.stat().st_size > 2_000_000:
                    continue
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{rel}:{i}: {line.strip()[:200]}")
                    if len(hits) >= 200:
                        return "\n".join(hits) + "\n…(stopped at 200 matches)"
    return "\n".join(hits) or "no matches"


def op_write(args: dict) -> str:
    p = writable(args["path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(args["content"])
    return f"wrote {p} ({len(args['content'])} characters)"


def op_edit(args: dict) -> str:
    p = writable(args["path"])
    text = p.read_text(encoding="utf-8")
    n = text.count(args["old"])
    if n == 0:
        raise Refused("the text to replace wasn't found (read the file again and copy it exactly)")
    if n > 1 and not args.get("replace_all"):
        raise Refused(f"the text appears {n} times; include more context or set replace_all")
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text.replace(args["old"], args["new"]))
    return f"edited {p} ({n} replacement{'s' if n > 1 else ''})"


def op_run(args: dict) -> str:
    cwd = readable(args.get("cwd") or str(default_root()))
    code, out = sh(args["command"], cwd, float(args.get("timeout") or 300))
    return cap(f"exit code {code}\n{out}")


# --- code changes ---------------------------------------------------------------------------


def load_changes() -> list[dict]:
    try:
        return json.loads(CHANGES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_change(change: dict) -> None:
    with _lock:
        changes = [c for c in load_changes() if c["id"] != change["id"]] + [change]
        CHANGES.parent.mkdir(parents=True, exist_ok=True)
        CHANGES.write_text(json.dumps(changes, indent=1), encoding="utf-8")


def get_change(cid: str) -> dict:
    for c in load_changes():
        if c["id"] == cid and c["status"] == "open":
            return c
    raise Refused(f"no open change '{cid}' (see code_changes)")


def link_node_modules(wt: Path) -> None:
    """Share the repo's ui/node_modules with the worktree (a junction on Windows)."""
    src, dst = need_repo() / "ui" / "node_modules", wt / "ui" / "node_modules"
    if not src.is_dir() or dst.exists():
        return
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(src), str(dst))
    else:
        dst.symlink_to(src, target_is_directory=True)


def unlink_node_modules(wt: Path) -> None:
    dst = wt / "ui" / "node_modules"
    if os.name == "nt" and dst.exists():
        os.rmdir(dst)  # removes the junction only, never the shared folder's contents
    elif dst.is_symlink():
        dst.unlink()


def remove_worktree(change: dict) -> None:
    wt = Path(change["path"])
    unlink_node_modules(wt)
    if wt.exists():
        sh(["git", "worktree", "remove", "--force", str(wt)], need_repo())
    sh(["git", "worktree", "prune"], need_repo())


def op_change_start(args: dict) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", args["title"].lower()).strip("-")[:32] or "change"
    cid = f"{slug}-{secrets.token_hex(2)}"
    need_repo()
    wt = WORK / cid
    base = git("rev-parse", "HEAD")
    branch_from = git("rev-parse", "--abbrev-ref", "HEAD")
    WORK.mkdir(parents=True, exist_ok=True)
    git("worktree", "add", "-b", f"kronos/{cid}", str(wt), base, timeout=300)
    link_node_modules(wt)
    save_change({"id": cid, "title": args["title"], "branch": f"kronos/{cid}", "path": str(wt),
                 "base": base, "from": branch_from, "status": "open", "created": time.time()})
    dirty = git("status", "--porcelain")
    note = ("\nNote: the user has uncommitted work in the repo; it isn't in this worktree, and "
            "apply will stop if your change touches the same files." if dirty else "")
    return (f"Change {cid} started at {wt} (branch kronos/{cid}, from {branch_from} at "
            f"{base[:8]}). Edit files under that folder.{note}")


def op_change_list(args: dict) -> str:
    rows = [f"{c['id']}  [{c['status']}]  {c['title']}  ({c['path']})" for c in load_changes()[-30:]]
    return "\n".join(rows) or "no changes yet"


def changed_files(change: dict) -> list[str]:
    wt = Path(change["path"])
    git("add", "-A", cwd=wt)
    out = git("diff", "--cached", "--name-only", change["base"], cwd=wt)
    return [line for line in out.splitlines() if line]


def op_change_diff(args: dict) -> str:
    change = get_change(args["change"])
    wt = Path(change["path"])
    files = changed_files(change)
    if not files:
        return "no changes yet"
    stat = git("diff", "--cached", "--stat", change["base"], cwd=wt)
    diff = git("diff", "--cached", change["base"], cwd=wt)
    return cap(stat + "\n\n" + diff)


def engine_python() -> list[str] | None:
    """The engine's Python (its .venv in the source), to check a worktree's engine code with
    (run from the worktree, so its code is what's imported)."""
    for rel in ("engine/.venv/Scripts/python.exe", "engine/.venv/bin/python"):
        if (need_repo() / rel).exists():
            return [str(need_repo() / rel)]
    return None


def run_checks(change: dict, scope: str) -> tuple[bool, str]:
    wt = Path(change["path"])
    files = changed_files(change)
    if not files:
        return False, "nothing to check: the change has no edits yet"
    steps: list[tuple[str, list[str] | str, Path, float]] = []
    py = engine_python()
    if any(f.startswith(("engine/", "packs/")) for f in files):
        if py is None:
            return False, "can't check engine code: the engine has no .venv (run uv sync in engine/)"
        steps.append(("ruff", [*py, "-m", "ruff", "check", "app", "tests"], wt / "engine", 300))
        if scope == "full":
            steps.append(("pytest", [*py, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"],
                          wt / "engine", 2000))
    ui_files = [f[len("ui/"):] for f in files if f.startswith("ui/")
                and f.endswith((".ts", ".tsx", ".js", ".mjs")) and (wt / f).exists()]
    if any(f.startswith("ui/") for f in files):
        if not (wt / "ui" / "node_modules").exists():
            return False, "can't check the interface: run npm install in ui/ first"
        steps.append(("typescript", "npx tsc --noEmit -p .", wt / "ui", 600))
        if ui_files:
            steps.append(("eslint", "npx eslint --quiet " + " ".join(f'"{f}"' for f in ui_files),
                          wt / "ui", 600))
    if not steps:
        return True, f"{len(files)} file(s) changed, none with automated checks"
    report, ok = [], True
    for name, cmd, cwd, timeout in steps:
        code, out = sh(cmd, cwd, timeout)
        report.append(f"## {name}: {'PASS' if code == 0 else 'FAIL'}\n{out.strip()[-6000:]}")
        if code:
            ok = False
            break
    return ok, cap("\n\n".join(report))


def op_change_check(args: dict) -> str:
    ok, report = run_checks(get_change(args["change"]), args.get("scope") or "quick")
    return ("All checks passed.\n\n" if ok else "Checks failed.\n\n") + report


def uncommitted() -> set[str]:
    """Paths with uncommitted edits in the user's checkout."""
    _, out = sh(["git", "status", "--porcelain", "-z"], need_repo())
    paths, entries = set(), iter(out.split("\0"))
    for e in entries:
        if len(e) > 3:
            paths.add(e[3:])
            if e[0] in "RC":  # a rename or copy is followed by its original path
                paths.add(next(entries, ""))
    return paths


def op_change_apply(args: dict) -> str:
    change = get_change(args["change"])
    wt = Path(change["path"])
    files = changed_files(change)
    if not files:
        raise Refused("the change has no edits to apply")
    ok, report = run_checks(change, "full")
    if not ok:
        return "Not applied: the checks failed. Fix them and apply again.\n\n" + report
    git("-c", "user.name=Kronos", "-c", "user.email=kronos@pantheon.local", "commit", "-q",
        "-m", args["message"], cwd=wt)
    clash = sorted(set(files) & uncommitted())
    if clash:
        return ("Checks passed and the change is committed on its branch, but not merged: the "
                "user has uncommitted edits in " + ", ".join(clash) + ". Ask them to commit or "
                "stash those, then apply again.")
    code, out = sh(["git", "merge", "--no-ff", "--no-edit", "-m",
                    f"Kronos: {change['title']}", change["branch"]], need_repo(), 120)
    if code:
        sh(["git", "merge", "--abort"], need_repo())
        return f"Checks passed but merging failed, so nothing changed:\n{out.strip()[-3000:]}"
    head = git("rev-parse", "--short", "HEAD")
    remove_worktree(change)
    sh(["git", "branch", "-D", change["branch"]], need_repo())
    change["status"] = "applied"
    save_change(change)
    # The interface reloads by itself in development; the engine runs what it started with.
    tail = (" The engine's code changed: it takes effect when Pantheon restarts (tell the user)."
            if any(f.startswith(("engine/", "packs/")) for f in files) else "")
    return (f"Applied: merged into the user's branch as {head} ({len(files)} files).{tail}\n\n"
            + report[-2000:])


def op_change_discard(args: dict) -> str:
    change = get_change(args["change"])
    remove_worktree(change)
    sh(["git", "branch", "-D", change["branch"]], need_repo())
    change["status"] = "discarded"
    save_change(change)
    return f"Discarded {change['id']}."


OPS = {"info": op_info, "read": op_read, "list": op_list, "search": op_search, "write": op_write,
       "edit": op_edit, "run": op_run, "change_start": op_change_start,
       "change_list": op_change_list, "change_diff": op_change_diff,
       "change_check": op_change_check, "change_apply": op_change_apply,
       "change_discard": op_change_discard}
