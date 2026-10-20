"""asyncio helpers."""

from __future__ import annotations

import asyncio


def stop_if_cancelled() -> None:
    """Re-raise a cancellation that an error has hidden.

    A task cancelled in the middle of a database call can see another exception in
    place of CancelledError (the cleanup's own error). A loop that catches Exception
    to keep running would then swallow the cancellation and never stop. Call this
    first in such a handler.
    """
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError
