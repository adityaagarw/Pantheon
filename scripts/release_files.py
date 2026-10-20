"""Gather the installers CI built into one folder for a GitHub release, with ``latest.json``
for the in-app updater (tauri-plugin-updater).

    python scripts/release_files.py <artifacts> <out> --tag v2.0.0 --repo owner/name

<artifacts> has a folder per platform, as the release workflow downloads them:
windows-x86_64, darwin-aarch64, darwin-x86_64, linux-x86_64, each with that build's
target/release/bundle tree. Every file gets a name unique across platforms (both Macs
build "Pantheon Studio.app.tar.gz"), and the updater gets each platform's signed package.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

# What users download, and what the updater installs (signed: <file>.sig next to it).
INSTALLERS = ("*-setup.exe", "*.dmg", "*.deb", "*.rpm", "*.AppImage")
UPDATER = {"windows": "*-setup.exe", "darwin": "*.app.tar.gz", "linux": "*.AppImage"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("artifacts", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--notes", default="")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    version = a.tag.lstrip("v")
    platforms: dict[str, dict[str, str]] = {}
    for folder in sorted(p for p in a.artifacts.iterdir() if p.is_dir()):
        key = folder.name  # e.g. darwin-aarch64
        os_name, arch = key.split("-", 1)
        files = [f for pattern in INSTALLERS for f in folder.rglob(pattern)]
        package = next(iter(sorted(folder.rglob(UPDATER[os_name]))), None)
        if package is not None and package not in files:
            files.append(package)
        for f in files:
            name = f.name.replace(" ", ".")
            if os_name == "darwin" and arch not in name:
                name = name.replace(".app.tar.gz", f"_{arch}.app.tar.gz")
            shutil.copy2(f, a.out / name)
            sig = f.with_name(f.name + ".sig")
            if f == package and sig.is_file():
                platforms[key] = {
                    "signature": sig.read_text(encoding="utf-8").strip(),
                    "url": f"https://github.com/{a.repo}/releases/download/{a.tag}/{name}",
                }
            print(f"{key}: {name}")
    if platforms:
        manifest = {"version": version, "notes": a.notes,
                    "pub_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "platforms": platforms}
        (a.out / "latest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"latest.json: {', '.join(platforms)}")
    else:
        print("no signed packages: latest.json skipped (set TAURI_SIGNING_PRIVATE_KEY)")


if __name__ == "__main__":
    main()
