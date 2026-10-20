"""Stage pages packaged as one static website.

The pages are rendered as they are on the Stage, with the library copied next to them
and the import map pointing there, so the folder works on any static host (and the zip
is that folder). ``pantheon.open`` goes to the page's file instead of asking Pantheon;
``site.js`` lists the pages for it.
"""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from pathlib import Path

from app import packs
from app.core.config import settings
from app.models import StagePage
from app.services import stage


class SiteError(ValueError):
    pass


def sites_root() -> Path:
    return settings.data_path / "sites"


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "page"


def url(org_id: str, slug: str) -> str:
    return f"{settings.public_url.rstrip('/')}/api/v1/sites/{org_id}/{slug}/"


def site_file(org_id: str, path: str) -> Path | None:
    root = (sites_root() / org_id).resolve()
    target = (root / path).resolve()
    return target if root in target.parents and target.is_file() else None


def resolve(pages: list[StagePage], target: str) -> StagePage | None:
    """A page by id, exact title, or title start ("Ch 4" never matches "Ch 41")."""
    t = target.strip().lower()
    for p in pages:
        if p.id == target.strip():
            return p
    for p in pages:
        if p.title.strip().lower() == t:
            return p
    for p in pages:
        title = p.title.strip().lower()
        if title.startswith(t) and not re.match(r"[a-z0-9]", title[len(t):len(t) + 1]):
            return p
    return None


def _page_html(p: StagePage, libs: list[packs.Library]) -> str:
    doc = stage.render(p, libs)
    # The library lives next to the pages; site.js (the page list) loads before anything.
    # "./": an import map ignores "lib/x.js" as a bare name, and then nothing loads.
    doc = doc.replace(f"{stage.LIB_URL}/", "./lib/")
    return re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + '<script src="site.js"></script>',
                  doc, count=1, flags=re.IGNORECASE)


async def package(org_id: str, name: str, targets: list[str], index: str = "") -> dict:
    # Newest first, so a title start picks the latest; archived pages aren't part of a site.
    pages = [p for p in await stage.for_org(org_id) if p.folder != stage.ARCHIVE]
    chosen: list[StagePage] = []
    for t in targets or [p.id for p in reversed(pages)]:
        p = resolve(pages, t)
        if p is None:
            raise SiteError(f"no stage page '{t}'")
        if p not in chosen:
            chosen.append(p)
    if not chosen:
        raise SiteError("there are no pages to package")
    home = resolve(chosen, index) if index else chosen[0]
    if home is None:
        raise SiteError(f"the index page '{index}' isn't among the pages")

    slug = slugify(name)
    out = sites_root() / org_id / slug
    tmp = out.with_name(slug + ".building")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    # The Stage's own files, and each library pack a page may use, at lib/<library id>/.
    shutil.copytree(stage.LIB_DIR, tmp / "lib")
    page_libs = {p.id: await stage.libraries_for(org_id, p.agent_id) for p in chosen}
    for lib in {lib.id: lib for libs in page_libs.values() for lib in libs}.values():
        shutil.copytree(lib.root, tmp / "lib" / lib.id, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("pack.json", "plugin.json", "docs.md",
                                                      "*.py", "__pycache__"))

    files: dict[str, str] = {}
    for p in chosen:
        f = "index.html" if p is home else f"{slugify(p.title)}.html"
        while f in files.values():
            f = f[:-5] + "-2.html"
        files[p.id] = f
        (tmp / f).write_text(_page_html(p, page_libs[p.id]), encoding="utf-8")
    listing = [{"id": p.id, "title": p.title, "file": files[p.id]} for p in chosen]
    (tmp / "site.js").write_text(
        "globalThis.__pantheonSite = " + json.dumps({"name": name, "pages": listing}) + ";\n",
        encoding="utf-8")

    shutil.rmtree(out, ignore_errors=True)
    tmp.rename(out)
    archive = out.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(out.rglob("*")):
            if f.is_file():
                z.write(f, Path(slug) / f.relative_to(out))
    return {"url": url(org_id, slug), "zip": url(org_id, slug).rstrip("/") + ".zip",
            "pages": listing}
