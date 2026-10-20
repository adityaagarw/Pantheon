"""Pantheon Host, built in: Kronos's work on this computer (app/host/ops.py), run by the
engine itself. Pantheon 1 sent these operations to a separate helper over a long poll; the
engine now runs on the user's computer, so ``bridge.call`` runs them here, in a thread.
"""

from __future__ import annotations

import asyncio
import json
import platform
from typing import Any

from app.tools.base import ToolError


class Bridge:
    connected = True

    def can(self, capability: str) -> bool:
        return capability in ("kronos", "folders", "recorder")

    async def status(self) -> dict[str, Any]:
        """This computer, the source Kronos may change (its branch and open changes) and the
        folders it may use."""
        from app.host import ops

        info = json.loads(await asyncio.to_thread(ops.op_info, {}))
        return {"connected": True, "info": {**info, "host": platform.node(),
                                             "capabilities": ["kronos", "folders", "recorder"]}}

    async def call(self, op: str, args: dict[str, Any], timeout: float = 120) -> Any:
        from app.host import ops

        fn = ops.OPS.get(op)
        if fn is None:
            raise ToolError(f"unknown operation '{op}'")
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn, args), timeout + 30)
        except ops.Refused as e:
            raise ToolError(str(e)) from None
        except TimeoutError:
            raise ToolError(f"{op} took longer than {int(timeout)} s") from None
        except (OSError, KeyError, ValueError) as e:
            raise ToolError(f"{op} failed: {e}") from None


bridge = Bridge()
