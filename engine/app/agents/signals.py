"""Decoupled wake-up hook: services signal the runtime without importing it."""

from __future__ import annotations

from collections.abc import Callable

_waker: Callable[[str], None] | None = None


def set_waker(fn: Callable[[str], None] | None) -> None:
    global _waker
    _waker = fn


def wake(agent_id: str) -> None:
    if _waker is not None:
        _waker(agent_id)
