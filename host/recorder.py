"""Shadow recording on this computer: windows, clicks (Windows) and screenshots, posted to a
Pantheon recording. Started by Pantheon Host when you pick "My computer" on the Shadow tab,
or on its own with a code: python shadow/recorder.py ABC123
"""

from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
import urllib.error
import urllib.request

from host import capture
from host.paths import tls_context


class Recorder:
    """Records until stop() or until Pantheon says the recording is over."""

    def __init__(self, backend: str, session_id: str, code: str, *, every: float = 15,
                 exclude: list[str] | None = None, insecure: bool = False) -> None:
        self.backend, self.session_id, self.code = backend.rstrip("/"), session_id, code.upper()
        self.every = every
        self.private = capture.PRIVATE + [w.lower() for w in exclude or [] if w]
        self.tls = tls_context(insecure)
        self.recording = True
        self.events: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None

    # --- talking to Pantheon -----------------------------------------------------------------

    def _post(self, path: str, body: dict, headers: dict | None = None) -> dict:
        req = urllib.request.Request(f"{self.backend}/api/v1{path}", method="POST",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **(headers or {})})
        with urllib.request.urlopen(req, timeout=60, context=self.tls) as r:
            return json.loads(r.read() or b"{}")

    def _send(self) -> None:
        while self.recording or not self.events.empty():
            try:
                batch = [self.events.get(timeout=1)]
            except queue.Empty:
                continue
            try:
                while len(batch) < 20:
                    batch.append(self.events.get_nowait())
            except queue.Empty:
                pass
            try:
                r = self._post(f"/shadow/{self.session_id}/events", {"events": batch},
                               {"X-Shadow-Code": self.code})
                if not r.get("recording"):
                    self.recording = False
            except urllib.error.HTTPError as e:
                if e.code in (401, 404):
                    self.recording = False
            except (urllib.error.URLError, OSError):
                pass  # Pantheon unreachable for a moment; these events are lost

    # --- recording ---------------------------------------------------------------------------

    def start(self) -> None:
        threading.Thread(target=self._send, daemon=True).start()
        self.thread = threading.Thread(target=self._record, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.recording = False

    def _hidden(self, title: str) -> bool:
        return any(w in title.lower() for w in self.private)

    def _record(self) -> None:
        t0 = time.time()
        last_title: str | None = None
        last_shot = last_check = clicked = 0.0
        last_hash = ""
        was_down = None

        def shot(window: str) -> None:
            nonlocal last_shot, last_hash
            last_shot = time.time()
            if self._hidden(window):
                return
            img = capture.screenshot()
            if not img:
                return
            digest = hashlib.sha1(img.encode()).hexdigest()
            if digest != last_hash:
                last_hash = digest
                self.events.put({"t": round(time.time() - t0, 2), "kind": "screen", "image": img,
                                 "data": {"window": window}})

        while self.recording:
            time.sleep(0.05)
            now = time.time()
            down = capture.mouse_down()
            if down and not was_down:
                x, y = capture.cursor()
                title = capture.window_title()
                self.events.put({"t": round(now - t0, 2), "kind": "click", "data": {
                    "x": x, "y": y, "button": down,
                    "window": "[private window]" if self._hidden(title) else title}})
                clicked = now
            was_down = down
            if now - last_check < 1:  # windows and screenshots once a second
                continue
            last_check = now
            title = capture.window_title()
            if not capture.same_window(last_title, title):
                self.events.put({"t": round(now - t0, 2), "kind": "window", "data": {
                    "title": "[private window]" if self._hidden(title) else title}})
                last_title = title
                shot(title)
            elif clicked and now - clicked > 0.6 and now - last_shot > 2:
                clicked = 0.0
                shot(title)
            elif now - last_shot > self.every:
                shot(title)


def connect(backend: str, code: str, insecure: bool = False) -> tuple[str, str]:
    """A recording's id and title from its code."""
    req = urllib.request.Request(f"{backend.rstrip('/')}/api/v1/shadow/connect", method="POST",
                                 data=json.dumps({"code": code}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30, context=tls_context(insecure)) as r:
        info = json.loads(r.read())
    return info["sessionId"], info["title"]
