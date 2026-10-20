"""Any npm library as a Stage library pack, fetched on demand.

``install("chart.js@4")`` asks esm.sh for the package built as one ES module (its own
dependencies bundled in), downloads it and every module it imports, rewrites those
imports to the downloaded files, and adds the result to the user's packs. Pages then
import it by its npm name, offline, and the site packager copies it like any library.

Dependencies that another library pack already provides (three, d3, gsap…) stay
external, so a page has one three.js and not two. A three.js add-on the three pack
doesn't ship (``three/examples/jsm/…``) is fetched into the new pack, for the same
three.js version, and mapped by its exact name.

Agents read the package's README as the library's docs.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from app import packs

ESM = "https://esm.sh"
JSDELIVR = "https://cdn.jsdelivr.net/npm"
REGISTRY = "https://registry.npmjs.org"
MAX_FILES = 400
MAX_BYTES = 40_000_000
DOCS_CHARS = 6000
# import "x", import("x"), from "x" (export … from too); minified code has no spaces.
SPECIFIER = re.compile(r"""(\bimport\s*\(?\s*|\bfrom\s*)(["'])([^"'\n]+)\2""")
SPEC = re.compile(r"^(@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*(@[\w.^~<>=*-]+)?$", re.IGNORECASE)
THREE_ADDON = re.compile(r"^three/(?:examples/jsm|addons)/(.+)$")


class NpmError(ValueError):
    pass


@dataclass
class Fetched:
    files: dict[str, bytes] = field(default_factory=dict)  # pack path -> content
    bytes: int = 0
    bare: set[str] = field(default_factory=set)  # bare imports left for the import map

    def add(self, path: str, data: bytes) -> None:
        if len(self.files) >= MAX_FILES:
            raise NpmError(f"the library has more than {MAX_FILES} modules")
        self.bytes += len(data)
        if self.bytes > MAX_BYTES:
            raise NpmError(f"the library is larger than {MAX_BYTES // 1_000_000} MB")
        self.files[path] = data


def parse_spec(spec: str) -> tuple[str, str]:
    """"chart.js@4" -> ("chart.js", "4"); "@scope/pkg" -> ("@scope/pkg", "latest")."""
    spec = spec.strip()
    if not SPEC.match(spec):
        raise NpmError(f"{spec!r} isn't an npm package name (like chart.js, or chart.js@4)")
    at = spec.rfind("@")
    if at > 0:
        return spec[:at], spec[at + 1:]
    return spec, "latest"


def library_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower().lstrip("@")).strip("-")[:64]


def _local(url: str) -> str:
    """Where a fetched module goes in the pack: its URL path under mods/."""
    parts = urlsplit(url)
    path = parts.path.lstrip("/")
    if parts.query:
        stem, ext = posixpath.splitext(path)
        path = f"{stem}.{hashlib.sha1(parts.query.encode()).hexdigest()[:8]}{ext or '.js'}"
    if not path.endswith((".js", ".mjs", ".css", ".json", ".wasm")):
        path += ".js"
    safe = "/".join(re.sub(r"[^\w.@+-]", "_", seg) for seg in path.split("/") if seg not in ("", ".", ".."))
    return f"mods/{parts.netloc.replace(':', '_')}/{safe}"


def _relative(from_path: str, to_path: str) -> str:
    rel = posixpath.relpath(to_path, posixpath.dirname(from_path) or ".")
    return rel if rel.startswith(".") else f"./{rel}"


async def _crawl(client: httpx.AsyncClient, url: str, as_path: str, out: Fetched,
                 externals: set[str], seen: dict[str, str]) -> None:
    """Fetch a module and, recursively, what it imports from the same host; imports
    are rewritten to the local copies. Bare names are left for the import map."""
    seen[url] = as_path
    r = await client.get(url)
    if r.status_code != 200:
        raise NpmError(f"couldn't fetch {url} ({r.status_code})")
    text = r.text
    host = urlsplit(url).netloc
    pending: list[tuple[str, str]] = []

    def rewrite(m: re.Match[str]) -> str:
        spec = m.group(3)
        if spec.startswith(("/", "./", "../", "https://", "http://")):
            target = urljoin(url, spec)
            if urlsplit(target).netloc != host:
                return m.group(0)  # another host: the page fetches it when online
            local = seen.get(target)
            if local is None:
                local = _local(target)
                seen[target] = local
                pending.append((target, local))
            return f"{m.group(1)}{m.group(2)}{_relative(as_path, local)}{m.group(2)}"
        out.bare.add(spec)
        return m.group(0)

    if as_path.endswith((".js", ".mjs")):
        text = SPECIFIER.sub(rewrite, text)
    out.add(as_path, text.encode("utf-8"))
    for target, local in pending:
        await _crawl(client, target, local, out, externals, seen)


async def _readme(client: httpx.AsyncClient, name: str, version: str) -> str:
    for f in ("README.md", "readme.md", "Readme.md"):
        r = await client.get(f"{JSDELIVR}/{name}@{version}/{f}")
        if r.status_code == 200:
            text = r.text
            # Badges and images mean nothing to a model.
            text = re.sub(r"^\s*(\[!\[|!\[|<img|<p align|<a href).*$", "", text, flags=re.MULTILINE)
            text = re.sub(r"<[^>\n]+>", "", text)  # leftover HTML (</p>, <br>, <div …>)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            return text[:DOCS_CHARS] + ("\n…" if len(text) > DOCS_CHARS else "")
    return ""


def _resolves(spec: str) -> bool:
    """Whether an installed library's import map covers this bare import."""
    names = {imp for lib in packs.libraries().values() for imp in lib.imports}
    return spec in names or any(n.endswith("/") and spec.startswith(n) for n in names)


def _three_npm_version() -> str | None:
    lib = packs.libraries().get("three")
    pack = packs.get(lib.pack_id) if lib else None
    m = re.match(r"r(\d+)", pack.version) if pack else None
    return f"0.{m.group(1)}.0" if m else None


async def install(spec: str) -> tuple[packs.Pack, list[str]]:
    """Fetch an npm package as a library pack; returns the pack and any warnings."""
    name, wanted = parse_spec(spec)
    warnings: list[str] = []
    async with httpx.AsyncClient(timeout=60, follow_redirects=True,
                                 headers={"User-Agent": "Pantheon"}) as client:
        r = await client.get(f"{REGISTRY}/{name.replace('/', '%2F')}/{wanted}")
        if r.status_code != 200:
            raise NpmError(f"npm has no package {name}@{wanted}")
        meta = r.json()
        version = str(meta["version"])
        # Libraries already installed stay shared: three, d3, gsap… are imported by name.
        provided = {imp.rstrip("/"): lib for lib in packs.libraries().values()
                    for imp in lib.imports if "/" not in imp.rstrip("/")}
        deps = {**(meta.get("dependencies") or {}), **(meta.get("peerDependencies") or {})}
        externals = {d for d in deps if d in provided and d != name}
        query = "bundle&target=es2022" + (f"&external={','.join(sorted(externals))}"
                                          if externals else "")
        out = Fetched()
        seen: dict[str, str] = {}
        await _crawl(client, f"{ESM}/{name}@{version}?{query}", "index.js", out, externals, seen)

        imports = {name: "index.js"}
        requires = sorted({provided[e].id for e in externals})
        # three.js add-ons the three pack doesn't ship come with this pack, same version.
        three_root = packs.libraries()["three"].root if "three" in provided else None
        three_version = _three_npm_version()
        for bare in sorted(out.bare):
            m = THREE_ADDON.match(bare)
            if m and three_root is not None and three_version is not None and \
                    not (three_root / "addons" / m.group(1)).is_file():
                local = f"three-addons/{m.group(1)}"
                await _crawl(client, f"{JSDELIVR}/three@{three_version}/examples/jsm/{m.group(1)}",
                             local, out, externals, seen)
                imports[f"three/examples/jsm/{m.group(1)}"] = local
                imports[f"three/addons/{m.group(1)}"] = local
            elif not _resolves(bare):
                warnings.append(f"it imports {bare!r}, which no installed library provides, so "
                                "a page using it won't load")
        readme = await _readme(client, name, version)

    lid = library_id(name)
    docs = "\n".join(filter(None, [
        f'`import … from "{name}"` ({name} {version} from npm)',
        str(meta.get("description") or ""),
        f"Homepage: {meta['homepage']}" if meta.get("homepage") else "",
        "Some README examples load it from a CDN or a <script> tag; on the Stage, import it "
        f'by name instead: import … from "{name}".',
        "", readme,
    ])).strip()
    manifest = {
        "id": lid, "name": name, "version": version,
        "description": str(meta.get("description") or f"{name} from npm"),
        "license": str(meta.get("license") or ""), "source": f"npm:{name}@{version}",
        "requires": requires,
        "libraries": [{"id": lid, "name": name,
                       "summary": str(meta.get("description") or name)[:200],
                       "imports": imports, "docs": "docs.md", "requires": requires}],
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / lid
        for path, data in out.files.items():
            f = root / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
        (root / "docs.md").write_text(docs, encoding="utf-8")
        (root / "pack.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        pack = packs.install_folder(root)
    if pack.error:
        raise NpmError(f"the pack didn't load: {pack.error}")
    return pack, warnings
