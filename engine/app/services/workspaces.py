"""Workspaces and worktrees: where an organization's agents work, set up when it's made.

An organization's workspace is a new folder, a folder the user picked on this computer,
or a git repository cloned from a URL. With git on (``settings.git``), it's a git
repository whose main checkout is the base branch, and each agent that writes files and
leads nobody works in its own worktree on its own branch:

    <workspace>/                          base branch (leads work and merge here)
    <workspace>/.pantheon/worktrees/ada/  branch agent/ada
    <workspace>/.pantheon/worktrees/ben/  branch agent/ben

Agents commit in their worktree (git_commit), bring in the base branch (git_update), and
their lead reviews and merges their branches (team_work, merge_work). ``.pantheon/`` is
excluded through .git/info/exclude, so the user's own files are never changed for it.

Folders the user picks are remembered as allowed workspace roots (agents can't add any).
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.events import bus
from app.models import Agent, Org, Relationship, Setting

WORKTREES = ".pantheon/worktrees"
BRANCH_PREFIX = "agent/"
FOLDERS_KEY = "workspace.folders"  # Setting: folders the user picked, allowed as roots
# Tools that change files: an agent with any of these gets a worktree.
WRITERS = ("write_file", "edit_file", "run_command", "computer_shell")
GIT_TIMEOUT = 300
IDENTITY = ("-c", "user.name=Pantheon", "-c", "user.email=pantheon@localhost")

_folders: list[Path] = []


class WorkspaceSetupError(ValueError):
    pass


# --- folders the user picked ----------------------------------------------------------------


def picked_folders() -> list[Path]:
    return list(_folders)


async def load_folders() -> None:
    """At startup: the folders the user picked, and which orgs use git."""
    async with SessionLocal() as session:
        row = await session.get(Setting, FOLDERS_KEY)
        orgs = list((await session.execute(select(Org))).scalars())
    _folders.clear()
    _folders.extend(Path(p) for p in (row.value if row else []) or [])
    _git_orgs.clear()
    _git_orgs.update(o.id for o in orgs if git_settings(o)["enabled"] and git_settings(o)["worktrees"])


async def allow_folder(path: Path) -> None:
    """The user picked this folder: agents of the org it's given to may work in it."""
    path = path.resolve()
    if path in _folders:
        return
    _folders.append(path)
    async with SessionLocal() as session:
        row = await session.get(Setting, FOLDERS_KEY)
        value = [str(p) for p in _folders]
        if row is None:
            session.add(Setting(key=FOLDERS_KEY, value=value))
        else:
            row.value = value
        await session.commit()


# --- git ------------------------------------------------------------------------------------


class GitError(RuntimeError):
    pass


