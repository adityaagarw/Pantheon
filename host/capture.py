"""What's on this computer's screen: the active window, mouse clicks (Windows) and
screenshots, using only the standard library (the Windows API directly, screencapture on
macOS, and gnome-screenshot, grim, scrot or ImageMagick on Linux). Never keystrokes.
"""

from __future__ import annotations

import base64
import platform
import re
import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

PRIVATE = ["password", "1password", "bitwarden", "keepass", "lastpass", "dashlane",
           "keychain", "credential", "sign in", "sign-in", "log in", "login", "incognito",
           "inprivate", "private browsing"]
SYSTEM = platform.system()
# Titles that animate (spinners, unread counts, progress) shouldn't count as switching windows.
NOISE = re.compile(r"\(\d+\)|\[\d+\]|\b\d{1,3}%|^\W+")


def same_window(a: str | None, b: str) -> bool:
    return a is not None and NOISE.sub("", a).strip() == NOISE.sub("", b).strip()
TMP = Path(tempfile.gettempdir()) / "pantheon-shadow"


# --- what's on screen -----------------------------------------------------------------------

if SYSTEM == "Windows":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.SetProcessDPIAware()

    def window_title() -> str:
        hwnd = user32.GetForegroundWindow()
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def mouse_down() -> str | None:
        for vk, name in ((0x01, "left"), (0x02, "right")):
            if user32.GetAsyncKeyState(vk) & 0x8000:
                return name
        return None

    def cursor() -> tuple[int, int]:
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    gdi32 = ctypes.windll.gdi32
    HANDLE = ctypes.c_void_p
    user32.GetDC.restype = HANDLE
    user32.GetDC.argtypes = [HANDLE]
    user32.ReleaseDC.argtypes = [HANDLE, HANDLE]
    gdi32.CreateCompatibleDC.restype = HANDLE
    gdi32.CreateCompatibleDC.argtypes = [HANDLE]
    gdi32.CreateDIBSection.restype = HANDLE
    gdi32.CreateDIBSection.argtypes = [HANDLE, ctypes.c_void_p, ctypes.c_uint,
                                       ctypes.POINTER(ctypes.c_void_p), HANDLE, ctypes.c_uint]
    gdi32.SelectObject.restype = HANDLE
    gdi32.SelectObject.argtypes = [HANDLE, HANDLE]
    gdi32.SetStretchBltMode.argtypes = [HANDLE, ctypes.c_int]
    gdi32.SetBrushOrgEx.argtypes = [HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    gdi32.StretchBlt.argtypes = [HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_uint]
    gdi32.DeleteObject.argtypes = [HANDLE]
    gdi32.DeleteDC.argtypes = [HANDLE]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32)]

    def windows_screenshot(max_width: int = 1600) -> str | None:
        """All monitors, scaled down with GDI, as a PNG data URL (no PowerShell, no packages)."""
        x, y, w, h = (user32.GetSystemMetrics(i) for i in (76, 77, 78, 79))  # virtual screen
        if w <= 0 or h <= 0:
            return None
        scale = min(1.0, max_width / w)
        ow, oh = max(int(w * scale) // 4 * 4, 4), max(int(h * scale), 1)  # 4-byte aligned rows
        screen = user32.GetDC(None)
        dc = gdi32.CreateCompatibleDC(screen)
        header = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=ow,
                                  biHeight=-oh, biPlanes=1, biBitCount=24)  # top-down BGR
        bits = ctypes.c_void_p()
        bmp = gdi32.CreateDIBSection(dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
        try:
            old = gdi32.SelectObject(dc, bmp)
            gdi32.SetStretchBltMode(dc, 4)  # HALFTONE: smooth downscaling
            gdi32.SetBrushOrgEx(dc, 0, 0, None)
            gdi32.StretchBlt(dc, 0, 0, ow, oh, screen, x, y, w, h, 0x00CC0020 | 0x40000000)
            bgr = ctypes.string_at(bits, ow * oh * 3)
            gdi32.SelectObject(dc, old)
        finally:
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(dc)
            user32.ReleaseDC(None, screen)
        rgb = bytearray(len(bgr))
        rgb[0::3], rgb[1::3], rgb[2::3] = bgr[2::3], bgr[1::3], bgr[0::3]
        return "data:image/png;base64," + base64.b64encode(png(ow, oh, bytes(rgb))).decode()

elif SYSTEM == "Darwin":
    def window_title() -> str:
        script = ('tell application "System Events" to set p to first application process whose '
                  'frontmost is true\nset t to name of p\ntry\nset t to t & " — " & name of '
                  'front window of p\nend try\nreturn t')
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=3, check=False)
        return r.stdout.strip()

    def mouse_down() -> str | None:
        return None

    def cursor() -> tuple[int, int]:
        return 0, 0

else:
    def window_title() -> str:
        try:
            wid = subprocess.run(["xprop", "-root", "_NET_ACTIVE_WINDOW"], capture_output=True,
                                 text=True, timeout=2, check=False).stdout.split("#")[-1].split(",")[0].strip()
            out = subprocess.run(["xprop", "-id", wid, "_NET_WM_NAME"], capture_output=True,
                                 text=True, timeout=2, check=False).stdout
            return out.split("=", 1)[1].strip().strip('"') if "=" in out else ""
        except (OSError, IndexError, subprocess.SubprocessError):
            return ""

    def mouse_down() -> str | None:
        return None

    def cursor() -> tuple[int, int]:
        return 0, 0


def png(width: int, height: int, rgb: bytes) -> bytes:
    """Encode 8-bit RGB pixels as PNG with the standard library."""
    row = width * 3
    raw = b"".join(b"\x00" + rgb[i * row:(i + 1) * row] for i in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def screenshot() -> str | None:
    """A JPEG (or PNG) data URL of the screen, or None if this system can't take one."""
    TMP.mkdir(parents=True, exist_ok=True)
    out = TMP / "shot.jpg"
    out.unlink(missing_ok=True)
    try:
        if SYSTEM == "Windows":
            return windows_screenshot()
        elif SYSTEM == "Darwin":
            subprocess.run(["screencapture", "-x", "-t", "jpg", str(out)], capture_output=True,
                           timeout=20, check=False)
            if out.exists():
                subprocess.run(["sips", "-Z", "1600", str(out)], capture_output=True, timeout=20, check=False)
        else:
            out = TMP / "shot.png"
            out.unlink(missing_ok=True)
            for cmd in (["gnome-screenshot", "-f", str(out)], ["grim", str(out)],
                        ["scrot", "-o", str(out)], ["import", "-window", "root", str(out)]):
                if shutil.which(cmd[0]):
                    subprocess.run(cmd, capture_output=True, timeout=20, check=False)
                    if out.exists():
                        break
    except (OSError, subprocess.SubprocessError):
        return None
    if not out.exists():
        return None
    kind = "png" if out.suffix == ".png" else "jpeg"
    return f"data:image/{kind};base64," + base64.b64encode(out.read_bytes()).decode()


# --- sending --------------------------------------------------------------------------------
