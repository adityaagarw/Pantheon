"""The interface for browsers (server mode): the built UI, served next to the API.

It's found at PANTHEON_UI_DIR, beside the engine in the installed runtime (runtime/ui), or
in the repo (ui/dist, after ``npm --prefix ui run build``). Paths that aren't files get
index.html, so the interface's own routes (/org/…) load on refresh.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

_ENGINE = Path(__file__).resolve().parents[2]
# Not from the system's table: on Windows it comes from the registry, where .js is sometimes
# text/plain, and browsers refuse to run module scripts served that way.
TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
         ".html": "text/html", ".json": "application/json", ".svg": "image/svg+xml",
         ".wasm": "application/wasm", ".png": "image/png", ".jpg": "image/jpeg",
         ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
         ".ico": "image/x-icon", ".woff2": "font/woff2", ".woff": "font/woff",
         ".ttf": "font/ttf", ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
         ".map": "application/json", ".txt": "text/plain"}


def ui_dir() -> Path | None:
    # The repo's ui/ holds the *source* index.html: its build (ui/dist) comes first. The
    # installed runtime's ui/ is already the build.
    for d in (os.environ.get("PANTHEON_UI_DIR"), _ENGINE.parent / "ui" / "dist",
              _ENGINE.parent / "ui"):
        if d and (Path(d) / "index.html").is_file():
            return Path(d).resolve()
    return None


def mount(app: FastAPI) -> None:
    """Add the catch-all route (after every API route, so those match first)."""

    @app.get("/{path:path}", include_in_schema=False)
    async def interface(path: str) -> FileResponse:
        root = ui_dir()
        if root is None or path == "api" or path.startswith("api/"):
            raise HTTPException(404, "not found" if root else
                                "the interface isn't built (npm --prefix ui run build)")
        file = (root / path).resolve()
        if path and file.is_file() and file.is_relative_to(root):
            cache = ("public, max-age=31536000, immutable" if path.startswith("assets/")
                     else "no-cache")
            return FileResponse(file, headers={"Cache-Control": cache},
                                media_type=TYPES.get(file.suffix.lower()))
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})