async def git(cwd: Path, *args: str, check: bool = True, timeout: float = GIT_TIMEOUT) -> str:
    """Run git in a folder; returns stdout (stderr too on failure, in the error)."""
    if shutil.which("git") is None:
        raise GitError("git isn't installed on this computer (https://git-scm.com)")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    proc = await asyncio.create_subprocess_exec(
        "git", *IDENTITY, *args, cwd=str(cwd), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        raise GitError(f"git {args[0]} took longer than {int(timeout)} s") from None
    text = out.decode("utf-8", "replace")
    if check and proc.returncode != 0:
        raise GitError((err.decode("utf-8", "replace") or text).strip() or f"git {args[0]} failed")
    return text


def git_settings(org: Org) -> dict[str, Any]:
    """{"enabled", "worktrees", "base"} (enabled False when the org doesn't use git)."""
    g = (org.settings or {}).get("git") or {}
    return {"enabled": bool(g.get("enabled")), "worktrees": bool(g.get("worktrees", True)),
            "base": str(g.get("base") or "main")}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "agent"


async def _ensure_repo(root: Path) -> str:
    """A git repository at root (made if it isn't one, with what's there as its first
    commit); returns the base branch."""
    if not (root / ".git").exists():
        await git(root, "init", "-b", "main")
    exclude = root / ".git" / "info" / "exclude"
    if exclude.parent.is_dir():
        text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if ".pantheon/" not in text.splitlines():
            exclude.write_text(text + ("" if text.endswith("\n") or not text else "\n")
                               + ".pantheon/\n", encoding="utf-8")
    if not (await git(root, "rev-parse", "--verify", "HEAD", check=False)).strip():
        await git(root, "add", "-A")
        await git(root, "commit", "--allow-empty", "-m", "Pantheon: the starting point")
    return (await git(root, "rev-parse", "--abbrev-ref", "HEAD")).strip() or "main"


# --- setting up an organization's workspace ---------------------------------------------------


async def setup(org_id: str, spec: dict[str, Any]) -> Org:
    """Give an organization its workspace.

    ``spec``: {"source": "new" | "folder" | "clone" | "current", "path": folder (for "folder"),
    "url": repository (for "clone"), "git": bool (default true), "worktrees": bool
    (default true)}."""
    from app.services import orgs
    from app.tools.base import check_workspace_path

    async with SessionLocal() as session:
        org = await session.get(Org, org_id)
    if org is None:
        raise WorkspaceSetupError("organization not found")
    source = spec.get("source") or "new"
    use_git = bool(spec.get("git", True)) or source == "clone"
    if source == "current":  # the folder it has now (to turn git and worktrees on later)
        from app.tools.base import org_workspace

        root = org_workspace(org)
    elif source == "folder":
        root = Path(str(spec.get("path") or "")).expanduser()
        if not root.is_absolute() or not root.is_dir():
            raise WorkspaceSetupError(f"no folder {root} on this computer")
        await allow_folder(root)
    elif source in ("new", "clone"):
        name = slug(org.name) or "org"
        root = settings.workspace_root_paths[0] / f"{name}-{org.id[-6:]}"
        if root.exists() and any(root.iterdir()):
            raise WorkspaceSetupError(f"{root} already exists and isn't empty")
        root.mkdir(parents=True, exist_ok=True)
        if source == "clone":
            url = str(spec.get("url") or "").strip()
            if not re.match(r"^(https?://|git@|ssh://|file://)\S+$", url):
                raise WorkspaceSetupError("give the repository's URL (https://…, git@… or ssh://…)")
            try:
                await git(root.parent, "clone", "--", url, str(root), timeout=900)
            except GitError as e:
                shutil.rmtree(root, ignore_errors=True)
                raise WorkspaceSetupError(f"couldn't clone {url}: {e}") from None
    else:
        raise WorkspaceSetupError("source must be new, folder, clone or current")
    root = check_workspace_path(root)

    git_cfg: dict[str, Any] = {"enabled": False}
    if use_git:
        try:
            base = await _ensure_repo(root)
        except GitError as e:
            raise WorkspaceSetupError(f"couldn't set up git in {root}: {e}") from None
        git_cfg = {"enabled": True, "worktrees": bool(spec.get("worktrees", True)), "base": base}
    org = await orgs.update_org(org_id, {"workspace": str(root), "settings": {"git": git_cfg}})
    await sync_worktrees(org_id)
    return org


# --- worktrees --------------------------------------------------------------------------------


def _writes(agent: Agent) -> bool:
    names = {str(t.get("name") if isinstance(t, dict) else t) for t in agent.tools or []}
    denied = {str(t.get("name")) for t in agent.tools or []
              if isinstance(t, dict) and t.get("approval") == "deny"}
    return any(w in names and w not in denied for w in WRITERS)


async def sync_worktrees(org_id: str) -> list[str]:
    """Give each agent that writes files and leads nobody its own worktree and branch.
    Leads work in the main checkout. An existing worktree is never taken away, so no work
    is lost when someone becomes a lead. Returns the names of agents that got one."""
    from app.tools.base import org_workspace

    async with SessionLocal() as session:
        org = await session.get(Org, org_id)
        if org is None:
            return []
        g = git_settings(org)
        if not (g["enabled"] and g["worktrees"]):
            _git_orgs.discard(org_id)
            return []
        _git_orgs.add(org_id)
        agents = list((await session.execute(select(Agent).where(
            Agent.org_id == org_id))).scalars())
        leads = set((await session.execute(select(Relationship.from_id).where(
            Relationship.org_id == org_id, Relationship.kind == "manages"))).scalars())
    root = org_workspace(org)
    made: list[str] = []
    for a in agents:
        if a.worktree or a.is_supervisor or a.id in leads or not _writes(a):
            continue
        rel = f"{WORKTREES}/{slug(a.name)}"
        branch = f"{BRANCH_PREFIX}{slug(a.name)}"
        path = root / rel
        try:
            if not path.exists():
                exists = (await git(root, "branch", "--list", branch)).strip()
                await git(root, "worktree", "add", *([] if exists else ["-b", branch]),
                          str(path), *([branch] if exists else [g["base"]]))
        except GitError as e:
            await bus.publish("workspace.error", {"agentId": a.id, "error": str(e)}, org_id=org_id)
            continue
        async with SessionLocal() as session:
            row = await session.get(Agent, a.id)
            if row is not None and not row.worktree:
                row.worktree = rel
                await session.commit()
        made.append(a.name)
    if made:
        await bus.publish("workspace.worktrees", {"made": made}, org_id=org_id)
    return made


_pending: dict[str, asyncio.Task[None]] = {}
# Orgs known to use git (filled as they're set up or synced), so a burst of changes in
# an org without git costs nothing.
_git_orgs: set[str] = set()
SYNC_DELAY = 2.0


def schedule_sync(org_id: str, delay: float | None = None) -> None:
    """Sync the org's worktrees once its changes settle. An org is built in a burst (agents,
    then who manages whom), and a lead mustn't get a worktree for the moment it manages
    nobody."""
    if org_id not in _git_orgs:  # only orgs that use git have worktrees to sync
        return
    if (t := _pending.get(org_id)) is not None:
        t.cancel()

    async def run() -> None:
        await asyncio.sleep(SYNC_DELAY if delay is None else delay)
        _pending.pop(org_id, None)
        try:
            await sync_worktrees(org_id)
        except Exception as e:  # noqa: BLE001 - worktrees are a convenience; work goes on
            await bus.publish("workspace.error", {"error": str(e)}, org_id=org_id)

    _pending[org_id] = asyncio.get_running_loop().create_task(run())


async def cancel_pending() -> None:
    """Drop the syncs still waiting (on shutdown)."""
    tasks = list(_pending.values())
    _pending.clear()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def branch_of(path: Path) -> str:
    return (await git(path, "rev-parse", "--abbrev-ref", "HEAD")).strip()


async def changes(path: Path) -> list[str]:
    """Uncommitted changes: "M src/app.py", "?? notes.md"…"""
    out = await git(path, "status", "--porcelain", "--untracked-files=all")
    return [line for line in out.splitlines() if line.strip()]


async def ahead_behind(root: Path, base: str, branch: str) -> tuple[int, int]:
    out = await git(root, "rev-list", "--left-right", "--count", f"{base}...{branch}", check=False)
    try:
        behind, ahead = (int(x) for x in out.split())
    except ValueError:
        return 0, 0
    return ahead, behind


async def worktrees(org: Org) -> list[dict[str, Any]]:
    """The main checkout and every agent's worktree: branch, commits ahead of and behind
    the base branch, uncommitted changes and the latest commit."""
    from app.tools.base import org_workspace

    root = org_workspace(org)
    g = git_settings(org)
    async with SessionLocal() as session:
        agents = list((await session.execute(select(Agent).where(
            Agent.org_id == org.id))).scalars())
    by_dir = {a.worktree.replace("\\", "/").strip("/"): a for a in agents if a.worktree}
    out: list[dict[str, Any]] = []
    if not g["enabled"]:
        return [{"path": ".", "branch": None, "agentId": None, "main": True, "changes": [],
                 "ahead": 0, "behind": 0, "last": None}]
    listing = await git(root, "worktree", "list", "--porcelain")
    for block in listing.strip().split("\n\n"):
        fields = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
        path = Path(fields.get("worktree", "")).resolve()
        if not path.exists():
            continue
        rel = "." if path == root else str(path.relative_to(root)).replace("\\", "/") \
            if path.is_relative_to(root) else str(path)
        branch = fields.get("branch", "").removeprefix("refs/heads/") or None
        ahead, behind = await ahead_behind(root, g["base"], branch) if branch else (0, 0)
        last = (await git(path, "log", "-1", "--format=%h%x09%s%x09%cI", check=False)).strip()
        h, _, rest = last.partition("\t")
        subject, _, when = rest.partition("\t")
        agent = by_dir.get(rel)
        out.append({"path": rel, "branch": branch, "main": path == root,
                    "agentId": agent.id if agent else None,
                    "agentName": agent.name if agent else None,
                    "changes": (await changes(path))[:200], "ahead": ahead, "behind": behind,
                    "last": {"hash": h, "subject": subject, "at": when} if h else None})
    return out


DIFF_CAP = 200_000


async def diff(root: Path, base: str, path: Path) -> dict[str, Any]:
    """What a worktree's branch changed from the base branch (committed), and what's not
    committed yet. Each is a summary (--stat) and the patch, capped."""
    branch = await branch_of(path)

    def cap(text: str) -> str:
        return text if len(text) <= DIFF_CAP else text[:DIFF_CAP] + "\n… (cut: too long to show)"

    committed = await git(root, "diff", f"{base}...{branch}", check=False) if branch != base else ""
    stat = await git(root, "diff", "--stat", f"{base}...{branch}", check=False) if branch != base else ""
    pending = await git(path, "diff", "HEAD", check=False)
    untracked = [f for f in (await git(path, "ls-files", "--others", "--exclude-standard",
                                       check=False)).splitlines() if f]
    return {"branch": branch, "base": base, "stat": stat.strip(), "committed": cap(committed),
            "uncommitted": cap(pending), "untracked": untracked[:200]}


async def commit(path: Path, message: str) -> str:
    if not await changes(path):
        return ""
    await git(path, "add", "-A")
    await git(path, "commit", "-m", message.strip() or "Work in progress")
    return (await git(path, "rev-parse", "--short", "HEAD")).strip()


async def merge(path: Path, ref: str) -> tuple[bool, list[str], str]:
    """Merge ``ref`` into the branch checked out at ``path``. A conflict is undone and
    reported: (merged, conflicted files, git's output)."""
    try:
        out = await git(path, "merge", "--no-ff", "--no-edit", ref)
        return True, [], out.strip()
    except GitError as e:
        conflicts = [f for f in (await git(path, "diff", "--name-only", "--diff-filter=U",
                                           check=False)).splitlines() if f]
        await git(path, "merge", "--abort", check=False)
        return False, conflicts, str(e)
