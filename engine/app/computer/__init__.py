"""Computer use: which computer agents' computer tools drive.

- ``off`` (the default): no computer use.
- ``driver``: this computer through cua-driver (app/computer/driver.py): agents work in
  the user's apps in the background with the desktop_* tools.
- ``native``: this computer, the user's own screen, mouse and keyboard
  (app/computer/native.py), with the computer_* tools.

Either way the user turns it on in Settings, can take it back at any moment (Stop in the
banner; for native also the mouse into the top-left corner), and sees who's using it.
- Any backend a plugin adds (``PackApi.computer_backend``): the cua-desktop pack adds the
  sandboxed Linux desktop in Docker that Pantheon 1 used.

The choice is the ``computer`` setting.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

STATE_KEY = "computer"


@dataclass
class Backend:
    id: str
    label: str
    description: str
    factory: Callable[[], Any]  # -> an object with cmd(), screenshot(), status()
    pack: str = ""


_BACKENDS: dict[str, Backend] = {}
_state: dict[str, Any] = {"backend": "off"}
_paused: dict[str, Any] = {"until": 0.0, "reason": ""}
# Who's using the computer right now (for the banner): {"agentId", "agent", "orgId", "at"}.
active: dict[str, Any] = {}


class ComputerOff(RuntimeError):
    pass


def register(backend: Backend) -> None:
    _BACKENDS[backend.id] = backend


def _native() -> Any:
    from app.computer.native import NativeComputer

    return NativeComputer()


def _driver() -> Any:
    from app.computer.driver import driver

    return driver


register(Backend("driver", "This computer",
                 "Agents work in your apps in the background with their own cursor (the "
                 "desktop_* tools, through cua-driver): you keep your mouse and keyboard. Press "
                 "Stop any time.", _driver))
register(Backend("native", "This computer, taking over the mouse and keyboard",
                 "Agents see your screen and move your real mouse and keyboard (the computer_* "
                 "tools), so don't use the computer meanwhile. Take it back any time: move the "
                 "mouse into the top-left corner of the screen, or press Stop.", _native))


def backends() -> list[Backend]:
    from app import packs

    return [b for b in _BACKENDS.values() if not b.pack or packs.is_usable(b.pack)]


def backend_id() -> str:
    return str(_state.get("backend") or "off")


def paused() -> str | None:
    """Why computer use is paused, if it is."""
    if _paused["until"] > time.time():
        return _paused["reason"]
    return None


def pause(reason: str, seconds: float = 24 * 3600) -> None:
    _paused.update(until=time.time() + seconds, reason=reason)
    active.clear()


def resume() -> None:
    _paused.update(until=0.0, reason="")


def current() -> Any:
    """The computer to drive, or ComputerOff saying why there's none."""
    b = backend_id()
    if b == "off":
        raise ComputerOff("computer use is off: the user can turn it on in Settings → This "
                          "computer")
    if (why := paused()) is not None:
        raise ComputerOff(f"computer use is paused ({why})")
    backend = next((x for x in backends() if x.id == b), None)
    if backend is None:
        raise ComputerOff(f"the computer '{b}' isn't available (is its pack switched off?)")
    return backend.factory()


async def load_state() -> None:
    from app.core.db import SessionLocal
    from app.models import Setting

    async with SessionLocal() as session:
        row = await session.get(Setting, STATE_KEY)
    _state.clear()
    _state.update(row.value if row and isinstance(row.value, dict) else {"backend": "off"})


async def set_backend(backend: str) -> None:
    from app.core.db import SessionLocal
    from app.models import Setting

    if backend != "off" and backend not in {b.id for b in backends()}:
        raise ValueError(f"no computer '{backend}'")
    _state["backend"] = backend
    resume()
    async with SessionLocal() as session:
        row = await session.get(Setting, STATE_KEY)
        if row is None:
            session.add(Setting(key=STATE_KEY, value=dict(_state)))
        else:
            row.value = dict(_state)
        await session.commit()


def status() -> dict[str, Any]:
    return {"backend": backend_id(), "paused": paused(),
            "active": dict(active) if active and time.time() - active.get("at", 0) < 30 else None,
            "backends": [{"id": b.id, "label": b.label, "description": b.description}
                         for b in backends()]}
