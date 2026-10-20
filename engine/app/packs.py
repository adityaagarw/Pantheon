"""Packs: drop-in folders that add to Pantheon without touching its code.

A pack is a folder with a ``pack.json`` (``plugin.json`` from Pantheon 1 also works):

    {
      "id": "town-square",                 # lowercase slug, unique
      "name": "Town Square",
      "version": "0.1.0",
      "description": "A small town for social simulations.",
      "requires": ["three"],               # libraries this pack's libraries import
      "libraries": [                       # Stage libraries agents can import
        {"id": "gsap", "name": "GSAP", "summary": "one line for the library list",
         "imports": {"gsap": "gsap.js"},   # import-map name -> file in the pack
         "docs": "docs.md",                # the reference agents read (stage_docs)
         "core": false}                    # core: the Stage itself needs it; always on
      ],
      "assets": [                          # 3D things for offices and scenes
        {"id": "knife", "label": "Kitchen knife", "file": "assets/knife.glb",
         "size_m": 0.3, "carryable": true, "category": "Props"},
        {"id": "bench", "label": "Park bench", "parts": [
           {"shape": "box", "size": [1.6, 0.08, 0.45], "pos": [0, 0.45, 0], "color": "#8a5a3b"}
        ]}
      ],
      "templates": ["templates/*.json"],   # organization/scenario templates
      "module": "plugin.py"                # optional Python: tools and world rules
    }

A pack with a ``module`` is a plugin: ``register(api: PackApi)`` adds tools (granted to
agents like any built-in tool) and ``on_use`` handlers that give objects behaviour
("handcuffs" restrain a target, a "radio" calls for backup, ...).

Packs come from two folders: the ones that ship with the app (the repo's ``packs/``),
then the user's (``<data>/packs``); a user's pack with the same id replaces the shipped
one, so a newer three.js is a pack too. Any pack can be switched off; a pack holding a
core library can't (only replaced). The user's own packs can be removed.

Packs are trusted code: install only what you'd run yourself. A pack that fails to load
is reported (Settings → Packs) and skipped; it never stops the engine.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import json
import logging
import re
import shutil
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import settings

log = logging.getLogger(__name__)

SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MANIFESTS = ("pack.json", "plugin.json")
STATE_KEY = "packs"  # Setting: {"disabled": [pack ids], "enabled": [ids of packs off by default]}
# Library URL segments the Stage itself serves (app/stage/lib).
RESERVED = {"fonts", "icons", "peeps", "stage.js", "art.js"}


class PackError(ValueError):
    pass


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
class Library:
    """A Stage library: import-map names served from its pack's folder."""

    id: str
    name: str
    summary: str
    pack_id: str
    root: Path
    imports: dict[str, str]  # import name -> file (or folder, for names ending in "/")
    docs: str = ""
    core: bool = False
    requires: list[str] = field(default_factory=list)

    def info(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "summary": self.summary, "pack": self.pack_id,
                "imports": sorted(self.imports), "core": self.core, "requires": self.requires,
                "hasDocs": bool(self.docs)}


