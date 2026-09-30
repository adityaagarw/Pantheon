"""Plugins: drop-in folders that extend Pantheon without touching its code.

A plugin is a folder in ``PANTHEON_PLUGINS_DIR`` with a ``plugin.json``:

    {
      "id": "town-square",                 # lowercase slug, unique
      "name": "Town Square",
      "version": "0.1.0",
      "description": "A small town for social simulations.",
      "templates": ["templates/*.json"],   # organization/scenario templates
      "assets": [                          # 3D things for offices and scenes
        {"id": "knife", "label": "Kitchen knife", "file": "assets/knife.glb",
         "size_m": 0.3, "carryable": true, "category": "Props"},
        {"id": "bench", "label": "Park bench", "parts": [
           {"shape": "box", "size": [1.6, 0.08, 0.45], "pos": [0, 0.45, 0], "color": "#8a5a3b"}
        ]}
      ],
      "module": "plugin.py"                # optional Python: tools and world rules
    }

``module`` may define ``register(api: PluginApi)`` to add tools (granted to
agents like any built-in tool) and ``on_use`` handlers that give objects
behaviour ("handcuffs" restrain a target, a "radio" calls for backup, ...).

Plugins are trusted code: install only what you'd run yourself. A plugin that
fails to load is reported (Settings → Plugins) and skipped; it never stops the
server.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import json
import logging
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import settings

log = logging.getLogger(__name__)

SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass
class UseEvent:
    """What a use_object handler receives."""

    obj: Any  # app.models.WorldObject (mutable: change .state, .holder_id, .place)
    action: str
    actor: Any  # app.models.Agent
    target: Any | None  # the Agent it was used on, if any
    world: Any  # app.services.spatial.World
    ctx: Any  # app.tools.base.ToolContext


UseHandler = Callable[[UseEvent], Awaitable[str | None]]


@dataclass
class Plugin:
    id: str
    name: str
    version: str
    description: str
    path: Path
    assets: list[dict[str, Any]] = field(default_factory=list)
    templates: dict[str, dict[str, Any]] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    error: str | None = None

    def info(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "version": self.version,
                "description": self.description, "path": str(self.path),
                "assets": [a["key"] for a in self.assets],
                "templates": list(self.templates), "tools": self.tools, "error": self.error}


class PluginApi:
    """Handed to a plugin module's ``register``."""

    def __init__(self, plugin: Plugin) -> None:
        self.plugin = plugin

    def tool(self, name: str, description: str, parameters: dict[str, Any] | None = None, *,
             approval: str = "auto", timeout: float = 120.0, side_effects: bool = False):
        """Decorator registering an agent tool (see app.tools.base.tool)."""
        from app.tools.base import tool

        self.plugin.tools.append(name)
        return tool(name, description, parameters, category=f"plugin:{self.plugin.id}",
                    approval=approval, timeout=timeout, side_effects=side_effects)

    def on_use(self, match: str, handler: UseHandler | None = None):
        """Give objects behaviour. ``match`` is a glob on the object's name or asset key."""

        def deco(fn: UseHandler) -> UseHandler:
            _USE_HANDLERS.append((match.lower(), fn))
            return fn

        return deco(handler) if handler else deco


_PLUGINS: dict[str, Plugin] = {}
_USE_HANDLERS: list[tuple[str, UseHandler]] = []
_loaded = False


def plugins_dir() -> Path:
    return settings.plugins_path


