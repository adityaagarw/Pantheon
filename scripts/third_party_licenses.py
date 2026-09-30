"""Regenerate THIRD_PARTY_NOTICES.md from the installed dependencies.

    cd server && uv run python ../scripts/third_party_licenses.py

Needs the backend's virtualenv (for Python package metadata) and web/node_modules
(npm install). Lists the runtime dependency closure only; dev and test tooling is
not shipped and not listed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib import metadata as md
from pathlib import Path

import tomllib
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "THIRD_PARTY_NOTICES.md"
MARKER = "<!-- generated below: do not edit by hand -->"

# Packages whose metadata doesn't declare a license (verified from their LICENSE files).
KNOWN = {"py_rust_stemmers": "MIT", "khroma": "MIT", "webgl-constants": "MIT"}


def python_rows() -> list[tuple[str, str, str, str]]:
    project = tomllib.loads((ROOT / "server" / "pyproject.toml").read_text(encoding="utf-8"))
    dists = {canonicalize_name(d.metadata["Name"]): d for d in md.distributions()}
    seen: set[str] = set()
    stack = [(Requirement(r), ("",)) for r in project["project"]["dependencies"]]
    while stack:
        req, extras = stack.pop()
        if req.marker and not any(req.marker.evaluate({"extra": e}) for e in extras):
            continue
        dist = dists.get(canonicalize_name(req.name))
        key = f"{canonicalize_name(req.name)}[{','.join(sorted(req.extras))}]"
        if dist is None or key in seen:
            continue
        seen.add(key)
        stack += [(Requirement(r), tuple(req.extras) or ("",)) for r in dist.requires or []]

    def license_of(dist: md.Distribution) -> str:
        m = dist.metadata
        if m.get("License-Expression"):
            return m["License-Expression"]
        classifiers = [c.split(" :: ")[-1] for c in m.get_all("Classifier") or []
                       if c.startswith("License ::") and not c.endswith("OSI Approved")]
        if classifiers:
            return " / ".join(classifiers)
        text = (m.get("License") or "").strip()
        return KNOWN.get(m["Name"], text.splitlines()[0][:60] if text else "UNKNOWN")

    def url_of(dist: md.Distribution) -> str:
        m = dist.metadata
        urls = [u.split(",", 1)[1].strip() for u in m.get_all("Project-URL") or []]
        return m.get("Home-page") or next((u for u in urls if "github" in u), urls[0] if urls else "")

    names = sorted({k.split("[")[0] for k in seen})
    return [(dists[n].metadata["Name"], dists[n].version, license_of(dists[n]), url_of(dists[n]))
            for n in names]


def npm_rows() -> list[tuple[str, str, str, str]]:
    web = ROOT / "web"
    paths = subprocess.run("npm ls --omit=dev --all --parseable", cwd=web, shell=True,
                           capture_output=True, text=True, check=False).stdout.split()
    rows: dict[tuple[str, str], tuple[str, str, str, str]] = {}
    for p in paths:
        pj = Path(p) / "package.json"
        if not pj.exists() or Path(p).resolve() == web.resolve():
            continue
        d = json.loads(pj.read_text(encoding="utf-8"))
        lic = d.get("license") or d.get("licenses") or KNOWN.get(d["name"], "UNKNOWN")
        if isinstance(lic, dict):
            lic = lic.get("type", "UNKNOWN")
        if isinstance(lic, list):
            lic = " OR ".join(x.get("type", "?") if isinstance(x, dict) else str(x) for x in lic)
        repo = d.get("repository")
        repo = repo.get("url", "") if isinstance(repo, dict) else (repo or d.get("homepage") or "")
        repo = repo.removeprefix("git+").removesuffix(".git")
        if repo.startswith("github:") or (repo and "://" not in repo and repo.count("/") == 1):
            repo = "https://github.com/" + repo.removeprefix("github:")
        rows[(d["name"], d.get("version", ""))] = (d["name"], d.get("version", ""), str(lic), repo)
    return sorted(rows.values())


def table(rows: list[tuple[str, str, str, str]]) -> str:
    lines = ["| Package | Version | License |", "|---|---|---|"]
    for name, version, lic, url in rows:
        label = f"[{name}]({url})" if url.startswith("http") else name
        lines.append(f"| {label} | {version} | {lic.replace('|', '/')} |")
    return "\n".join(lines)


def main() -> None:
    head = OUT.read_text(encoding="utf-8").split(MARKER)[0] if OUT.exists() else ""
    if not head:
        sys.exit("THIRD_PARTY_NOTICES.md needs its hand-written header and the marker line")
    py, js = python_rows(), npm_rows()
    body = (f"{MARKER}\n\n## Python packages (backend runtime, {len(py)})\n\n{table(py)}\n\n"
            f"## npm packages (frontend runtime, {len(js)})\n\n"
            "Platform-specific binaries (e.g. `@img/sharp-*`) vary by operating system; the list "
            "reflects the machine it was generated on.\n\n"
            f"{table(js)}\n")
    OUT.write_text(head + body, encoding="utf-8", newline="\n")
    unknown = [r for r in py + js if r[2] == "UNKNOWN"]
    print(f"wrote {OUT.name}: {len(py)} Python, {len(js)} npm"
          + (f"; UNKNOWN licenses: {[r[0] for r in unknown]}" if unknown else ""))


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
