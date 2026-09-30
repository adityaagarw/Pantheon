"""Organization templates: portable org definitions shipped as JSON files."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


@lru_cache
def _load() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for f in sorted(TEMPLATE_DIR.glob("*.json")):
        out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
    return out


def _all() -> dict[str, dict[str, Any]]:
    from app import plugins

    return {**_load(), **plugins.templates()}


def get(key: str) -> dict[str, Any] | None:
    return _all().get(key)


def catalog() -> list[dict[str, Any]]:
    return [{"key": k, "name": v.get("name", k), "description": v.get("description", ""),
             "plugin": k.split("/")[0] if "/" in k else None,
             "agents": [{"name": a["name"], "role": a.get("role", "")} for a in v.get("agents", [])]}
            for k, v in _all().items()]
