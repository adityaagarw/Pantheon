"""Client for a cua computer-server (github.com/trycua/cua).

The server runs inside a sandbox desktop (the ``trycua/cua-xfce`` container)
and executes commands sent as ``POST /cmd {"command": ..., "params": {...}}``;
it answers with a ``data: {json}`` line. Screenshots come back as base64 in
``image_data``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx


class ComputerError(RuntimeError):
    pass


class CuaComputer:
    def __init__(self, base_url: str, *, api_key: str = "", container: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {k: v for k, v in (("X-API-Key", api_key),
                                          ("X-Container-Name", container)) if v}

    async def cmd(self, command: str, timeout: float = 60, **params: Any) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                res = await client.post(f"{self.base_url}/cmd", headers=self.headers,
                                        json={"command": command, "params": params})
        except httpx.HTTPError as e:
            raise ComputerError(f"the computer is not reachable at {self.base_url} ({e}). "
                                "Start it with: docker compose --profile computer up -d") from None
        if res.status_code >= 400:
            try:
                detail = res.json().get("detail")
            except ValueError:
                detail = res.text[:300]
            raise ComputerError(f"{command} failed: HTTP {res.status_code} {detail}")
        result: dict[str, Any] | None = None
        for line in res.text.splitlines():
            if line.startswith("data:"):
                result = json.loads(line[5:].strip())
        if result is None:
            raise ComputerError(f"{command}: empty response")
        if not result.get("success", False):
            raise ComputerError(f"{command} failed: {result.get('error') or result}")
        return result

    async def screenshot(self, quality: int = 70) -> str:
        """A JPEG data: URL of the whole screen."""
        r = await self.cmd("screenshot", format="jpeg", quality=quality)
        fmt = r.get("format") or "jpeg"
        return f"data:image/{fmt};base64,{r['image_data']}"

    async def screen_size(self) -> tuple[int, int]:
        r = await self.cmd("get_screen_size")
        size = r.get("size") or {}
        return int(size.get("width", 0)), int(size.get("height", 0))

    async def status(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                res = await client.get(f"{self.base_url}/status", headers=self.headers)
            return {"ok": res.status_code < 400, **(res.json() if res.content else {})}
        except (httpx.HTTPError, ValueError) as e:
            return {"ok": False, "error": str(e)}
