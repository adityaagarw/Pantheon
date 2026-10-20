"""Recording the user at work on this computer, for Shadow (services/shadow.py).

It logs clicks (where, which button, in which window), window changes, and how much was
typed, with Enter, Tab and the like. It never logs which keys were typed. It takes a
screenshot when something happens, and every SCREEN_EVERY seconds if the screen changed,
except of windows that look private (password managers, sign-in, private browsing).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.computer.native import NativeComputer, active_window

SCREEN_EVERY = 20.0
POLL = 1.0
NOISE = re.compile(r"\(\d+\)|\[\d+\]|\b\d{1,3}%|^\W+")  # spinners, counts, progress
PRIVATE = re.compile(r"password|1password|bitwarden|keepass|lastpass|dashlane|keychain|"
                     r"credential|sign[ -]?in|log[ -]?in|inprivate|incognito|private browsing", re.I)
SPECIAL = {"enter", "tab", "esc", "backspace", "delete", "up", "down", "left", "right",
           "page_up", "page_down", "home", "end"}


class InputLog:
    """Clicks and typing from pynput's listener threads, collected for the recorder."""

    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.lock = threading.Lock()
        self.events: list[dict[str, Any]] = []
        self.typed = 0
        self.special: list[str] = []
        self.last_key = 0.0
        self._listeners: list[Any] = []

    def _t(self) -> float:
        return round(time.monotonic() - self.t0, 2)

    def _flush(self) -> None:
        if self.typed or self.special:
            self.events.append({"t": self._t(), "kind": "typing", "data": {
                "keys": self.typed, "special": self.special[:20], "window": active_window()}})
            self.typed, self.special = 0, []

    def on_click(self, x: int, y: int, button: Any, pressed: bool) -> None:
        if pressed:
            with self.lock:
                self._flush()
                self.events.append({"t": self._t(), "kind": "click", "data": {
                    "x": int(x), "y": int(y), "button": str(button).split(".")[-1],
                    "window": active_window()}})

    def on_press(self, key: Any) -> None:
        from pynput import keyboard

        with self.lock:
            self.last_key = time.monotonic()
            if isinstance(key, keyboard.KeyCode):
                self.typed += 1
            else:
                name = str(key).split(".")[-1]
                if name in SPECIAL:
                    self.special.append(name)
                    if name == "enter":
                        self._flush()

    def start(self) -> None:
        from pynput import keyboard, mouse

        self._listeners = [mouse.Listener(on_click=self.on_click),
                           keyboard.Listener(on_press=self.on_press)]
        for listener in self._listeners:
            listener.start()

    def stop(self) -> None:
        for listener in self._listeners:
            listener.stop()

    def take(self) -> list[dict[str, Any]]:
        with self.lock:
            if self.typed and time.monotonic() - self.last_key > 2:
                self._flush()
            out, self.events = self.events, []
        return out


async def record(add: Callable[[list[dict[str, Any]]], Awaitable[Any]]) -> None:
    """Record until cancelled, handing events to ``add`` every couple of seconds."""
    log = InputLog()
    screen = NativeComputer()
    await asyncio.to_thread(log.start)
    last_window: str | None = None
    last_hash, last_shot = "", 0.0
    await asyncio.to_thread(screen._shot, 40)  # learn the screen's scale
    try:
        while True:
            await asyncio.sleep(POLL)
            items: list[dict[str, Any]] = []
            w = await asyncio.to_thread(active_window)
            if last_window is None or NOISE.sub("", w).strip() != NOISE.sub("", last_window).strip():
                items.append({"t": log._t(), "kind": "window", "data": {"title": w}})
                last_window = w
            for e in log.take():
                if e["kind"] == "click":  # in the screenshots' pixels, as agents see them
                    e["data"]["x"] = round((e["data"]["x"] - screen._origin[0]) / screen._scale)
                    e["data"]["y"] = round((e["data"]["y"] - screen._origin[1]) / screen._scale)
                items.append(e)
            now = time.monotonic()
            if PRIVATE.search(w):
                for i in items:  # nothing about a private window but that it was used
                    i["data"] = ({"title": "(a private window)"} if i["kind"] == "window"
                                 else {**i["data"], "window": "(a private window)"})
            elif items or now - last_shot > SCREEN_EVERY:
                data, _, _ = await asyncio.to_thread(screen._shot, 60)
                digest = hashlib.sha1(data.encode()).hexdigest()
                if digest != last_hash or any(i["kind"] != "window" for i in items):
                    items.append({"t": log._t() + 0.01, "kind": "screen",
                                  "image": f"data:image/jpeg;base64,{data}",
                                  "data": {"window": w}})
                    last_hash, last_shot = digest, now
            if items:
                await add(items)
    finally:
        await asyncio.to_thread(log.stop)
