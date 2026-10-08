"""Pantheon Host's tray icon (optional: needs pystray and Pillow, bundled in the packaged app).

Shows whether Pantheon is connected and whether this computer is being recorded, and
offers: Open Pantheon, Kronos's folders, start/stop Pantheon, trust its certificate,
start at login, and quit.
"""

from __future__ import annotations

import contextlib
import threading
import webbrowser
from typing import TYPE_CHECKING

from host import autostart, paths, system

if TYPE_CHECKING:
    from host.main import Host, Link


def available() -> bool:
    try:
        import PIL  # noqa: F401
        import pystray  # noqa: F401
    except ImportError:
        return False
    return True


def _image(connected: bool, recording: bool):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), 14, fill=(109, 93, 252) if connected else (110, 114, 128))
    d.polygon([(12, 24), (32, 11), (52, 24)], fill="white")  # a temple: roof, columns, base
    for x in (16, 28, 40):
        d.rectangle((x, 28, x + 7, 47), fill="white")
    d.rectangle((12, 50, 52, 54), fill="white")
    if recording:
        d.ellipse((40, 2, 62, 24), fill=(232, 58, 58), outline="white", width=3)
    return img


def run(host: Host, link: Link, stop: threading.Event, log) -> None:
    """Runs the tray in this thread (the main thread) until Quit."""
    import pystray
    from pystray import Menu
    from pystray import MenuItem as Item

    def recording() -> bool:
        return bool(host.recorder and host.recorder.recording)

    def status(_item) -> str:
        if not link.connected:
            return "Pantheon isn't running"
        return f"Recording “{host.recording_title}”" if recording() else "Connected to Pantheon"

    def act(fn, *args):
        """Run something slow off the menu thread and show its result as a notification."""
        def go() -> None:
            try:
                msg = fn(*args)
            except Exception as e:  # noqa: BLE001 - shown to the user, not raised
                msg = f"{type(e).__name__}: {e}"
            if msg:
                log(msg)
                with contextlib.suppress(Exception):  # notifications aren't supported everywhere
                    icon.notify(msg, "Pantheon Host")
        threading.Thread(target=go, daemon=True).start()

    def add_folder(*_):
        act(lambda: host.pick_folder({}))

    def remove(path: str):
        return lambda *_: act(lambda: host.disallow({"path": path}))

    def folders():
        roots = paths.load_config().get("allowed", [])
        if not roots:
            return [Item("Only Pantheon's own code", None, enabled=False)]
        return [Item(f"Remove {p}", remove(p)) for p in roots]

    def toggle_login(*_):
        act(autostart.uninstall if autostart.installed() else autostart.install)

    def quit_(icon_, *_):
        stop.set()
        if host.recorder:
            host.recorder.stop()
        icon_.stop()

    icon = pystray.Icon("pantheon-host", _image(False, False), "Pantheon Host", Menu(
        Item(status, None, enabled=False),
        Item(lambda _i: f"Stop recording “{host.recording_title}”",
             lambda *_: act(lambda: host.record_stop({})), visible=lambda _i: recording()),
        Item("Open Pantheon", lambda *_: webbrowser.open(system.web_url()), default=True),
        Menu.SEPARATOR,
        Item("Let Kronos into a folder…", add_folder),
        Item("Folders Kronos may use", Menu(folders)),
        Menu.SEPARATOR,
        Item("Start Pantheon", lambda *_: act(system.start_pantheon),
             visible=lambda _i: not link.connected),
        Item("Stop Pantheon", lambda *_: act(system.stop_pantheon), visible=lambda _i: link.connected),
        Item("Trust Pantheon's certificate", lambda *_: act(system.trust_certificate)),
        Item("Start at login", toggle_login, checked=lambda _i: autostart.installed()),
        Menu.SEPARATOR,
        Item("Quit", quit_),
    ))

    def refresh() -> None:
        last = None
        while not stop.wait(2):
            state = (link.connected, recording())
            if state != last:
                icon.icon = _image(*state)
                icon.title = "Pantheon Host: " + status(None)
                last = state
            icon.update_menu()

    threading.Thread(target=refresh, daemon=True).start()
    icon.run()