@dataclass
class Pack:
    id: str
    name: str
    version: str
    description: str
    path: Path
    bundled: bool
    requires: list[str] = field(default_factory=list)
    libraries: list[Library] = field(default_factory=list)
    assets: list[dict[str, Any]] = field(default_factory=list)
    templates: dict[str, dict[str, Any]] = field(default_factory=dict)
    module: str = ""
    tools: list[str] = field(default_factory=list)
    agent_kinds: list[str] = field(default_factory=list)  # registered by its module
    computers: list[str] = field(default_factory=list)  # computer backends it registered
    # Office content (furniture, characters, themes): see _office.
    office: dict[str, Any] | None = None
    # The app needs this pack (the Stage's libraries, the original office): it can be
    # replaced by a pack with the same id, never switched off.
    required: bool = False
    # A pack can ship switched off ("enabled": false), for the user to switch on.
    default_on: bool = True
    error: str | None = None

    @property
    def core(self) -> bool:
        return self.required or any(lib.core for lib in self.libraries)

    @property
    def enabled(self) -> bool:
        if self.core:
            return True
        return self.id not in _disabled if self.default_on else self.id in _enabled

    @property
    def usable(self) -> bool:
        return self.error is None and self.enabled

    def kinds(self) -> list[str]:
        office = self.office or {}
        return [k for k, has in (
            ("library", self.libraries), ("furniture", office.get("items")),
            ("characters", office.get("characters")), ("themes", office.get("themes")),
            ("assets", self.assets), ("templates", self.templates),
            ("agents", self.agent_kinds), ("computer", self.computers),
            ("plugin", self.module)) if has]

    def counts(self) -> dict[str, int]:
        office = self.office or {}
        return {k: len(office.get(k) or []) for k in ("items", "characters", "themes")}

    def info(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "version": self.version,
                "description": self.description, "path": str(self.path),
                "bundled": self.bundled, "core": self.core, "enabled": self.enabled,
                "kinds": self.kinds(), "requires": self.requires,
                "libraries": [lib.info() for lib in self.libraries],
                "assets": [a["key"] for a in self.assets],
                "templates": list(self.templates), "tools": self.tools,
                "agentKinds": self.agent_kinds, "computers": self.computers,
                "office": self.counts(), "module": self.module, "error": self.error}


class PackApi:
    """Handed to a pack module's ``register``."""

    def __init__(self, pack: Pack) -> None:
        self.pack = pack

    def tool(self, name: str, description: str, parameters: dict[str, Any] | None = None, *,
             approval: str = "auto", timeout: float = 120.0, side_effects: bool = False):
        """Decorator registering an agent tool (see app.tools.base.tool)."""
        from app.tools.base import tool

        self.pack.tools.append(name)
        return tool(name, description, parameters, category=f"plugin:{self.pack.id}",
                    approval=approval, timeout=timeout, side_effects=side_effects)

    def agent_kind(self, kind_id: str, label: str, description: str, run, *,
                   presets: list[dict[str, Any]] | None = None,
                   fields: list[dict[str, Any]] | None = None, validate=None,
                   check=None) -> None:
        """A new kind of agent (app/agents/kinds.py): ``run(turn) -> reply``, and optionally
        ``check(runner) -> report`` for the agent's Check button."""
        from app.agents import kinds

        kinds.register(kinds.Kind(kind_id, label, description, run, presets or [], fields or [],
                                  validate, pack=self.pack.id, check=check))
        self.pack.agent_kinds.append(kind_id)

    def computer_backend(self, backend_id: str, label: str, description: str, factory) -> None:
        """A computer agents can drive (app/computer/__init__.py): ``factory() ->`` an object
        with cmd(), screenshot() and status(), like app/computer/cua.py."""
        from app import computer

        computer.register(computer.Backend(backend_id, label, description, factory,
                                           pack=self.pack.id))
        self.pack.computers.append(backend_id)

    def on_use(self, match: str, handler: UseHandler | None = None):
        """Give objects behaviour. ``match`` is a glob on the object's name or asset key."""

        def deco(fn: UseHandler) -> UseHandler:
            _USE_HANDLERS.append((match.lower(), fn))
            return fn

        return deco(handler) if handler else deco


_PACKS: dict[str, Pack] = {}
_USE_HANDLERS: list[tuple[str, UseHandler]] = []
_modules_loaded: set[str] = set()
_disabled: set[str] = set()
_enabled: set[str] = set()
_loaded = False


def packs_dir() -> Path:
    """The user's pack folder (where added packs go)."""
    return settings.packs_path


def pack_roots() -> list[tuple[Path, bool]]:
    """Where packs load from, and whether that folder ships with the app."""
    return [(settings.bundled_packs_path, True), (settings.packs_path, False)]


def _inside(root: Path, rel: str) -> Path:
    path = (root / rel).resolve()
    if root.resolve() not in path.parents:
        raise PackError(f"{rel!r} is outside the pack")
    return path


