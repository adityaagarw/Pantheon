"""Things Pantheon Host does on this computer besides Kronos and recording: start and stop
Pantheon's containers, trust Pantheon's HTTPS certificate, and ask you for a folder.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from host import paths

SYSTEM = platform.system()
DOCKER_DESKTOP = {
    "Windows": Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe"),
    "Darwin": Path("/Applications/Docker.app"),
}


# --- Pantheon's containers ------------------------------------------------------------------


def pantheon_up() -> bool:
    try:
        with urllib.request.urlopen(paths.backend() + "/api/v1/health", timeout=3,
                                    context=paths.tls_context()) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def web_url() -> str:
    """Pantheon's web app, next to the backend this helper talks to."""
    u = urlparse(paths.backend())
    if u.port == 8710 or u.port is None:
        return f"http://{u.hostname or 'localhost'}:3710"
    return f"{u.scheme}://{u.netloc}"  # behind the HTTPS proxy, same address


def _docker(*args: str, timeout: float = 600, cwd: Path | None = None) -> tuple[int, str]:
    if not shutil.which("docker"):
        return 127, "Docker isn't installed (get Docker Desktop from docker.com)"
    r = subprocess.run(["docker", *args], cwd=str(cwd or paths.REPO), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
    return r.returncode, (r.stdout + r.stderr).strip()


def docker_running() -> bool:
    code, _ = _docker("info", "--format", "{{.ServerVersion}}", timeout=20)
    return code == 0


def start_docker(wait: float = 180) -> bool:
    """Start Docker Desktop if it isn't running, and wait for it."""
    if docker_running():
        return True
    app = DOCKER_DESKTOP.get(SYSTEM)
    if app and app.exists():
        if SYSTEM == "Windows":
            subprocess.Popen([str(app)], creationflags=0x08000000)  # no console window
        else:
            subprocess.Popen(["open", "-a", str(app)])
    elif SYSTEM == "Linux" and shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "start", "docker-desktop"], capture_output=True,
                       check=False)
    deadline = time.time() + wait
    while time.time() < deadline:
        if docker_running():
            return True
        time.sleep(3)
    return False


def _profiles() -> list[str]:
    """Include the sandbox desktop if this computer has it (it's optional and large)."""
    code, out = _docker("ps", "-a", "--filter", "label=com.docker.compose.service=desktop",
                        "--format", "{{.Names}}", timeout=30)
    return ["--profile", "computer"] if code == 0 and out.strip() else []


def start_pantheon() -> str:
    if not start_docker():
        return "Docker isn't running (start Docker Desktop, then try again)"
    code, out = _docker("compose", *_profiles(), "up", "-d")
    return "Pantheon is starting" if code == 0 else f"couldn't start Pantheon: {out[-400:]}"


def stop_pantheon() -> str:
    code, out = _docker("compose", *_profiles(), "stop")
    return "Pantheon stopped" if code == 0 else f"couldn't stop Pantheon: {out[-400:]}"


# --- Pantheon's HTTPS certificate ------------------------------------------------------------


def trust_certificate() -> str:
    """Add Pantheon's own certificate authority to this computer's trusted roots, for you only.
    The operating system asks you to confirm."""
    root = paths.CADDY_ROOT
    if not root.exists():
        return "Pantheon's HTTPS proxy hasn't made its certificate yet (open https://<this-pc>:3711 once)"
    if SYSTEM == "Windows":
        cmd = ["certutil", "-user", "-addstore", "Root", str(root)]
    elif SYSTEM == "Darwin":
        cmd = ["security", "add-trusted-cert", "-r", "trustRoot", "-k",
               str(Path.home() / "Library/Keychains/login.keychain-db"), str(root)]
    elif shutil.which("certutil"):  # Chrome and Firefox on Linux use the NSS database
        db = Path.home() / ".pki/nssdb"
        db.mkdir(parents=True, exist_ok=True)
        cmd = ["certutil", "-d", f"sql:{db}", "-A", "-t", "C,,", "-n", "Pantheon local CA", "-i",
               str(root)]
    else:
        return (f"Install it by hand: {root} (on Linux: sudo cp it to /usr/local/share/"
                "ca-certificates/pantheon.crt && sudo update-ca-certificates)")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
    if r.returncode == 0:
        return "Pantheon's certificate is trusted on this computer (restart your browser)"
    return f"not trusted: {(r.stderr or r.stdout).strip()[-300:]}"


# --- asking you for a folder ----------------------------------------------------------------


def ask_folder(prompt: str = "Choose a folder Kronos may use") -> str | None:
    """A native folder picker on this computer's screen. None if you cancel or there's no screen."""
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        return None
    try:
        root = tkinter.Tk()
    except tkinter.TclError:  # no display
        return None
    try:
        root.withdraw()
        root.attributes("-topmost", True)  # in front of the browser that asked
        path = filedialog.askdirectory(title=prompt, mustexist=True, parent=root)
        return path or None
    finally:
        root.destroy()
