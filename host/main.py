#!/usr/bin/env python3
"""Pantheon Host: the helper that connects Pantheon to this computer.

It runs in the background (normally started when you log in) and does the things the
containers can't: Kronos's work with files and Pantheon's code, recording this computer
for Shadow, starting Pantheon, and trusting its certificate. One connection, no codes.

    python host/main.py                    # run (keeps reconnecting while Pantheon restarts)
    python host/main.py setup              # first time: Docker, Pantheon, settings, start
    python host/main.py install            # start it when you log in (uninstall undoes it)
    python host/main.py allow "D:/Reports" # let Kronos into a folder; disallow removes it
    python host/main.py list               # what Kronos can reach, and shared workspaces
    python host/main.py mount "D:/code/app"  # a workspace folder for organizations (unmount)
    python host/main.py start | stop       # start or stop Pantheon's containers
    python host/main.py trust              # trust Pantheon's HTTPS certificate on this computer
    python host/main.py record ABC123      # record this computer for one recording, by code

Needs only Python 3.9+ (and git for Kronos's code changes). With pystray and Pillow
installed (pip install pystray pillow) it shows a tray icon; the packaged app has them.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host import autostart, kronos_ops, mounts, paths, system, tray
from host.recorder import Recorder, connect

VERSION = 2
LOG = Path.home() / ".pantheon" / "host.log"
_log_lock = threading.Lock()


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    if sys.stdout:  # absent when started at login without a console
        print(line, flush=True)
    with _log_lock:
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            if LOG.exists() and LOG.stat().st_size > 2_000_000:
                LOG.replace(LOG.with_suffix(".log.1"))
            with LOG.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


class Refused(kronos_ops.Refused):
    pass


# --- what this computer can do for Pantheon -------------------------------------------------


class Host:
    def __init__(self) -> None:
        self.recorder: Recorder | None = None
        self.recording_title = ""

    def capabilities(self) -> list[str]:
        return ["kronos", "recorder", "folders", "workspaces"]

    def info(self) -> dict:
        rec = self.recorder if self.recorder and self.recorder.recording else None
        return {**json.loads(kronos_ops.op_info({})), "host": platform.node(), "version": VERSION,
                "capabilities": self.capabilities(),
                "recording": {"sessionId": rec.session_id, "title": self.recording_title}
                if rec else None}

    def record_start(self, args: dict) -> str:
        if self.recorder and self.recorder.recording:
            if self.recorder.session_id == args["sessionId"]:
                return "already recording"
            self.recorder.stop()
        self.recorder = Recorder(paths.backend(), args["sessionId"], args["code"],
                                 every=float(args.get("every") or 15),
                                 exclude=list(args.get("exclude") or []))
        self.recording_title = args.get("title", "")
        self.recorder.start()
        log(f"recording this computer for \"{self.recording_title}\"")
        return f"recording on {platform.node()}"

    def record_stop(self, args: dict) -> str:
        if self.recorder and (not args.get("sessionId") or self.recorder.session_id == args["sessionId"]):
            self.recorder.stop()
            log("recording stopped")
            return "stopped"
        return "not recording"

    def pick_folder(self, args: dict) -> str:
        """Ask the person at this computer for a folder (a native picker): Pantheon can ask,
        but only they choose what Kronos may use."""
        path = system.ask_folder()
        if not path:
            raise kronos_ops.Refused("no folder chosen")
        return self.allow({"path": path})

    def allow(self, args: dict) -> str:
        p = str(Path(args["path"]).expanduser().resolve())
        if not Path(p).is_dir():
            raise kronos_ops.Refused(f"{p} is not a folder")
        cfg = paths.load_config()
        cfg["allowed"] = sorted(set(cfg.get("allowed", [])) | {p})
        paths.save_config(cfg)
        return f"Kronos may now use {p}"

    def disallow(self, args: dict) -> str:
        """Taking access away is always fine, so Pantheon may ask for it directly."""
        p = str(Path(args["path"]).expanduser().resolve())
        cfg = paths.load_config()
        cfg["allowed"] = [x for x in cfg.get("allowed", []) if x != p and x != args["path"]]
        paths.save_config(cfg)
        return f"Kronos may no longer use {p}"

    def pick_workspace(self, args: dict) -> dict:
        """A folder on this computer for an organization's workspace: the person here picks
        it, then the backend restarts with it mounted (see host/mounts.py)."""
        org = str(args.get("org") or "an organization")[:80]
        path = system.ask_folder(f"Choose the workspace folder for {org}")
        if not path:
            raise kronos_ops.Refused("no folder chosen")
        got = mounts.mount(path)
        log(f"workspace for {org}: {got['hostPath']} -> {got['path']}"
            + (" (restarting the backend)" if got["restart"] else ""))
        return got

    def ops(self) -> dict:
        return {**kronos_ops.OPS, "record_start": self.record_start,
                "record_stop": self.record_stop, "pick_folder": self.pick_folder,
                "disallow": self.disallow, "pick_workspace": self.pick_workspace,
                "unmount_workspace": lambda args: mounts.unmount(str(args.get("path") or "")),
                "ping": lambda args: "pong"}


# --- the link to Pantheon -------------------------------------------------------------------


class Link:
    def __init__(self, host: Host) -> None:
        self.host = host
        self.base = paths.backend() + "/api/v1/host"
        self.tls = paths.tls_context()
        self.connected = False

    def _req(self, method: str, path: str, body: dict | None = None, timeout: float = 30):
        token = paths.TOKEN.read_text(encoding="utf-8").strip() if paths.TOKEN.exists() else ""
        req = urllib.request.Request(
            self.base + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", "X-Host-Token": token})
        with urllib.request.urlopen(req, timeout=timeout, context=self.tls) as r:
            raw = r.read()
            return json.loads(raw) if r.status == 200 and raw else None

    def hello(self) -> None:
        self._req("POST", "/hello", self.host.info())

    def result(self, job_id: str, ok: bool, result=None, error: str | None = None) -> None:
        for _ in range(5):
            try:
                self._req("POST", "/result", {"id": job_id, "ok": ok, "result": result,
                                              "error": error})
                return
            except (urllib.error.URLError, OSError):
                time.sleep(2)

    def handle(self, job: dict) -> None:
        op = job.get("op", "")
        log(f"{op} {json.dumps(job.get('args', {}))[:120]}")
        try:
            fn = self.host.ops().get(op)
            if fn is None:
                raise Refused(f"this computer's Pantheon Host doesn't know '{op}' (update it?)")
            self.result(job["id"], True, fn(job.get("args") or {}))
            if op.startswith(("change_", "record_", "pick_", "disallow", "unmount_")) and op not in (
                    "change_list", "change_diff", "change_check"):
                self.hello()  # Pantheon shows the new state right away
        except kronos_ops.Refused as e:
            self.result(job["id"], False, error=str(e))
        except Exception as e:  # noqa: BLE001 - every failure goes back to the caller
            self.result(job["id"], False, error=f"{type(e).__name__}: {e}")

    def run(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        log(f"Pantheon Host for {paths.REPO}, connecting to {paths.backend()}")
        last_hello = 0.0
        while not stop.is_set():
            try:
                if not self.connected or time.time() - last_hello > 60:
                    self.hello()
                    last_hello = time.time()
                    if not self.connected:
                        log("connected to Pantheon")
                    self.connected = True
                job = self._req("GET", "/next?wait=25", timeout=40)
                if job:
                    threading.Thread(target=self.handle, args=(job,), daemon=True).start()
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    log("Pantheon rejected the token: is Pantheon Host in the same Pantheon "
                        "folder as the running containers?")
                    stop.wait(10)
                else:
                    stop.wait(3)
                self.connected = False
            except (urllib.error.URLError, OSError):
                if self.connected:
                    log("lost Pantheon (restarting?); reconnecting")
                self.connected = False
                stop.wait(3)


# --- command line ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):  # titles and paths may not fit the console's code page
        if stream and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Pantheon Host: connects Pantheon to this computer")
    ap.add_argument("command", nargs="?", default="run",
                    choices=["run", "setup", "install", "uninstall", "allow", "disallow", "list",
                             "mount", "unmount", "start", "stop", "trust", "record"])
    ap.add_argument("arg", nargs="?",
                    help="a folder (allow/disallow/mount/unmount) or a recording code (record)")
    ap.add_argument("--backend", help=f"Pantheon's address (default {paths.DEFAULT_BACKEND})")
    ap.add_argument("--every", type=float, default=15, help="record: seconds between idle screenshots")
    ap.add_argument("--exclude", default="", help="record: window-title words never to screenshot")
    ap.add_argument("--insecure", action="store_true", help="accept any HTTPS certificate")
    ap.add_argument("--at-login", action="store_true", help=argparse.SUPPRESS)  # from autostart
    ap.add_argument("--no-tray", action="store_true", help="run without the tray icon")
    ap.add_argument("--dir", help="setup: where Pantheon goes (default ~/Pantheon)")
    ap.add_argument("--dry-run", action="store_true", help="setup: don't touch Docker")
    ap.add_argument("--source", help=argparse.SUPPRESS)  # setup: a different Pantheon zip
    a = ap.parse_args(argv)
    cfg = paths.load_config()
    if a.backend and a.command != "record":  # a one-off recording doesn't move the helper
        cfg["backend"] = a.backend.rstrip("/")
        paths.save_config(cfg)

    if a.command in ("allow", "disallow"):
        if not a.arg:
            sys.exit(f"usage: python host/main.py {a.command} <folder>")
        p = str(Path(a.arg).expanduser().resolve())
        if a.command == "allow":
            if not Path(p).is_dir():
                sys.exit(f"{p} is not a folder")
            cfg["allowed"] = sorted(set(cfg.get("allowed", [])) | {p})
        else:
            cfg["allowed"] = [x for x in cfg.get("allowed", []) if x != p]
        paths.save_config(cfg)
        print(f"{'Allowed' if a.command == 'allow' else 'Removed'}: {p}")
        print("Pantheon Host picks this up right away; Pantheon shows it within a minute.")
        return
    if a.command in ("mount", "unmount"):
        if not a.arg:
            sys.exit(f"usage: python host/main.py {a.command} <folder>")
        try:
            if a.command == "unmount":
                print(mounts.unmount(a.arg))
                return
            got = mounts.mount(a.arg, restart=False)
        except kronos_ops.Refused as e:
            sys.exit(str(e))
        print(f"Shared: {got['hostPath']}\nIn Pantheon it is {got['path']}: use that as an "
              "organization's workspace (Settings of the organization).")
        if got["path"].startswith(mounts.CONTAINER_ROOT):
            print("Restart Pantheon's backend to see it: docker compose up -d --no-deps backend")
        return
    if a.command == "list":
        print(f"Pantheon repo (read; code changes in worktrees): {paths.REPO}")
        for r in cfg.get("allowed", []):
            print(f"Allowed: {r}")
        for h, c in mounts.mounts().items():
            print(f"Workspace folder: {h} (in Pantheon: {c})")
        print(f"Settings: {paths.CONFIG}")
        return
    if a.command == "record":
        record_once(a)
        return
    if a.command == "setup":
        from host.setup import SOURCE_ZIP, Setup, SetupError

        try:
            Setup(Path(a.dir).expanduser() if a.dir else None, source=a.source or SOURCE_ZIP,
                  dry_run=a.dry_run).run()
        except SetupError as e:
            sys.exit(str(e))
        return
    simple = {"install": autostart.install, "uninstall": autostart.uninstall,
              "start": system.start_pantheon, "stop": system.stop_pantheon,
              "trust": system.trust_certificate}
    if a.command in simple:
        print(simple[a.command]())
        return
    serve(at_login=a.at_login, use_tray=not a.no_tray)


def serve(*, at_login: bool = False, use_tray: bool = True) -> None:
    if paths.FROZEN and paths.find_repo() is None:
        # The packaged app, opened before Pantheon is set up: the setup window, then a fresh
        # start so everything uses the folder that was chosen.
        from host import setup

        if setup.window():
            import subprocess

            subprocess.Popen([sys.executable, *sys.argv[1:]])
        return
    host, stop = Host(), threading.Event()
    link = Link(host)
    if at_login and paths.load_config().get("start_pantheon", True):
        def bring_up() -> None:
            if not system.pantheon_up():
                log(system.start_pantheon())
        threading.Thread(target=bring_up, daemon=True).start()
    if use_tray and tray.available():
        threading.Thread(target=link.run, args=(stop,), daemon=True).start()
        tray.run(host, link, stop, log)  # the tray owns the main thread until Quit
        log("quit")
        return
    if use_tray:
        log("(no tray icon: pip install pystray pillow to get one)")
    try:
        link.run(stop)
    except KeyboardInterrupt:
        log("stopped")


def record_once(a: argparse.Namespace) -> None:
    """Record this computer for one recording, by its code (no Pantheon Host needed)."""
    backend = (a.backend or paths.backend()).rstrip("/")
    if not a.arg:
        sys.exit("usage: python host/main.py record <code>")
    try:
        session_id, title = connect(backend, a.arg, a.insecure)
    except urllib.error.HTTPError as e:
        sys.exit(f"Pantheon didn't accept the code {a.arg}: "
                 f"{json.loads(e.read() or b'{}').get('detail', e.reason)}")
    except (urllib.error.URLError, OSError) as e:
        import ssl

        reason = getattr(e, "reason", e)
        if isinstance(reason, ssl.SSLCertVerificationError):
            sys.exit(f"Pantheon at {backend} uses a certificate this computer doesn't trust "
                     f"({reason.verify_message}). Run this from the Pantheon folder, use "
                     f"http://localhost:8710 on the Pantheon computer, or add --insecure.")
        sys.exit(f"Can't reach Pantheon at {backend}: {reason}. Is it running?")
    rec = Recorder(backend, session_id, a.arg, every=a.every,
                   exclude=[w.strip() for w in a.exclude.split(",") if w.strip()],
                   insecure=a.insecure)
    print(f"Recording \"{title}\". Work as you normally would; Ctrl+C stops.")
    if platform.system() != "Windows":
        print("(Clicks are recorded on Windows only; here you get windows and screenshots.)")
    rec.start()
    try:
        while rec.recording:
            time.sleep(0.5)
    except KeyboardInterrupt:
        rec.stop()
        try:
            rec._post(f"/shadow/{session_id}/stop", {})
        except (urllib.error.URLError, OSError):
            pass
        print("Stopped. Back in Pantheon, ask the agent to learn from the recording.")
        return
    print("The recording was stopped in Pantheon.")


if __name__ == "__main__":
    main()
