"""Run the engine: ``python -m app.serve`` (the desktop app starts it this way).

It listens on 127.0.0.1 only. The desktop shell passes its data folder and port with
PANTHEON_DATA_DIR and PANTHEON_PORT, and watches for ``PANTHEON_READY <port>`` on stdout.

``--public [--port N]`` runs it as a server instead (``pantheon-serve`` in the installed
runtime): on every interface, with the interface for browsers; other devices need the
access code it prints (services/server.py, app/core/access.py).
The engine stops when the shell does: on Windows the shell's job object ends it; on
macOS and Linux it exits when its parent process is gone (PANTHEON_EXIT_WITH_PARENT).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import threading
import time

import uvicorn

from app.core.config import settings


def _exit_with_parent() -> None:
    """Exit when the process that started us is gone, even if it crashed. An orphan is
    handed to another parent, so a changed parent pid means ours has exited. (Windows
    doesn't reparent; there the shell's job object ends us instead.)"""
    if os.environ.get("PANTHEON_EXIT_WITH_PARENT") != "1" or sys.platform == "win32":
        return
    parent = os.getppid()

    def watch() -> None:
        while os.getppid() == parent:
            time.sleep(2)
        os._exit(0)

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


class _Server(uvicorn.Server):
    public = False

    async def startup(self, sockets=None) -> None:
        await super().startup(sockets)
        if self.started:
            print(f"PANTHEON_READY {settings.port}", flush=True)
            if self.public:
                from app.services import server

                urls = [f"http://{h}:{settings.port}/" for h in ("localhost", *server.addresses())]
                print(f"Pantheon is open at {', '.join(urls)}", flush=True)
                print(f"Access code for other devices: {server.status()['accessCode']}", flush=True)


def main() -> None:
    if sys.platform == "win32":
        # MCP servers run as subprocesses, which needs the Proactor loop on Windows.
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    parser = argparse.ArgumentParser(prog="pantheon-serve", description="Run Pantheon's engine.")
    parser.add_argument("--public", action="store_true",
                        help="listen on every interface and serve the interface to browsers")
    parser.add_argument("--port", type=int, help="the port (default 8730 with --public, else 8720)")
    args = parser.parse_args()
    if args.port:
        settings.port = args.port
    elif args.public and "PANTHEON_PORT" not in os.environ:
        settings.port = 8730  # the app itself uses 8720
    for name, url in (("stage_base_url", "http://127.0.0.1:{}"), ("public_url", "http://localhost:{}")):
        if getattr(settings, name) in (url.format(8720), url.format(settings.model_fields["port"].default)):
            setattr(settings, name, url.format(settings.port))  # derived from the port
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings.data_path.mkdir(parents=True, exist_ok=True)  # a first run without the app
    _exit_with_parent()
    config = uvicorn.Config("app.main:app", host="0.0.0.0" if args.public else "127.0.0.1",
                            port=settings.port, loop="none", log_level="info",
                            ws_max_size=64 * 1024 * 1024)
    server = _Server(config)
    server.public = args.public
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
