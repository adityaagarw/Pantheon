"""First-time setup: get Pantheon running on this computer, for people who'd rather not
use a terminal. The packaged app runs it when Pantheon isn't set up yet.

1. Docker: checks it's installed (or opens the download page) and starts it.
2. Pantheon: uses the folder it's already in, or downloads it from GitHub.
3. Settings: creates .env from .env.example (local defaults; add API keys in the app).
4. Starts Pantheon (the first start builds it, which takes a while).
5. Starts Pantheon Host whenever you log in, and opens Pantheon in your browser.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import time
import urllib.request
import webbrowser
import zipfile
from collections.abc import Callable
from pathlib import Path

from host import autostart, paths, system

SOURCE_ZIP = "https://github.com/adityaagarw/Pantheon/archive/refs/heads/main.zip"
DOCKER_DOWNLOAD = "https://www.docker.com/products/docker-desktop/"
STEPS = ["Docker", "Pantheon", "Settings", "Start Pantheon", "Start at login"]


class SetupError(RuntimeError):
    pass


class Setup:
    def __init__(self, target: Path | None = None, *, say: Callable[[str], None] = print,
                 step: Callable[[str, str], None] = lambda name, state: None,
                 source: str = SOURCE_ZIP, dry_run: bool = False) -> None:
        self.target = target or paths.find_repo() or paths.DEFAULT_HOME
        self.say, self.step, self.source, self.dry_run = say, step, source, dry_run

    def run(self) -> Path:
        self._do("Docker", self.docker)
        folder = self._do("Pantheon", self.pantheon)
        self._do("Settings", lambda: self.settings(folder))
        self._do("Start Pantheon", lambda: self.start(folder))
        self._do("Start at login", self.login)
        if not self.dry_run:
            webbrowser.open(system.web_url())
        self.say("Pantheon is ready.")
        return folder

    def _do(self, name: str, fn):
        self.step(name, "running")
        try:
            out = fn()
        except Exception:
            self.step(name, "failed")
            raise
        self.step(name, "done")
        return out

    def docker(self) -> None:
        if self.dry_run:
            return
        if not shutil.which("docker"):
            webbrowser.open(DOCKER_DOWNLOAD)
            raise SetupError("Pantheon runs in Docker. Install Docker Desktop (the download page "
                             "just opened), start it once, then open Pantheon Host again.")
        self.say("Starting Docker…")
        if not system.start_docker(wait=300):
            raise SetupError("Docker didn't start. Open Docker Desktop, wait until it says it's "
                             "running, then try again.")

    def pantheon(self) -> Path:
        folder = self.target
        if paths.is_pantheon(folder):
            self.say(f"Using Pantheon in {folder}")
        else:
            if folder.exists() and any(folder.iterdir()):
                raise SetupError(f"{folder} isn't empty; choose another folder for Pantheon")
            self.say("Downloading Pantheon…")
            with urllib.request.urlopen(self.source, timeout=300) as r:
                data = r.read()
            self.say(f"Unpacking into {folder}…")
            self._unpack(data, folder)
            if not paths.is_pantheon(folder):
                raise SetupError("the download didn't contain Pantheon")
        cfg = paths.load_config()
        cfg["repo"] = str(folder)
        paths.save_config(cfg)
        return folder

    @staticmethod
    def _unpack(data: bytes, folder: Path) -> None:
        """GitHub's zip has one top folder (Pantheon-main/); its contents go into `folder`."""
        folder.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            top = names[0].split("/")[0] + "/" if names and all(
                n.startswith(names[0].split("/")[0] + "/") for n in names) else ""
            root = folder.resolve()
            for info in z.infolist():
                rel = info.filename[len(top):]
                if not rel:
                    continue
                dest = (folder / rel).resolve()
                if root not in dest.parents and dest != root:
                    raise SetupError("the download had an unsafe path in it")
                if info.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, dest.open("wb") as out:
                    shutil.copyfileobj(src, out)

    def settings(self, folder: Path) -> None:
        env, example = folder / ".env", folder / ".env.example"
        if env.exists():
            self.say("Keeping your existing settings (.env)")
        elif example.exists():
            shutil.copyfile(example, env)
            self.say("Created .env with local defaults")

    def start(self, folder: Path) -> None:
        if self.dry_run:
            return
        self.say("Starting Pantheon. The first time, Docker builds it: this can take 10-20 minutes.")
        proc = subprocess.Popen(["docker", "compose", "up", "-d", "--build"], cwd=str(folder),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace",
                                creationflags=0x08000000 if system.SYSTEM == "Windows" else 0)
        last = 0.0
        for line in proc.stdout or []:
            if time.time() - last > 1.5 and line.strip():  # a steady trickle, not a flood
                self.say(line.strip()[:160])
                last = time.time()
        if proc.wait() != 0:
            raise SetupError("Pantheon didn't start; see the messages above")
        self.say("Waiting for Pantheon to answer…")
        deadline = time.time() + 600
        while time.time() < deadline:
            if system.pantheon_up():
                return
            time.sleep(3)
        raise SetupError("Pantheon started but isn't answering yet; give it a minute, then open it")

    def login(self) -> None:
        if self.dry_run:
            return
        self.say(autostart.install())


