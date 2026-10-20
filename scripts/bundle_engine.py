"""Build the engine runtime the installers ship: a standalone Python with the engine's
dependencies installed, the engine, the packs and the built interface.

    python scripts/bundle_engine.py            # -> apps/desktop/src-tauri/runtime/

The result (gitignored) is copied into the app as a resource folder (tauri.bundle.json):

    runtime/python/   a relocatable CPython (python-build-standalone, installed by uv)
    runtime/engine/   app/, alembic/, alembic.ini, bench/
    runtime/packs/    the bundled packs
    runtime/ui/       the built interface, for server mode (browsers)
    runtime/pantheon-serve(.cmd)   run the engine without the window (a server)

Needs uv and a built interface (npm --prefix ui run build). Runs on Windows, macOS and
Linux, building for the platform it runs on.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "apps" / "desktop" / "src-tauri" / "runtime"
WORK = ROOT / "build" / "bundle"
PYTHON = "3.12"
WINDOWS = sys.platform == "win32"

# Parts of the standard library the engine never uses (saves ~40 MB).
STDLIB_DROP = ["test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3", "pydoc_data"]


def run(*cmd: str | Path, **kw) -> str:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, text=True, stdout=subprocess.PIPE,
                          **kw).stdout


def python_exe(root: Path) -> Path:
    return root / "python.exe" if WINDOWS else root / "bin" / "python3"


def standalone_python() -> Path:
    """A fresh uv-managed (python-build-standalone) CPython, copied to runtime/python."""
    install = WORK / "pythons"
    shutil.rmtree(install, ignore_errors=True)
    run("uv", "python", "install", PYTHON, "--install-dir", install, "--no-bin")
    # cpython-3.12.14-<platform> (uv also adds cpython-3.12-<platform>, a link to it)
    found = [d for d in install.iterdir() if d.is_dir() and not d.is_symlink()
             and d.name.startswith("cpython-") and d.name.split("-")[1].count(".") == 2]
    if len(found) != 1:
        sys.exit(f"expected one Python in {install}, found {found}")
    dest = OUT / "python"
    shutil.copytree(found[0], dest, symlinks=True)
    lib = dest / "Lib" if WINDOWS else next((dest / "lib").glob("python3.*"))
    for name in STDLIB_DROP:
        shutil.rmtree(lib / name, ignore_errors=True)
    (lib / "EXTERNALLY-MANAGED").unlink(missing_ok=True)  # it's ours to install into
    return python_exe(dest)


def install_dependencies(py: Path) -> None:
    req = WORK / "requirements.txt"
    run("uv", "export", "--project", ROOT / "engine", "--frozen", "--no-dev", "--no-hashes",
        "--no-emit-project", "--no-header", "-o", req)
    run("uv", "pip", "install", "--python", py, "--no-cache", "--link-mode", "copy", "-r", req)
    shutil.rmtree(OUT / "python" / "include", ignore_errors=True)  # only needed to build wheels


def copy_sources() -> None:
    engine = OUT / "engine"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "results", "runs")
    for name in ("app", "alembic", "bench"):
        if (ROOT / "engine" / name).exists():
            shutil.copytree(ROOT / "engine" / name, engine / name, ignore=ignore)
    shutil.copy2(ROOT / "engine" / "alembic.ini", engine / "alembic.ini")
    shutil.copytree(ROOT / "packs", OUT / "packs", ignore=ignore)
    dist = ROOT / "ui" / "dist"
    if not (dist / "index.html").exists():
        sys.exit("build the interface first: npm --prefix ui run build")
    shutil.copytree(dist, OUT / "ui")


def write_launchers() -> None:
    """``pantheon-serve``: the engine and interface for browsers, without the window."""
    if WINDOWS:
        (OUT / "pantheon-serve.cmd").write_text(
            "@echo off\r\nset PYTHONPATH=%~dp0engine\r\nset PYTHONNOUSERSITE=1\r\n"
            '"%~dp0python\\python.exe" -m app.serve --public %*\r\n', encoding="utf-8")
    else:
        sh = OUT / "pantheon-serve"
        sh.write_text('#!/bin/sh\nhere="$(cd "$(dirname "$0")" && pwd)"\n'
                      'PYTHONPATH="$here/engine" PYTHONNOUSERSITE=1 '
                      'exec "$here/python/bin/python3" -m app.serve --public "$@"\n',
                      encoding="utf-8")
        sh.chmod(0o755)


def smoke_test(py: Path) -> None:
    """The bundled Python imports the whole engine (catches a missing dependency here,
    not on a user's computer)."""
    env = {**os.environ, "PYTHONPATH": str(OUT / "engine"), "PYTHONNOUSERSITE": "1",
           "PYTHONIOENCODING": "utf-8"}
    # pynput needs a display on Linux; the engine only imports it when it's used.
    extra = "" if sys.platform.startswith("linux") else "pynput, "
    out = run(py, "-c", "import app.main, app.mcpbridge, app.computer.native, mss, "
                        f"{extra}playwright, fastembed, cua_driver; print('engine imports ok')",
              env=env, cwd=OUT)
    print(out.strip())


def main() -> None:
    shutil.rmtree(OUT, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True)
    py = standalone_python()
    install_dependencies(py)
    copy_sources()
    write_launchers()
    # Faster first start (the app's folder may be read-only), with the bundled Python's tags.
    # Best effort: a few packaged files are templates, not Python, and don't compile.
    subprocess.run([str(py), "-m", "compileall", "-q", "-j", "0", str(OUT / "engine"),
                    str(OUT / "packs"), str(OUT / "python")], stdout=subprocess.DEVNULL)
    smoke_test(py)
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"runtime ready: {OUT} ({size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
