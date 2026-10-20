"""Pantheon Host, built in: what the engine may do on this computer for Kronos (and the
user). In Pantheon 1 this was a separate helper program; the engine now runs on the user's
computer, so it does it itself (app/host/ops.py, services/host.py).

- REPO: Pantheon's source, when it runs from a checkout (None in an installed app).
- The allowed folders: where Kronos may read, write and run commands besides the source.
  The user adds them in Settings → This computer; they're the ``host.folders`` setting.
"""

from __future__ import annotations

from pathlib import Path

FOLDERS_KEY = "host.folders"


def _repo() -> Path | None:
    root = Path(__file__).resolve().parents[3]
    return root if (root / ".git").exists() and (root / "engine").is_dir() and \
        (root / "ui").is_dir() else None


REPO: Path | None = _repo()
_folders: list[Path] = []


def allowed_roots() -> list[Path]:
    return list(_folders)


def default_root() -> Path:
    if REPO is not None:
        return REPO
    if _folders:
        return _folders[0]
    from app.host.ops import Refused

    raise Refused("no folder is allowed yet: the user can allow one in Settings → This computer")


async def load_folders() -> None:
    from app.core.db import SessionLocal
    from app.models import Setting

    async with SessionLocal() as session:
        row = await session.get(Setting, FOLDERS_KEY)
    _folders.clear()
    _folders.extend(Path(p) for p in (row.value if row else []) or [])


async def set_folders(folders: list[Path]) -> None:
    from app.core.db import SessionLocal
    from app.models import Setting

    _folders[:] = list(dict.fromkeys(p.resolve() for p in folders))
    async with SessionLocal() as session:
        row = await session.get(Setting, FOLDERS_KEY)
        value = [str(p) for p in _folders]
        if row is None:
            session.add(Setting(key=FOLDERS_KEY, value=value))
        else:
            row.value = value
        await session.commit()