def window() -> bool:
    """The setup window (packaged app). True once Pantheon is set up."""
    import queue
    import threading
    import tkinter
    from tkinter import filedialog, ttk

    root = tkinter.Tk()
    root.title("Set up Pantheon")
    root.geometry("560x430")
    ttk.Label(root, text="Pantheon", font=("Segoe UI", 18, "bold")).pack(anchor="w", padx=18, pady=(16, 0))
    ttk.Label(root, text="Your own organization of AI agents. This sets it up on this computer.",
              wraplength=520).pack(anchor="w", padx=18)
    folder = tkinter.StringVar(value=str(paths.find_repo() or paths.DEFAULT_HOME))
    row = ttk.Frame(root)
    row.pack(fill="x", padx=18, pady=(12, 4))
    ttk.Label(row, text="Folder:").pack(side="left")
    ttk.Entry(row, textvariable=folder).pack(side="left", fill="x", expand=True, padx=6)
    ttk.Button(row, text="Change…", command=lambda: folder.set(
        filedialog.askdirectory(mustexist=False) or folder.get())).pack(side="left")
    marks = {}
    steps = ttk.Frame(root)
    steps.pack(fill="x", padx=18, pady=6)
    for name in STEPS:
        var = tkinter.StringVar(value=f"○  {name}")
        marks[name] = var
        ttk.Label(steps, textvariable=var).pack(anchor="w")
    log = tkinter.Text(root, height=8, wrap="word", state="disabled", font=("Consolas", 9))
    log.pack(fill="both", expand=True, padx=18, pady=6)
    buttons = ttk.Frame(root)
    buttons.pack(fill="x", padx=18, pady=(0, 14))
    go = ttk.Button(buttons, text="Set up")
    go.pack(side="right")
    events: queue.Queue = queue.Queue()
    result = {"ok": False}

    def pump() -> None:
        while not events.empty():
            kind, a, b = events.get()
            if kind == "say":
                log.configure(state="normal")
                log.insert("end", a + "\n")
                log.see("end")
                log.configure(state="disabled")
            elif kind == "step":
                marks[a].set({"running": "◐  ", "done": "●  ", "failed": "✕  "}[b] + a)
            elif kind == "end":
                result["ok"] = a
                go.configure(text="Close" if a else "Try again", state="normal",
                             command=root.destroy if a else start)
        root.after(150, pump)

    def start() -> None:
        go.configure(state="disabled")
        setup = Setup(Path(folder.get()).expanduser(), say=lambda m: events.put(("say", m, None)),
                      step=lambda n, s: events.put(("step", n, s)))

        def work() -> None:
            try:
                setup.run()
                events.put(("end", True, None))
            except Exception as e:  # noqa: BLE001 - shown in the window
                events.put(("say", f"Stopped: {e}", None))
                events.put(("end", False, None))
        threading.Thread(target=work, daemon=True).start()

    go.configure(command=start)
    pump()
    root.mainloop()
    return result["ok"]
