"""Organization templates: portable org definitions (orgs.export_org's format).

Three ship with Pantheon (app/templates: a solo assistant, a build team, a research
flow), packs add more (Pantheon 1's are the templates-classic pack, off by default), and
any organization can be saved as one of the user's own (<data>/templates). A template is
a JSON file, so it can be shared and imported too.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import settings

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
MINE = "my/"


class TemplateError(ValueError):
    pass


def user_dir() -> Path:
    return settings.data_path / "templates"


@lru_cache
def _load() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for f in sorted(TEMPLATE_DIR.glob("*.json")):
        out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
    return out


def _mine() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for f in sorted(user_dir().glob("*.json")) if user_dir().is_dir() else []:
        try:
            out[MINE + f.stem] = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def _all() -> dict[str, dict[str, Any]]:
    from app import packs

    return {**_load(), **packs.templates(), **_mine()}


def get(key: str) -> dict[str, Any] | None:
    return _all().get(key)


def _source(key: str) -> str:
    return "mine" if key.startswith(MINE) else "pack" if "/" in key else "builtin"


def catalog() -> list[dict[str, Any]]:
    return [{"key": k, "name": v.get("name", k), "description": v.get("description", ""),
             "source": _source(k), "plugin": k.split("/")[0] if _source(k) == "pack" else None,
             "mode": (v.get("settings") or {}).get("mode", "team"),
             "workspace": v.get("workspace") or None,
             "flows": [f.get("name", "") for f in v.get("flows") or []],
             "agents": [{"name": a["name"], "role": a.get("role", "")}
                        for a in v.get("agents", [])]}
            for k, v in _all().items()]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]


def save(definition: dict[str, Any], name: str = "", description: str | None = None) -> str:
    """Keep a definition as one of the user's templates (replacing one with the same
    name); returns its key."""
    if not isinstance(definition, dict) or not isinstance(definition.get("agents"), list):
        raise TemplateError("that isn't an organization definition (it needs agents)")
    name = (name or definition.get("name") or "").strip()
    if not (slug := _slug(name)):
        raise TemplateError("the template needs a name")
    d = {**definition, "name": name}
    if description is not None:
        d["description"] = description
    user_dir().mkdir(parents=True, exist_ok=True)
    (user_dir() / f"{slug}.json").write_text(json.dumps(d, indent=2, ensure_ascii=False),
                                              encoding="utf-8")
    return MINE + slug


def delete(key: str) -> None:
    if not key.startswith(MINE):
        raise TemplateError("only your own templates can be deleted")
    f = user_dir() / f"{key[len(MINE):]}.json"
    if not f.is_file():
        raise TemplateError("template not found")
    f.unlink()
