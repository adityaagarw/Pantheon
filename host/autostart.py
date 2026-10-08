"""Start Pantheon Host when you log in (and stop doing so).

- Windows: a shortcut in your Startup folder that runs it without a console window.
- macOS: a LaunchAgent (~/Library/LaunchAgents/ai.pantheon.host.plist).
- Linux: a systemd user service, or an XDG autostart entry where there's no systemd.
"""

from __future__ import annotations

import os
import platform
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from host.paths import FROZEN, REPO

MAIN = REPO / "host" / "main.py"
SYSTEM = platform.system()


def launch(windowless: bool) -> list[str]:
    """How to start Pantheon Host at login: the packaged app itself, or Python and main.py."""
    if FROZEN:
        return [sys.executable, "--at-login"]
    exe = Path(sys.executable)
    if windowless and SYSTEM == "Windows" and exe.with_name("pythonw.exe").exists():
        exe = exe.with_name("pythonw.exe")  # no console window
    return [str(exe), str(MAIN), "--at-login"]


def _windows_shortcut() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup/Pantheon Host.lnk"


def _mac_plist() -> Path:
    return Path.home() / "Library/LaunchAgents/ai.pantheon.host.plist"


def _linux_unit() -> Path:
    return Path.home() / ".config/systemd/user/pantheon-host.service"


def _linux_desktop() -> Path:
    return Path.home() / ".config/autostart/pantheon-host.desktop"


def installed() -> bool:
    if SYSTEM == "Windows":
        return _windows_shortcut().exists()
    if SYSTEM == "Darwin":
        return _mac_plist().exists()
    return _linux_unit().exists() or _linux_desktop().exists()


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, capture_output=True, timeout=60, check=False)


def install() -> str:
    if SYSTEM == "Windows":
        link = _windows_shortcut()
        link.parent.mkdir(parents=True, exist_ok=True)
        # A real .lnk, made by the Windows shell (what "Create shortcut" does).
        script = (
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:PANTHEON_LNK); "
            "$s.TargetPath = $env:PANTHEON_EXE; $s.Arguments = $env:PANTHEON_ARGS; "
            "$s.WorkingDirectory = $env:PANTHEON_REPO; $s.Description = 'Pantheon Host'; $s.Save()")
        cmd = launch(True)
        env = {**os.environ, "PANTHEON_LNK": str(link), "PANTHEON_EXE": cmd[0],
               "PANTHEON_ARGS": subprocess.list2cmdline(cmd[1:]), "PANTHEON_REPO": str(REPO)}
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           env=env, capture_output=True, text=True, timeout=60, check=False)
        if not link.exists():
            raise RuntimeError(f"couldn't create the Startup shortcut: {r.stderr.strip()[:300]}")
        return f"Pantheon Host will start when you log in (shortcut: {link})"
    if SYSTEM == "Darwin":
        plist = _mac_plist()
        plist.parent.mkdir(parents=True, exist_ok=True)
        with plist.open("wb") as f:
            plistlib.dump({"Label": "ai.pantheon.host",
                           "ProgramArguments": launch(False),
                           "WorkingDirectory": str(REPO), "RunAtLoad": True,
                           "KeepAlive": {"SuccessfulExit": False},
                           "StandardOutPath": str(Path.home() / ".pantheon/host.out.log"),
                           "StandardErrorPath": str(Path.home() / ".pantheon/host.out.log")}, f)
        _run(["launchctl", "unload", str(plist)])
        _run(["launchctl", "load", "-w", str(plist)])
        return f"Pantheon Host will start when you log in ({plist})"
    if shutil.which("systemctl"):
        unit = _linux_unit()
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(
            "[Unit]\nDescription=Pantheon Host\nAfter=graphical-session.target network-online.target\n\n"
            f"[Service]\nExecStart={shlex.join(launch(False))}\nWorkingDirectory={REPO}\n"
            "Restart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n",
            encoding="utf-8")
        _run(["systemctl", "--user", "daemon-reload"])
        _run(["systemctl", "--user", "enable", "--now", "pantheon-host.service"])
        return f"Pantheon Host will start when you log in (systemd user service {unit.name})"
    desktop = _linux_desktop()
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(f"[Desktop Entry]\nType=Application\nName=Pantheon Host\n"
                       f"Exec={shlex.join(launch(False))}\nPath={REPO}\n"
                       "X-GNOME-Autostart-enabled=true\n",
                       encoding="utf-8")
    return f"Pantheon Host will start when you log in ({desktop})"


def uninstall() -> str:
    if SYSTEM == "Windows":
        _windows_shortcut().unlink(missing_ok=True)
    elif SYSTEM == "Darwin":
        plist = _mac_plist()
        if plist.exists():
            _run(["launchctl", "unload", "-w", str(plist)])
            plist.unlink()
    else:
        if _linux_unit().exists():
            _run(["systemctl", "--user", "disable", "pantheon-host.service"])
            _linux_unit().unlink()
            _run(["systemctl", "--user", "daemon-reload"])
        _linux_desktop().unlink(missing_ok=True)
    return "Pantheon Host won't start at login any more"
