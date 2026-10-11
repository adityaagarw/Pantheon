"""Folders on this computer that organizations use as their workspace.

Pantheon's agents work inside the backend container, which sees only the folders Docker
mounts into it. To give an organization a folder from this computer, Pantheon Host adds a
mount for it to ``docker-compose.override.yml`` (Docker Compose reads that file next to
``docker-compose.yml``) and restarts the backend. The folder shows up at
``/host/<name>`` in the container.

Only the person at this computer picks these folders (in a native picker, or with
``python host/main.py mount <folder>``). Pantheon can ask for the picker and can take a
mount away, but it can never name a folder to add.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from pathlib import Path

from host import paths
from host.kronos_ops import Refused

OVERRIDE = paths.REPO / "docker-compose.override.yml"
MARK = "# Managed by Pantheon Host"
CONTAINER_ROOT = "/host"


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "/").replace('"', '\\"') + '"'


def mounts() -> dict[str, str]:
    """{folder on this computer: path in the container}"""
    return dict(paths.load_config().get("mounts") or {})


def container_path(folder: Path) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", folder.name.lower()).strip("-")[:40] or "folder"
    return f"{CONTAINER_ROOT}/{slug}-{hashlib.sha1(str(folder).encode()).hexdigest()[:6]}"


def workspaces_dir() -> Path:
    """The folder mounted at /workspaces (PANTHEON_WORKSPACES in .env, or data/workspaces)."""
    env = paths.REPO / ".env"
    value = ""
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("PANTHEON_WORKSPACES="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
    folder = Path(value).expanduser() if value else paths.REPO / "data" / "workspaces"
    return (folder if folder.is_absolute() else paths.REPO / folder).resolve()


def _volumes(current: dict[str, str]) -> str:
    # The long form: a Windows drive letter's colon can't be mistaken for the separator.
    return "\n".join(f"      - type: bind\n        source: {_quote(h)}\n        target: {_quote(c)}"
                     for h, c in sorted(current.items()))


def _write_override(current: dict[str, str]) -> None:
    if OVERRIDE.exists() and MARK not in OVERRIDE.read_text(encoding="utf-8", errors="replace"):
        raise Refused(
            f"{OVERRIDE.name} is yours, so Pantheon Host won't change it. Add these lines under "
            "services > backend > volumes in it, then run docker compose up -d backend:\n"
            + _volumes(current))
    if not current:
        OVERRIDE.unlink(missing_ok=True)
        return
    body = _volumes(current)
    OVERRIDE.write_text(
        f"{MARK}: folders on this computer that organizations use as their workspace.\n"
        "# Change them in Pantheon (an organization's Settings) or with\n"
        "# python host/main.py mount|unmount <folder>. Edits here are overwritten.\n"
        f"services:\n  backend:\n    volumes:\n{body}\n", encoding="utf-8")


def _restart_backend() -> None:
    """Recreate only the backend, so it gets the new mounts (a second after we answer)."""
    from host import system

    def later() -> None:
        time.sleep(1.5)
        code, out = system._docker("compose", "up", "-d", "--no-deps", "backend", timeout=300)
        if code:
            from host.main import log

            log(f"couldn't restart Pantheon's backend for a new folder: {out[-300:]}")

    threading.Thread(target=later, daemon=True).start()


def mount(folder: str, *, restart: bool = True) -> dict:
    """Make a folder on this computer usable as a workspace. Returns where Pantheon sees it
    and whether the backend restarts to get it."""
    p = Path(folder).expanduser().resolve()
    if not p.is_dir():
        raise Refused(f"{p} is not a folder")
    shared = workspaces_dir()
    if p == shared or shared in p.parents:  # already in the container
        rel = p.relative_to(shared).as_posix()
        return {"hostPath": str(p), "path": "/workspaces" + (f"/{rel}" if rel != "." else ""),
                "restart": False}
    current = mounts()
    if str(p) in current:
        return {"hostPath": str(p), "path": current[str(p)], "restart": False}
    current[str(p)] = container_path(p)
    _write_override(current)
    cfg = paths.load_config()
    cfg["mounts"] = current
    paths.save_config(cfg)
    if restart:
        _restart_backend()
    return {"hostPath": str(p), "path": current[str(p)], "restart": restart}


def unmount(path: str) -> str:
    """Stop sharing a folder (by its path here or in the container). It takes effect the
    next time the backend starts; taking access away is always fine to ask for."""
    current = mounts()
    keep = {h: c for h, c in current.items() if path not in (h, c)
            and str(Path(path).expanduser().resolve()) != h}
    if keep == current:
        return f"{path} isn't shared"
    _write_override(keep)
    cfg = paths.load_config()
    cfg["mounts"] = keep
    paths.save_config(cfg)
    return f"{path} is no longer shared (from the next restart of Pantheon)"
