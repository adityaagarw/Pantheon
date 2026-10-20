"""Make the macOS disk image from the built .app, with hdiutil only.

    python scripts/make_dmg.py      # after: npx tauri build --bundles app (on macOS)

Tauri's own bundle_dmg.sh lays the window out through Finder with AppleScript, which
fails or times out on headless build machines. This makes the same kind of image (the app
and a link to /Applications) without Finder: bundle/dmg/<Product>_<version>_<arch>.dmg.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAURI = ROOT / "apps" / "desktop" / "src-tauri"
BUNDLE = TAURI / "target" / "release" / "bundle"


def main() -> None:
    conf = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    product, version = conf["productName"], conf["version"]
    arch = {"x86_64": "x64", "arm64": "aarch64"}.get(platform.machine(), platform.machine())
    app = BUNDLE / "macos" / f"{product}.app"
    if not app.is_dir():
        sys.exit(f"no {app}: build the app first (npx tauri build --bundles app)")
    out = BUNDLE / "dmg" / f"{product}_{version}_{arch}.dmg"
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / product
        stage.mkdir()
        subprocess.run(["ditto", str(app), str(stage / app.name)], check=True)
        (stage / "Applications").symlink_to("/Applications")
        for attempt in range(1, 4):  # hdiutil sometimes reports "Resource busy"
            proc = subprocess.run(["hdiutil", "create", "-volname", product, "-srcfolder",
                                   str(stage), "-ov", "-format", "UDZO", str(out)],
                                  capture_output=True, text=True)
            if proc.returncode == 0:
                break
            print(f"hdiutil failed (attempt {attempt}): {proc.stderr.strip()}", flush=True)
            time.sleep(10)
        else:
            sys.exit("couldn't make the disk image")
    print(f"made {out} ({out.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