def _asset_entry(pack: Pack, raw: dict[str, Any]) -> dict[str, Any]:
    from app.services import assets as asset_svc

    aid = str(raw.get("id", ""))
    if not SLUG.match(aid):
        raise PackError(f"asset id {aid!r} must be a lowercase slug")
    # "plugin:" keys, as in Pantheon 1, so its templates and office designs still resolve.
    base = {"key": f"plugin:{pack.id}/{aid}", "id": aid, "label": raw.get("label") or aid,
            "category": raw.get("category") or "Props",
            "carryable": bool(raw.get("carryable", False)),
            "blocking": bool(raw.get("blocking", not raw.get("carryable", False))),
            "description": raw.get("description", ""), "license": raw.get("license", ""),
            "source": f"plugin:{pack.id}"}
    if raw.get("parts"):
        spec = asset_svc.validate_parts(raw["parts"])
        return base | {"kind": "procedural", "parts": spec,
                       "size": asset_svc.parts_size(spec), "scale": 1.0}
    file = str(raw.get("file", ""))
    path = _inside(pack.path, file) if file else None
    if path is None or not path.is_file():
        raise PackError(f"asset {aid}: file {file!r} not found inside the pack")
    bounds = asset_svc.glb_bounds(path.read_bytes())
    scale, size = asset_svc.fit(bounds, raw.get("size_m"), raw.get("height_m"))
    return base | {"kind": "glb", "url": f"/api/v1/packs/{pack.id}/files/{file}",
                   "bounds": bounds, "scale": scale, "size": size}


def _library(pack: Pack, raw: dict[str, Any]) -> Library:
    lid = str(raw.get("id") or pack.id)
    if not SLUG.match(lid) or lid in RESERVED:
        raise PackError(f"library id {lid!r} must be a lowercase slug, not {sorted(RESERVED)}")
    imports = {str(k): str(v) for k, v in (raw.get("imports") or {}).items()}
    if not imports:
        raise PackError(f"library {lid} has no imports")
    for name, rel in imports.items():
        target = _inside(pack.path, rel)
        if name.endswith("/") != rel.endswith("/"):
            raise PackError(f"library {lid}: {name!r} and {rel!r} must both end in / or neither")
        if not (target.is_dir() if rel.endswith("/") else target.is_file()):
            raise PackError(f"library {lid}: {rel!r} not found inside the pack")
    docs = str(raw.get("docsText") or "")
    if raw.get("docs"):
        docs = _inside(pack.path, str(raw["docs"])).read_text(encoding="utf-8").strip()
    return Library(lid, str(raw.get("name") or lid), str(raw.get("summary") or pack.description),
                   pack.id, pack.path, imports, docs, bool(raw.get("core")),
                   [str(r) for r in raw.get("requires") or pack.requires])


FLOORS = ("oak", "walnut", "concrete", "polished", "slate", "epoxy", "carpet", "tile", "grass",
          "asphalt")
THEME_KEYS = {"id", "label", "description", "floor", "sky", "hemi", "sun", "ambient", "wall",
              "glass", "trim", "sign", "rooms"}


