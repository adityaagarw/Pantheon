"""This computer, for agents: the screen (mss), the mouse and keyboard (pynput).

It speaks the same commands as the cua computer-server (app/computer/cua.py), so the
computer tools work the same on either. Screenshots are scaled down to at most
MAX_WIDTH pixels wide; the coordinates agents give are in the screenshot and are scaled
back to the screen.

Fail-safe: the user moving the mouse into the top-left corner of the screen takes the
computer back. The next action fails, and computer use pauses (app/computer/__init__.py).
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import subprocess
import sys
import time
from typing import Any

MAX_WIDTH = 1366
CORNER = 3  # pixels from the top-left corner that mean "stop"


class TakenBack(RuntimeError):
    """The user took the computer back (the mouse is in the top-left corner)."""


def _dpi_aware() -> None:
    """Screen pixels for both the screenshot and the mouse on scaled Windows displays."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001 - older Windows: best effort
            pass


_dpi_aware()


def active_window() -> str:
    """The title of the window in front, where the platform tells us."""
    try:
        if sys.platform == "win32":
            import ctypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            return buf.value
        if sys.platform == "darwin":
            out = subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to get name of first '
                 'application process whose frontmost is true'],
                capture_output=True, text=True, timeout=2).stdout
            return out.strip()
        wid = subprocess.run(["xprop", "-root", "_NET_ACTIVE_WINDOW"], capture_output=True,
                             text=True, timeout=2).stdout.split("#")[-1].split(",")[0].strip()
        out = subprocess.run(["xprop", "-id", wid, "_NET_WM_NAME"], capture_output=True,
                             text=True, timeout=2).stdout
        return out.split("=", 1)[1].strip().strip('"') if "=" in out else ""
    except Exception:  # noqa: BLE001
        return ""


def _key(name: str):
    from pynput.keyboard import Key, KeyCode

    n = name.strip()
    low = n.lower().replace(" ", "_")
    aliases = {"return": "enter", "escape": "esc", "control": "ctrl", "win": "cmd",
               "super": "cmd", "meta": "cmd", "command": "cmd", "option": "alt",
               "pageup": "page_up", "pagedown": "page_down", "pgup": "page_up",
               "pgdn": "page_down", "del": "delete", "ins": "insert", "arrowup": "up",
               "arrowdown": "down", "arrowleft": "left", "arrowright": "right"}
    low = aliases.get(low, low)
    if hasattr(Key, low):
        return getattr(Key, low)
    if len(n) == 1:
        return KeyCode.from_char(n.lower() if n.isupper() else n)
    raise ValueError(f"unknown key '{name}'")


class NativeComputer:
    label = "this computer"

    def __init__(self) -> None:
        self._scale = 1.0
        self._origin = (0, 0)
        self._size = (0, 0)

    # --- the screen ---------------------------------------------------------------------

    def _shot(self, quality: int) -> tuple[str, int, int]:
        import mss
        from PIL import Image

        with mss.mss() as sct:
            mon = sct.monitors[1]  # the primary screen
            raw = sct.grab(mon)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        self._origin = (mon["left"], mon["top"])
        self._size = (raw.size[0], raw.size[1])
        if img.width > MAX_WIDTH:
            h = round(img.height * MAX_WIDTH / img.width)
            img = img.resize((MAX_WIDTH, h), Image.LANCZOS)
        self._scale = raw.size[0] / img.width
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return base64.b64encode(buf.getvalue()).decode(), img.width, img.height

    def _xy(self, x: Any, y: Any) -> tuple[int, int]:
        if not self._size[0]:
            self._shot(40)  # learn the scale
        return (self._origin[0] + round(float(x) * self._scale),
                self._origin[1] + round(float(y) * self._scale))

    # --- input --------------------------------------------------------------------------

    def _guard(self) -> None:
        from pynput.mouse import Controller

        x, y = Controller().position
        if x - self._origin[0] <= CORNER and y - self._origin[1] <= CORNER:
            raise TakenBack("the user took the computer back (the mouse is in the top-left "
                            "corner of the screen); computer use is paused until they resume it")

    def _do(self, action: str, p: dict[str, Any]) -> dict[str, Any]:
        from pynput.keyboard import Controller as Keys
        from pynput.mouse import Button
        from pynput.mouse import Controller as Mouse

        if action in ("screenshot",):
            data, w, h = self._shot(int(p.get("quality") or 70))
            return {"success": True, "image_data": data, "format": "jpeg", "width": w, "height": h}
        if action == "get_screen_size":
            if not self._size[0]:
                self._shot(40)
            return {"success": True, "size": {"width": round(self._size[0] / self._scale),
                                              "height": round(self._size[1] / self._scale)}}
        if action == "run_command":
            r = subprocess.run(p["command"], shell=True, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=float(p.get("timeout") or 170))
            return {"success": True, "stdout": r.stdout[-20000:], "stderr": r.stderr[-6000:],
                    "return_code": r.returncode}
        if action == "open":
            target = str(p["target"])
            if sys.platform == "win32":
                os.startfile(target)  # noqa: S606 - the user's default app for it
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", target])
            return {"success": True}
        self._guard()
        mouse, keys = Mouse(), Keys()
        if action in ("left_click", "right_click", "double_click", "move_cursor"):
            mouse.position = self._xy(p["x"], p["y"])
            time.sleep(0.05)
            if action != "move_cursor":
                button = Button.right if action == "right_click" else Button.left
                mouse.click(button, 2 if action == "double_click" else 1)
        elif action == "drag_to":
            mouse.press(Button.left)
            time.sleep(0.05)
            tx, ty = self._xy(p["x"], p["y"])
            sx, sy = mouse.position
            for i in range(1, 11):  # a few steps, so apps see a drag
                mouse.position = (sx + (tx - sx) * i // 10, sy + (ty - sy) * i // 10)
                time.sleep(0.02)
            mouse.release(Button.left)
        elif action in ("scroll_up", "scroll_down"):
            mouse.scroll(0, (1 if action == "scroll_up" else -1) * int(p.get("clicks") or 3))
        elif action == "type_text":
            keys.type(str(p["text"]))
        elif action == "press_key":
            k = _key(str(p["key"]))
            keys.press(k)
            keys.release(k)
        elif action == "hotkey":
            combo = [_key(str(k)) for k in p["keys"]]
            for k in combo:
                keys.press(k)
            for k in reversed(combo):
                keys.release(k)
        else:
            return {"success": False, "error": f"unknown action {action}"}
        return {"success": True}

    async def cmd(self, action: str, /, timeout: float = 60, **params: Any) -> dict[str, Any]:
        from app.computer.cua import ComputerError

        try:
            result = await asyncio.wait_for(asyncio.to_thread(self._do, action, params), timeout)
        except TakenBack:
            raise
        except (ValueError, KeyError, OSError, subprocess.SubprocessError) as e:
            raise ComputerError(f"{action} failed: {e}") from None
        if not result.get("success"):
            raise ComputerError(f"{action} failed: {result.get('error')}")
        return result

    async def screenshot(self, quality: int = 70) -> str:
        r = await self.cmd("screenshot", quality=quality)
        return f"data:image/jpeg;base64,{r['image_data']}"

    async def screen_size(self) -> tuple[int, int]:
        r = await self.cmd("get_screen_size")
        return int(r["size"]["width"]), int(r["size"]["height"])

    async def status(self) -> dict[str, Any]:
        try:
            w, h = await self.screen_size()
            return {"ok": True, "width": w, "height": h}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
