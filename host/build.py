"""Build the Pantheon Host app: one file, no Python needed.

    uv run --no-project --with pyinstaller --with pystray --with pillow python host/build.py

Makes host/dist/PantheonHost.exe (Windows), host/dist/PantheonHost.app (macOS) or
host/dist/PantheonHost (Linux). Put it in your Pantheon folder (or anywhere: opened outside
one, it offers to set Pantheon up).
"""

from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path

HOST = Path(__file__).resolve().parent
ROOT = HOST.parent
sys.path.insert(0, str(ROOT))

SYSTEM = platform.system()
TRAY_BACKEND = {"Windows": "pystray._win32", "Darwin": "pystray._darwin"}.get(
    SYSTEM, "pystray._xorg")


def icon_file(build: Path) -> Path | None:
    """The tray icon's artwork as an app icon."""
    from host.tray import _image

    big = _image(True, False).resize((256, 256))
    if SYSTEM == "Windows":
        out = build / "pantheon.ico"
        big.save(out, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    elif SYSTEM == "Darwin":
        out = build / "pantheon.icns"
        big.save(out)
    else:
        return None
    return out


def main() -> None:
    import PyInstaller.__main__

    build = HOST / "build"
    build.mkdir(exist_ok=True)
    args = [str(HOST / "main.py"), "--name", "PantheonHost", "--onefile", "--noconfirm",
            "--paths", str(ROOT), "--distpath", str(HOST / "dist"), "--workpath", str(build),
            "--specpath", str(build), "--hidden-import", TRAY_BACKEND,
            "--hidden-import", "PIL._tkinter_finder"]
    if SYSTEM in ("Windows", "Darwin"):
        args.append("--windowed")  # no console window; it lives in the tray
    if (icon := icon_file(build)) is not None:
        args += ["--icon", str(icon)]
    if SYSTEM == "Darwin":
        args += ["--osx-bundle-identifier", "ai.pantheon.host"]
    PyInstaller.__main__.run(args)
    shutil.rmtree(build, ignore_errors=True)
    print("Built:", *(str(p) for p in (HOST / "dist").iterdir()))


if __name__ == "__main__":
    main()
