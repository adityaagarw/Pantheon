"""3D assets added at runtime: imported GLB models and procedural models.

Procedural assets are small lists of colored primitives (boxes, cylinders,
spheres, cones) — enough for Zeus to "build" a prop or a piece of furniture
on request. Imported assets are binary glTF (.glb) files fetched from a URL
(e.g. a CC0 model found with web_search) and normalized to a real-world size.

Every asset is described to the browser in one shape (see ``to_dict``) so the
office renders DB assets and plugin assets the same way.
"""

from __future__ import annotations

import json
import math
import re
import struct
from typing import Any

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.events import bus
from app.models import Asset

SHAPES = ("box", "cylinder", "sphere", "cone")
MAX_PARTS = 64
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class AssetError(ValueError):
    pass


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]
    return s or "asset"


# --- procedural -----------------------------------------------------------------------


def _vec(v: Any, n: int, name: str, lo: float = -50, hi: float = 50) -> list[float]:
    if not isinstance(v, list | tuple) or len(v) != n:
        raise AssetError(f"{name} must be a list of {n} numbers")
    out = [float(x) for x in v]
    if any(not math.isfinite(x) or x < lo or x > hi for x in out):
        raise AssetError(f"{name} values must be between {lo} and {hi}")
    return out


def validate_parts(parts: Any) -> list[dict[str, Any]]:
    """Check a procedural model: [{shape, size:[w,h,d], pos:[x,y,z], rot?:[x,y,z], color}]."""
    if not isinstance(parts, list) or not parts:
        raise AssetError("parts must be a non-empty list")
    if len(parts) > MAX_PARTS:
        raise AssetError(f"at most {MAX_PARTS} parts")
    out = []
    for i, p in enumerate(parts):
        if not isinstance(p, dict):
            raise AssetError(f"part {i} must be an object")
        shape = p.get("shape")
        if shape not in SHAPES:
            raise AssetError(f"part {i}: shape must be one of {', '.join(SHAPES)}")
        color = str(p.get("color") or "#9aa0a6")
        if not HEX.match(color):
            raise AssetError(f"part {i}: color must be #rrggbb")
        out.append({"shape": shape, "size": _vec(p.get("size"), 3, f"part {i} size", 0.001, 30),
                    "pos": _vec(p.get("pos", [0, 0, 0]), 3, f"part {i} pos"),
                    "rot": _vec(p.get("rot", [0, 0, 0]), 3, f"part {i} rot", -7, 7),
                    "color": color})
    return out


def parts_size(parts: list[dict[str, Any]]) -> list[float]:
    """Footprint [w, d, h] in meters (parts are centered on the item's origin)."""
    xs = [abs(p["pos"][0]) + p["size"][0] / 2 for p in parts]
    zs = [abs(p["pos"][2]) + p["size"][2] / 2 for p in parts]
    ys = [p["pos"][1] + p["size"][1] / 2 for p in parts]
    return [round(2 * max(xs), 3), round(2 * max(zs), 3), round(max(ys), 3)]


# --- glb ----------------------------------------------------------------------------


def _mat_mul(a: list[float], b: list[float]) -> list[float]:
    # Column-major 4x4 (glTF convention).
    return [sum(a[k * 4 + r] * b[c * 4 + k] for k in range(4)) for c in range(4) for r in range(4)]


def _node_matrix(node: dict[str, Any]) -> list[float]:
    if "matrix" in node:
        return [float(x) for x in node["matrix"]]
    tx, ty, tz = node.get("translation", [0, 0, 0])
    qx, qy, qz, qw = node.get("rotation", [0, 0, 0, 1])
    sx, sy, sz = node.get("scale", [1, 1, 1])
    r = [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy + qz * qw), 2 * (qx * qz - qy * qw),
         2 * (qx * qy - qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz + qx * qw),
         2 * (qx * qz + qy * qw), 2 * (qy * qz - qx * qw), 1 - 2 * (qx * qx + qy * qy)]
    return [r[0] * sx, r[1] * sx, r[2] * sx, 0, r[3] * sy, r[4] * sy, r[5] * sy, 0,
            r[6] * sz, r[7] * sz, r[8] * sz, 0, tx, ty, tz, 1]