def _asset_entry(plugin: Plugin, raw: dict[str, Any]) -> dict[str, Any]:
    from app.services import assets as asset_svc

    aid = str(raw.get("id", ""))
    if not SLUG.match(aid):
        raise ValueError(f"asset id {aid!r} must be a lowercase slug")
    base = {"key": f"plugin:{plugin.id}/{aid}", "id": aid, "label": raw.get("label") or aid,
            "category": raw.get("category") or "Props",
            "carryable": bool(raw.get("carryable", False)),
            "blocking": bool(raw.get("blocking", not raw.get("carryable", False))),
            "description": raw.get("description", ""), "license": raw.get("license", ""),
            "source": f"plugin:{plugin.id}"}
    if raw.get("parts"):
        spec = asset_svc.validate_parts(raw["parts"])
        return base | {"kind": "procedural", "parts": spec,
                       "size": asset_svc.parts_size(spec), "scale": 1.0}
    file = str(raw.get("file", ""))
    path = (plugin.path / file).resolve()
    if not file or not path.is_file() or plugin.path.resolve() not in path.parents:
        raise ValueError(f"asset {aid}: file {file!r} not found inside the plugin")
    bounds = asset_svc.glb_bounds(path.read_bytes())
    scale, size = asset_svc.fit(bounds, raw.get("size_m"), raw.get("height_m"))
    return base | {"kind": "glb", "url": f"/api/v1/plugins/{plugin.id}/files/{file}",
                   "bounds": bounds, "scale": scale, "size": size}


def _load_one(folder: Path) -> Plugin | None:
    manifest_path = folder / "plugin.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return Plugin(folder.name, folder.name, "?", "", folder, error=f"bad plugin.json: {e}")
    pid = str(manifest.get("id") or folder.name)
    plugin = Plugin(pid, str(manifest.get("name") or pid), str(manifest.get("version") or "0"),
                    str(manifest.get("description") or ""), folder)
    try:
        if not SLUG.match(pid):
            raise ValueError("id must be a lowercase slug")
        for raw in manifest.get("assets") or []:
            plugin.assets.append(_asset_entry(plugin, raw))
        for pattern in manifest.get("templates") or []:
            for f in sorted(folder.glob(pattern)):
                plugin.templates[f"{pid}/{f.stem}"] = json.loads(f.read_text(encoding="utf-8"))
        if module := manifest.get("module"):
            path = (folder / module).resolve()
            if folder.resolve() not in path.parents or not path.is_file():
                raise ValueError(f"module {module!r} not found inside the plugin")
            name = f"pantheon_plugin_{pid.replace('-', '_')}"
            spec = importlib.util.spec_from_file_location(name, path)
            assert spec and spec.loader
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
            if hasattr(mod, "register"):
                mod.register(PluginApi(plugin))
    except Exception as e:  # noqa: BLE001 - a broken plugin must not stop the server
        log.exception("plugin %s failed to load", pid)
        plugin.error = f"{type(e).__name__}: {e}"
    return plugin


def load_all(force: bool = False) -> list[Plugin]:
    global _loaded
    if _loaded and not force:
        return list(_PLUGINS.values())
    _loaded = True
    root = plugins_dir()
    if not root.is_dir():
        return []
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        if folder.name in {p.path.name for p in _PLUGINS.values()}:
            continue
        plugin = _load_one(folder)
        if plugin is not None:
            _PLUGINS[plugin.id] = plugin
            if plugin.error is None:
                log.info("plugin %s %s loaded (%d assets, %d templates, %d tools)", plugin.id,
                         plugin.version, len(plugin.assets), len(plugin.templates),
                         len(plugin.tools))
    return list(_PLUGINS.values())


def all_plugins() -> list[Plugin]:
    return load_all()


def get(pid: str) -> Plugin | None:
    load_all()
    return _PLUGINS.get(pid)


def assets() -> list[dict[str, Any]]:
    return [a for p in load_all() if p.error is None for a in p.assets]


def templates() -> dict[str, dict[str, Any]]:
    return {k: v for p in load_all() if p.error is None for k, v in p.templates.items()}


async def handle_use(event: UseEvent) -> list[str]:
    """Run every plugin handler matching the object; returns their narration."""
    out: list[str] = []
    names = {str(event.obj.name).lower(), str(event.obj.asset).lower()}
    for match, fn in list(_USE_HANDLERS):
        if any(fnmatch.fnmatchcase(n, match) for n in names):
            text = await fn(event)
            if text:
                out.append(text)
    return out
