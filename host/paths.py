"""Where Pantheon Host keeps things, and how it reaches Pantheon."""

from __future__ import annotations

import json
import os
import ssl
import sys
from pathlib import Path

CONFIG = Path.home() / ".pantheon" / "host.json"
OLD_CONFIG = Path.home() / ".pantheon" / "kronos.json"
DEFAULT_BACKEND = os.environ.get("PANTHEON_BACKEND", "http://localhost:8710")
# Where first-time setup puts Pantheon when it isn't anywhere yet.
DEFAULT_HOME = Path.home() / "Pantheon"
FROZEN = bool(getattr(sys, "frozen", False))  # the packaged app (PyInstaller)


def load_config() -> dict:
    for path in (CONFIG, OLD_CONFIG):  # kronos.json was the first name of this file
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return {"allowed": [], "backend": DEFAULT_BACKEND}


def save_config(cfg: dict) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def is_pantheon(folder: Path) -> bool:
    return (folder / "docker-compose.yml").is_file() and (folder / "server").is_dir()


def find_repo() -> Path | None:
    """The Pantheon folder: where this code lives, next to the packaged app, or the one
    first-time setup chose. None when Pantheon isn't set up on this computer yet."""
    if not FROZEN:
        return Path(__file__).resolve().parent.parent
    exe = Path(sys.executable).resolve().parent
    for folder in (exe, exe.parent, exe.parent.parent):
        if is_pantheon(folder):
            return folder
    chosen = load_config().get("repo")
    if chosen and is_pantheon(Path(chosen)):
        return Path(chosen)
    return None


REPO = find_repo() or DEFAULT_HOME
DATA = REPO / "data"
# The link's shared secret: written by the backend into data/, which only the backend
# container and this computer can read.
TOKEN = DATA / "host.token"
# Pantheon's HTTPS proxy signs with its own local authority.
CADDY_ROOT = DATA / "caddy/caddy/pki/authorities/local/root.crt"


def allowed_roots() -> list[Path]:
    return [Path(p).expanduser().resolve() for p in load_config().get("allowed", [])]


def backend() -> str:
    return (load_config().get("backend") or DEFAULT_BACKEND).rstrip("/")


def tls_context(insecure: bool = False) -> ssl.SSLContext:
    if insecure:
        return ssl._create_unverified_context()
    ctx = ssl.create_default_context()
    if CADDY_ROOT.exists():
        ctx.load_verify_locations(str(CADDY_ROOT))
    return ctx
