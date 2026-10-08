"""Pantheon Host: the helper that connects Pantheon to this computer (see host/main.py)."""

import subprocess
import sys

if sys.platform == "win32":
    # The helper runs without a console (the tray app, or pythonw at login), and Windows gives
    # every console program it starts (git, docker, powershell...) a window of its own, which
    # flashes up every time. Start them all without one; their output is captured anyway.
    CREATE_NO_WINDOW = 0x08000000

    class _NoWindowPopen(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = _NoWindowPopen  # subprocess.run() and friends use it too