def glb_bounds(data: bytes) -> dict[str, list[float]]:
    """Axis-aligned bounds of a .glb's default scene, in the model's own units."""
    if len(data) < 20 or data[:4] != b"glTF":
        raise AssetError("not a binary glTF (.glb) file")
    _, version, _ = struct.unpack_from("<4sII", data, 0)
    if version != 2:
        raise AssetError("only glTF 2.0 is supported")
    length, ctype = struct.unpack_from("<II", data, 12)
    if ctype != 0x4E4F534A:  # "JSON"
        raise AssetError("malformed .glb (no JSON chunk)")
    gltf = json.loads(data[20:20 + length].decode("utf-8"))
    nodes = gltf.get("nodes") or []
    meshes = gltf.get("meshes") or []
    accessors = gltf.get("accessors") or []
    scenes = gltf.get("scenes") or [{"nodes": list(range(len(nodes)))}]
    roots = scenes[gltf.get("scene", 0)].get("nodes", [])
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    identity = [1.0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]

    def visit(idx: int, parent: list[float], depth: int) -> None:
        if depth > 64 or idx >= len(nodes):
            return
        node = nodes[idx]
        m = _mat_mul(parent, _node_matrix(node))
        if "mesh" in node and node["mesh"] < len(meshes):
            for prim in meshes[node["mesh"]].get("primitives", []):
                acc = accessors[prim.get("attributes", {}).get("POSITION", -1)] \
                    if prim.get("attributes", {}).get("POSITION") is not None else None
                if not acc or "min" not in acc or "max" not in acc:
                    continue
                mn, mx = acc["min"], acc["max"]
                for cx in (mn[0], mx[0]):
                    for cy in (mn[1], mx[1]):
                        for cz in (mn[2], mx[2]):
                            p = [m[0] * cx + m[4] * cy + m[8] * cz + m[12],
                                 m[1] * cx + m[5] * cy + m[9] * cz + m[13],
                                 m[2] * cx + m[6] * cy + m[10] * cz + m[14]]
                            for k in range(3):
                                lo[k] = min(lo[k], p[k])
                                hi[k] = max(hi[k], p[k])
        for child in node.get("children", []):
            visit(child, m, depth + 1)

    for r in roots:
        visit(r, identity, 0)
    if not math.isfinite(lo[0]):
        raise AssetError("the model has no geometry")
    return {"min": [round(x, 5) for x in lo], "max": [round(x, 5) for x in hi]}


def fit(bounds: dict[str, list[float]], size_m: Any = None,
        height_m: Any = None) -> tuple[float, list[float]]:
    """Scale that brings the model to the requested size; returns (scale, [w, d, h])."""
    ext = [bounds["max"][k] - bounds["min"][k] for k in range(3)]
    if height_m:
        scale = float(height_m) / max(ext[1], 1e-6)
    elif size_m:
        scale = float(size_m) / max(max(ext), 1e-6)
    else:
        scale = 1.0
    if not math.isfinite(scale) or scale <= 0:
        raise AssetError("invalid size")
    return round(scale, 6), [round(ext[0] * scale, 3), round(ext[2] * scale, 3),
                             round(ext[1] * scale, 3)]


# --- records -------------------------------------------------------------------------


def to_dict(a: Asset) -> dict[str, Any]:
    base = {"key": f"asset:{a.id}", "id": a.id, "label": a.label, "category": a.category,
            "kind": a.kind, "size": a.size, "scale": a.scale, "carryable": a.carryable,
            "blocking": a.blocking, "description": a.description, "license": a.license,
            "sourceUrl": a.source_url, "source": a.created_by}
    if a.kind == "procedural":
        return base | {"parts": (a.spec or {}).get("parts", [])}
    return base | {"url": f"/api/v1/assets/{a.id}/file", "bounds": (a.spec or {}).get("bounds")}


