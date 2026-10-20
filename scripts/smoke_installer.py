"""Check a built installer works: install it the way a user would, start the installed app
and wait for its engine to answer, then run the installed ``pantheon-serve`` and check a
browser would get the interface.

    python scripts/smoke_installer.py            # after npm --prefix apps/desktop run build

Windows: the NSIS setup, installed silently for this user. macOS: the .app from the .dmg.
Linux: the .deb (installed with apt) and the AppImage. On Linux without a display, run it
under xvfb-run. Exits non-zero, with the engine's log, if anything fails.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "apps" / "desktop" / "src-tauri" / "target" / "release" / "bundle"
APP_PORT, SERVE_PORT = 8791, 8792
TMP = Path(tempfile.mkdtemp(prefix="pantheon-smoke-"))


def log(msg: str) -> None:
    print(f"[smoke] {msg}", flush=True)


def one(pattern: str) -> Path:
    found = sorted(BUNDLE.glob(pattern))
    if not found:
        sys.exit(f"no {pattern} under {BUNDLE}: build first")
    return found[-1]


def get(url: str, timeout: float = 5) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except OSError as e:
        return 0, str(e)


def wait_health(port: int, proc: subprocess.Popen, seconds: float = 240) -> dict:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"it exited with code {proc.returncode}")
        status, body = get(f"http://127.0.0.1:{port}/api/v1/health")
        if status == 200:
            return json.loads(body)
        time.sleep(2)
    raise RuntimeError(f"no answer on port {port} within {seconds:.0f} s")


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.terminate()
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                proc.kill()


def install() -> tuple[Path, Path]:
    """Install; return (the app's executable, its runtime folder)."""
    if sys.platform == "win32":
        setup = one("nsis/*-setup.exe")
        dest = TMP / "PantheonStudio"  # NSIS takes /D= unquoted: no spaces
        log(f"installing {setup.name} to {dest}")
        subprocess.run([str(setup), "/S", f"/D={dest}"], check=True, timeout=900)
        exe = next(p for p in dest.glob("*.exe") if "uninstall" not in p.name.lower())
        shim = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WindowsApps" / "pantheon-serve.cmd"
        if not shim.is_file() or str(dest) not in shim.read_text(errors="replace"):
            sys.exit(f"pantheon-serve isn't on PATH ({shim})")
        log("pantheon-serve is on PATH")
        return exe, dest / "runtime"
    if sys.platform == "darwin":
        dmg = one("dmg/*.dmg")
        mount = TMP / "dmg"
        log(f"mounting {dmg.name}")
        subprocess.run(["hdiutil", "attach", str(dmg), "-nobrowse", "-mountpoint", str(mount)],
                       check=True)
        app = next(mount.glob("*.app"))
        dest = TMP / app.name
        subprocess.run(["ditto", str(app), str(dest)], check=True)
        subprocess.run(["hdiutil", "detach", str(mount)], check=False)
        exe = next((dest / "Contents" / "MacOS").iterdir())
        return exe, dest / "Contents" / "Resources" / "runtime"
    deb = one("deb/*.deb")
    log(f"installing {deb.name}")
    subprocess.run(["sudo", "apt-get", "install", "-y", str(deb)], check=True)
    files = subprocess.run(["dpkg", "-L", subprocess.run(
        ["dpkg-deb", "-f", str(deb), "Package"], capture_output=True, text=True,
        check=True).stdout.strip()], capture_output=True, text=True, check=True).stdout.split("\n")
    exe = Path(next(f for f in files if f.startswith("/usr/bin/")))
    runtime = Path(next(f for f in files if f.endswith("/runtime/pantheon-serve"))).parent
    if not os.path.exists("/usr/bin/pantheon-serve"):
        sys.exit("pantheon-serve isn't on PATH (/usr/bin/pantheon-serve)")
    log("pantheon-serve is on PATH")
    return exe, runtime


def run_app(exe: Path, runtime: Path) -> None:
    data = TMP / "app-data"
    env = {**os.environ, "PANTHEON_PORT": str(APP_PORT), "PANTHEON_DATA_DIR": str(data)}
    log(f"starting the installed app: {exe}")
    proc = subprocess.Popen([str(exe)], env=env)
    try:
        health = wait_health(APP_PORT, proc)
        log(f"the installed app's engine answers: {health}")
    except RuntimeError as e:
        engine_log = next(iter(sorted(TMP.rglob("engine.log"))), None)
        if engine_log is None:  # the app keeps its log in its own data folder
            for base in (Path(os.environ.get("APPDATA", "")), Path.home() / ".local/share",
                         Path.home() / "Library/Application Support"):
                cand = base / "ai.pantheon.studio" / "logs" / "engine.log"
                if cand.is_file():
                    engine_log = cand
        tail = engine_log.read_text(errors="replace")[-4000:] if engine_log else "(no engine log)"
        sys.exit(f"the installed app failed: {e}\n--- engine.log ---\n{tail}")
    finally:
        stop(proc)


def run_serve(runtime: Path) -> None:
    launcher = runtime / ("pantheon-serve.cmd" if os.name == "nt" else "pantheon-serve")
    env = {**os.environ, "PANTHEON_DATA_DIR": str(TMP / "serve-data")}
    log(f"starting {launcher} --public --port {SERVE_PORT}")
    proc = subprocess.Popen([str(launcher), "--port", str(SERVE_PORT)], env=env,
                            shell=os.name == "nt")
    try:
        wait_health(SERVE_PORT, proc)
        status, page = get(f"http://127.0.0.1:{SERVE_PORT}/org/anything")
        if status != 200 or "<div id=\"root\"" not in page:
            sys.exit(f"pantheon-serve didn't serve the interface ({status}): {page[:300]}")
        log("pantheon-serve serves the interface to browsers")
    finally:
        stop(proc)


def appimage() -> None:
    image = one("appimage/*.AppImage")
    image.chmod(0o755)
    env = {**os.environ, "PANTHEON_PORT": str(APP_PORT + 10),
           "PANTHEON_DATA_DIR": str(TMP / "appimage-data"), "APPIMAGE_EXTRACT_AND_RUN": "1"}
    log(f"starting {image.name}")
    proc = subprocess.Popen([str(image)], env=env)
    try:
        log(f"the AppImage's engine answers: {wait_health(APP_PORT + 10, proc)}")
    except RuntimeError as e:
        log_file = TMP / "appimage-data" / "logs" / "engine.log"
        tail = log_file.read_text(errors="replace")[-4000:] if log_file.is_file() else "(no engine log)"
        sys.exit(f"the AppImage failed: {e}\n--- engine.log ---\n{tail}")
    finally:
        stop(proc)


def already_running() -> bool:
    """Pantheon Studio allows one copy at a time: a second one hands over to it and quits,
    so the check can't start the installed app while Pantheon is open."""
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq pantheon-studio.exe"],
                             capture_output=True, text=True).stdout
        return "pantheon-studio.exe" in out.lower()
    return subprocess.run(["pgrep", "-x", "pantheon-studio"], capture_output=True).returncode == 0


def main() -> None:
    if already_running():
        sys.exit("Close Pantheon Studio first: it allows one copy at a time, so the installed "
                 "copy would hand over to the open one and quit.")
    exe, runtime = install()
    try:
        run_app(exe, runtime)
        run_serve(runtime)
        if sys.platform.startswith("linux"):
            appimage()
        log("all good")
    finally:
        if sys.platform == "win32":  # leave no Start menu entry or uninstall record behind
            uninstaller = next(exe.parent.glob("uninstall*.exe"), None)
            if uninstaller:
                subprocess.run(["powershell", "-NoProfile", "-Command",
                                f"Start-Process -FilePath '{uninstaller}' -ArgumentList '/S' -Wait"],
                               timeout=300)
                shim = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WindowsApps" / "pantheon-serve.cmd"
                log("uninstalled; pantheon-serve " + ("still on PATH!" if shim.exists() else "removed"))
                # The installer remembers the last folder for the next install: forget this one.
                subprocess.run(["reg", "delete", r"HKCU\Software\pantheon", "/f"], capture_output=True)
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