def _office(pack: Pack, raw: Any) -> dict[str, Any]:
    """Office content (``"office": "office.json"`` or inline):

        {"kits": {"furniture": {"scale": 2}},          # model folder -> model units to meters
         "models": {"furniture/desk": {"min": [x, y, z], "max": [x, y, z]}},  # models/<key>.glb
         "items": [{"key", "label", "category", "parts": [{"model", "x", "z", "y"?, "rot"?}],
                    "size": [w, d], "blocking", "seats": [{"x", "z", "facing"}], "thumb"?,
                    "workstation"?, "screen"?, "procedural"?, "special"?}],
         "rigs": {"kenney": {"scale" | "fit", "sitLift", "clips": {...}, "tint"?}},
         "characters": [{"key", "label", "group", "model", "thumb", "rig", "default"?, "fit"?}],
         "themes": [{"id", "label", "floor", "sky", ...}]}

    Paths are inside the pack. Item and character keys are global: a pack that adds to
    the office should prefix its keys with its id (``mypack:lamp``)."""
    if isinstance(raw, str):
        raw = json.loads(_inside(pack.path, raw).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise PackError("office must be an object or a .json file in the pack")
    kits = raw.get("kits") or {}
    models = raw.get("models") or {}
    for key, b in models.items():
        if not (isinstance(b, dict) and len(b.get("min") or []) == 3 and len(b.get("max") or []) == 3):
            raise PackError(f"model {key}: bounds need min and max [x, y, z]")
        if key.split("/")[0] not in kits:
            raise PackError(f"model {key}: no kit '{key.split('/')[0]}' (kits give each folder's scale)")
        if not _inside(pack.path, f"models/{key}.glb").is_file():
            raise PackError(f"model {key}: models/{key}.glb not found")
    for item in raw.get("items") or []:
        key = item.get("key")
        if not key or not item.get("label") or len(item.get("size") or []) != 2:
            raise PackError(f"item {key!r}: needs key, label and size [w, d]")
        for part in item.get("parts") or []:
            if part.get("model") not in models:
                raise PackError(f"item {key}: model {part.get('model')!r} isn't in this pack's models")
    rigs = raw.get("rigs") or {}
    for c in raw.get("characters") or []:
        if not c.get("key") or c.get("rig") not in rigs:
            raise PackError(f"character {c.get('key')!r}: needs a key and one of this pack's rigs")
        for f in ("model", "thumb"):
            if c.get(f) and not _inside(pack.path, c[f]).is_file():
                raise PackError(f"character {c['key']}: {c[f]} not found")
    for t in raw.get("themes") or []:
        if not SLUG.match(str(t.get("id", ""))) or not t.get("label"):
            raise PackError(f"theme {t.get('id')!r}: needs a lowercase id and a label")
        if t.get("floor", "oak") not in FLOORS:
            raise PackError(f"theme {t['id']}: floor must be one of {', '.join(FLOORS)}")
        if extra := set(t) - THEME_KEYS:
            raise PackError(f"theme {t['id']}: unknown fields {', '.join(sorted(extra))}")
    return {k: raw.get(k) or ({} if k in ("kits", "models", "rigs") else [])
            for k in ("kits", "models", "items", "rigs", "characters", "themes")}


def read_manifest(folder: Path) -> dict[str, Any] | None:
    for name in MANIFESTS:
        f = folder / name
        if f.is_file():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                raise PackError(f"bad {name}: {e}") from None
    return None


def _load_one(folder: Path, bundled: bool) -> Pack | None:
    try:
        manifest = read_manifest(folder)
    except PackError as e:
        return Pack(folder.name, folder.name, "?", "", folder, bundled, error=str(e))
    if manifest is None:
        return None
    pid = str(manifest.get("id") or folder.name)
    pack = Pack(pid, str(manifest.get("name") or pid), str(manifest.get("version") or "0"),
                str(manifest.get("description") or ""), folder, bundled,
                requires=[str(r) for r in manifest.get("requires") or []],
                module=str(manifest.get("module") or ""),
                required=bool(manifest.get("core")),
                default_on=manifest.get("enabled", True) is not False)
    try:
        if not SLUG.match(pid):
            raise PackError("id must be a lowercase slug")
        if manifest.get("office"):
            pack.office = _office(pack, manifest["office"])
        for raw in manifest.get("libraries") or []:
            pack.libraries.append(_library(pack, raw))
        for raw in manifest.get("assets") or []:
            pack.assets.append(_asset_entry(pack, raw))
        for pattern in manifest.get("templates") or []:
            for f in sorted(folder.glob(pattern)):
                pack.templates[f"{pid}/{f.stem}"] = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - a broken pack must not stop the engine
        log.exception("pack %s failed to load", pid)
        pack.error = f"{type(e).__name__}: {e}"
    return pack


def _load_module(pack: Pack) -> None:
    """Run a plugin's Python once per process (its tools stay registered until restart)."""
    if not pack.module or pack.error or pack.id in _modules_loaded:
        return
    try:
        path = _inside(pack.path, pack.module)
        if not path.is_file():
            raise PackError(f"module {pack.module!r} not found inside the pack")
        name = f"pantheon_pack_{pack.id.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        if hasattr(mod, "register"):
            mod.register(PackApi(pack))
        _modules_loaded.add(pack.id)
    except Exception as e:  # noqa: BLE001
        log.exception("pack %s: its module failed to load", pack.id)
        pack.error = f"{type(e).__name__}: {e}"


def load_all(force: bool = False) -> list[Pack]:
    global _loaded
    if _loaded and not force:
        return list(_PACKS.values())
    _loaded = True
    found: dict[str, Pack] = {}
    for root, bundled in pack_roots():
        if not root.is_dir():
            continue
        for folder in sorted(p for p in root.iterdir()
                             if p.is_dir() and not p.name.startswith((".", "_"))):
            pack = _load_one(folder, bundled)
            if pack is not None:
                found[pack.id] = pack  # the user's folder comes second and wins
    # Tools and agent kinds a module registered stay with the pack that registered them.
    for pid, old in _PACKS.items():
        if pid in found and pid in _modules_loaded:
            found[pid].tools = old.tools
            found[pid].agent_kinds = old.agent_kinds
            found[pid].computers = old.computers
    _PACKS.clear()
    _PACKS.update(found)
    _office_cache.clear()
    for pack in _PACKS.values():
        if pack.enabled:
            _load_module(pack)
        if pack.error is None:
            log.info("pack %s %s (%s) from %s", pack.id, pack.version,
                     ", ".join(pack.kinds()) or "empty", pack.path.parent)
    return list(_PACKS.values())


def all_packs() -> list[Pack]:
    return load_all()


def get(pid: str) -> Pack | None:
    load_all()
    return _PACKS.get(pid)


def usable() -> list[Pack]:
    return [p for p in load_all() if p.usable]


def is_usable(pid: str) -> bool:
    pack = get(pid)
    return pack is not None and pack.usable


def assets() -> list[dict[str, Any]]:
    return [a for p in usable() for a in p.assets]


def templates() -> dict[str, dict[str, Any]]:
    return {k: v for p in usable() for k, v in p.templates.items()}


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


# --- the office ---------------------------------------------------------------------------


def office_packs() -> list[tuple[Pack, dict[str, Any]]]:
    """Enabled packs with office content, shipped ones first (a later key wins)."""
    ordered = sorted(usable(), key=lambda p: (not p.bundled, p.id))
    return [(p, p.office) for p in ordered if p.office]


_office_cache: dict[str, Any] = {}


def office_items() -> dict[str, dict[str, Any]]:
    """Every placeable item by key, as world building needs it: label, category, footprint,
    whether it blocks, seat count, whether it's a desk. Kept until the packs change."""
    if (cached := _office_cache.get("items")) is not None:
        return cached
    out: dict[str, dict[str, Any]] = {}
    for _, office in office_packs():
        for item in office["items"]:
            out[item["key"]] = {
                "label": item["label"], "category": item.get("category") or "Props",
                "size": [round(float(item["size"][0]), 2), round(float(item["size"][1]), 2)],
                "blocking": item.get("blocking") is not False,
                "seats": len(item.get("seats") or []), "workstation": bool(item.get("workstation"))}
    _office_cache["items"] = out
    return out


def theme_ids() -> list[str]:
    return list(dict.fromkeys(t["id"] for _, office in office_packs() for t in office["themes"]))


# --- libraries ----------------------------------------------------------------------------


def libraries() -> dict[str, Library]:
    """Every library of an enabled pack, by id."""
    return {lib.id: lib for p in usable() for lib in p.libraries}


def chosen(selection: list[str] | None) -> list[Library]:
    """The libraries for a selection (an org's or agent's list of library ids; None means
    every enabled one). Core libraries are always included, and so is whatever a chosen
    library requires."""
    avail = libraries()
    want = list(avail) if selection is None else \
        [lid for lid, lib in avail.items() if lib.core] + list(selection)
    out: dict[str, Library] = {}
    while want:
        lid = want.pop(0)
        if lid in out or lid not in avail:
            continue
        out[lid] = avail[lid]
        want.extend(avail[lid].requires)
    return list(out.values())


def selection_for(org_settings: dict[str, Any] | None, agent_libraries: list[str] | None) -> \
        list[str] | None:
    """An agent's own list wins over its organization's; None means every library."""
    if agent_libraries is not None:
        return list(agent_libraries)
    sel = (org_settings or {}).get("libraries")
    return None if sel is None else [str(s) for s in sel]


def import_map(libs: list[Library], base: str) -> dict[str, str]:
    """Import-map entries for these libraries, served under ``base`` (the Stage library
    URL): ``<base>/<library id>/<file>``."""
    out: dict[str, str] = {}
    for lib in libs:
        for name, rel in lib.imports.items():
            out[name] = f"{base}/{lib.id}/{rel}"
    return out


def library_file(lib_id: str, rel: str) -> Path | None:
    """A file of an enabled library's pack (any file: a module imports its neighbours)."""
    lib = libraries().get(lib_id)
    if lib is None:
        return None
    root = lib.root.resolve()
    target = (root / rel).resolve()
    return target if root in target.parents and target.is_file() else None


# --- switching packs on and off, adding and removing them ---------------------------------


async def load_state() -> None:
    """Which packs the user switched off (kept in the settings table)."""
    from app.core.db import SessionLocal
    from app.models import Setting

    async with SessionLocal() as session:
        row = await session.get(Setting, STATE_KEY)
    _disabled.clear()
    _disabled.update((row.value or {}).get("disabled", []) if row else [])
    _enabled.clear()
    _enabled.update((row.value or {}).get("enabled", []) if row else [])
    _office_cache.clear()


async def _save_state() -> None:
    from app.core.db import SessionLocal
    from app.models import Setting

    async with SessionLocal() as session:
        row = await session.get(Setting, STATE_KEY)
        value = {"disabled": sorted(_disabled), "enabled": sorted(_enabled)}
        if row is None:
            session.add(Setting(key=STATE_KEY, value=value))
        else:
            row.value = value
        await session.commit()


async def set_enabled(pid: str, on: bool) -> Pack:
    pack = get(pid)
    if pack is None:
        raise PackError(f"no pack '{pid}'")
    if not on and pack.core:
        raise PackError(f"{pack.name} is part of the Stage itself; it can be replaced by a "
                        "newer pack with the same id, but not switched off")
    if on:
        _disabled.discard(pid)
        _enabled.add(pid)
        _office_cache.clear()
        _load_module(pack)
    else:
        _disabled.add(pid)
        _enabled.discard(pid)
        _office_cache.clear()
    await _save_state()
    return pack


def install_folder(src: Path) -> Pack:
    """Copy a pack folder into the user's packs (replacing one with the same id)."""
    manifest = read_manifest(src)
    if manifest is None:
        raise PackError("that folder has no pack.json")
    pid = str(manifest.get("id") or src.name)
    if not SLUG.match(pid):
        raise PackError(f"pack id {pid!r} must be a lowercase slug")
    dest = packs_dir() / pid
    tmp = dest.with_name(pid + ".adding")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src, tmp, ignore=shutil.ignore_patterns("__pycache__", ".git"))
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    load_all(force=True)
    pack = get(pid)
    assert pack is not None
    return pack


def install_zip(data: bytes) -> Pack:
    """A pack from a .zip: pack.json at its top level or in one folder inside it."""
    import io
    import tempfile
    import zipfile

    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(io.BytesIO(data)) as z:
        root = Path(tmp).resolve()
        for member in z.namelist():
            if root not in (root / member).resolve().parents and (root / member).resolve() != root:
                raise PackError(f"the zip has an unsafe path: {member}")
        z.extractall(root)
        candidates = [root] + [p for p in root.iterdir() if p.is_dir()]
        src = next((c for c in candidates if any((c / m).is_file() for m in MANIFESTS)), None)
        if src is None:
            raise PackError("no pack.json in the zip (at the top or in one folder)")
        return install_folder(src)


def remove(pid: str) -> None:
    """Delete one of the user's packs (shipped packs can only be switched off)."""
    pack = get(pid)
    if pack is None:
        raise PackError(f"no pack '{pid}'")
    if pack.bundled:
        raise PackError(f"{pack.name} ships with Pantheon; switch it off instead")
    shutil.rmtree(pack.path)
    load_all(force=True)