async def all_assets() -> list[dict[str, Any]]:
    from app import plugins

    async with SessionLocal() as session:
        rows = (await session.execute(select(Asset).order_by(Asset.created_at))).scalars().all()
    return [to_dict(a) for a in rows] + plugins.assets()


async def resolve_key(ref: str) -> dict[str, Any] | None:
    """Find an asset by key ("asset:x", "plugin:p/x"), id or label."""
    ref_l = ref.strip().lower()
    for a in await all_assets():
        if ref_l in (a["key"].lower(), a["id"].lower(), a["label"].lower()):
            return a
    return None


async def _unique_id(base: str) -> str:
    base = slugify(base)
    async with SessionLocal() as session:
        n, cand = 1, base
        while await session.get(Asset, cand) is not None:
            n += 1
            cand = f"{base}-{n}"
    return cand


async def create_procedural(label: str, parts: Any, *, category: str = "Props",
                            carryable: bool = False, description: str = "",
                            created_by: str = "user") -> Asset:
    spec = validate_parts(parts)
    size = parts_size(spec)
    asset = Asset(id=await _unique_id(label), label=label.strip()[:80] or "Asset",
                  category=category[:40] or "Props", kind="procedural", spec={"parts": spec},
                  size=size, scale=1.0, carryable=carryable, blocking=not carryable,
                  description=description, created_by=created_by)
    async with SessionLocal() as session:
        session.add(asset)
        await session.commit()
    await bus.publish("asset.created", to_dict(asset))
    return asset


async def import_glb(url: str, label: str, *, size_m: float | None = None,
                     height_m: float | None = None, category: str = "Props",
                     carryable: bool = False, license_: str = "", description: str = "",
                     created_by: str = "user") -> Asset:
    if not re.match(r"^https?://", url):
        raise AssetError("url must be http(s)")
    limit = settings.max_asset_bytes
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True,
                                     headers={"User-Agent": "Pantheon/2"}) as client:
            async with client.stream("GET", url) as res:
                if res.status_code >= 400:
                    raise AssetError(f"download failed: HTTP {res.status_code}")
                chunks, total = [], 0
                async for chunk in res.aiter_bytes():
                    total += len(chunk)
                    if total > limit:
                        raise AssetError(f"file is larger than {limit // 1_000_000} MB")
                    chunks.append(chunk)
    except httpx.HTTPError as e:
        raise AssetError(f"download failed: {e}") from None
    data = b"".join(chunks)
    bounds = glb_bounds(data)
    scale, size = fit(bounds, size_m, height_m)
    aid = await _unique_id(label)
    settings.assets_path.mkdir(parents=True, exist_ok=True)
    (settings.assets_path / f"{aid}.glb").write_bytes(data)
    asset = Asset(id=aid, label=label.strip()[:80] or aid, category=category[:40] or "Props",
                  kind="glb", file=f"{aid}.glb", spec={"bounds": bounds}, size=size, scale=scale,
                  carryable=carryable, blocking=not carryable, description=description,
                  source_url=url, license=license_, created_by=created_by)
    async with SessionLocal() as session:
        session.add(asset)
        await session.commit()
    await bus.publish("asset.created", to_dict(asset))
    return asset


async def delete_asset(aid: str) -> None:
    async with SessionLocal() as session:
        a = await session.get(Asset, aid)
        if a is None:
            raise AssetError("asset not found")
        await session.delete(a)
        await session.commit()
    if a.file:
        (settings.assets_path / a.file).unlink(missing_ok=True)
    await bus.publish("asset.deleted", {"key": f"asset:{aid}"})
