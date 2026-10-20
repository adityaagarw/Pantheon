"""A real (headless Chromium) browser for agents, via Playwright.

Each agent gets its own browser context and tab, created on first use and
closed after a while idle. Pages are described to the model as text with
numbered interactive elements ("[3] button: Sign in"), so any model can use
the browser; vision models also get a screenshot. The latest screenshot per
agent is kept for the Pantheon UI's live view.

Navigation to private / internal addresses is blocked (agents must not reach
Pantheon's own API or other containers through the browser), except Stage
pages, which the browser opens to check they render.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import socket
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from app.core.config import settings

log = logging.getLogger(__name__)
_install_lock = asyncio.Lock()


async def install_chromium() -> None:
    """Download Playwright's Chromium (once, ~150 MB): an installed Pantheon ships without it.
    It goes where Playwright looks by default (ms-playwright in the user's cache)."""
    import sys

    async with _install_lock:
        log.info("downloading Chromium for the browser tool")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "playwright", "install", "chromium",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), 900)
        except TimeoutError:
            proc.kill()
            raise BrowserError("downloading Chromium took too long; try again") from None
        if proc.returncode:
            tail = out.decode(errors="replace").strip().splitlines()[-3:]
            raise BrowserError("couldn't download Chromium for the browser: " + " ".join(tail))
IDLE_SECONDS = 600
VIEWPORT = {"width": 1280, "height": 800}
INTERNAL_HOSTS = {"localhost", "backend", "frontend", "postgres", "desktop", "https",
                  "host.docker.internal"}

# Numbers the visible interactive elements and returns them with the page text.
SNAPSHOT_JS = r"""
() => {
  const sel = 'a[href],button,input,textarea,select,[role=button],[role=link],[role=tab],' +
              '[role=checkbox],[role=menuitem],[onclick],[contenteditable=true],summary';
  let n = 0;
  const items = [];
  document.querySelectorAll('[data-pid]').forEach(e => e.removeAttribute('data-pid'));
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.visibility === 'hidden' || st.display === 'none') continue;
    if (r.bottom < 0 || r.top > innerHeight * 3) continue;
    n += 1;
    el.setAttribute('data-pid', String(n));
    const tag = el.tagName.toLowerCase();
    const kind = el.getAttribute('role') || (tag === 'input' ? (el.type || 'input') : tag);
    const label = (el.getAttribute('aria-label') || el.innerText || el.value ||
                   el.getAttribute('placeholder') || el.getAttribute('title') ||
                   el.getAttribute('name') || el.getAttribute('href') || '')
                   .replace(/\s+/g, ' ').trim().slice(0, 90);
    items.push(`[${n}] ${kind}: ${label}`);
    if (n >= 150) break;
  }
  const text = (document.body?.innerText || '').replace(/\n{3,}/g, '\n\n').slice(0, 8000);
  return {title: document.title, url: location.href, text, items};
}
"""


NOISE = ("GL Driver Message", "GPU stall", "WebGL-", "Automatic fallback to software WebGL")


def _noise(text: str) -> bool:
    """Headless graphics chatter that isn't a problem with the page."""
    return any(n in text for n in NOISE)


def page_error(e: Any) -> str:
    """A script error with where it was thrown ("... (studio-profile.html line 612)"), so it
    can be fixed from the report instead of hunted down with scripts."""
    import re

    msg = str(getattr(e, "message", "") or e).strip().splitlines()[0]
    for frame in str(getattr(e, "stack", "") or "").splitlines()[1:]:
        if m := re.search(r"([^/\s()]+):(\d+):\d+\)?\s*$", frame):
            return f"error: {msg} ({m.group(1)} line {m.group(2)})"[:500]
    return f"error: {msg}"[:500]


class BrowserError(RuntimeError):
    pass


_verdicts: dict[str, tuple[bool, float]] = {}


def _private(host: str) -> bool:
    if not host or host.lower() in INTERNAL_HOSTS or host.endswith(".internal"):
        return True
    cached = _verdicts.get(host)
    if cached and time.monotonic() - cached[1] < 300:
        return cached[0]
    verdict = _resolve_private(host)
    _verdicts[host] = (verdict, time.monotonic())
    return verdict


def _resolve_private(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False  # let the browser report the DNS error
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    return False


def allowed(url: str) -> bool:
    if url.startswith(("about:", "data:", "blob:")):
        return True
    if url.startswith(settings.stage_base_url.rstrip("/") + "/api/v1/stage"):
        return True
    from app.services import preview

    if preview.serves(url):  # a workspace page checked as a site (stage_check)
        return True
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return False
    return not _private(p.hostname or "")


@dataclass
class Session:
    context: Any
    page: Any
    last_used: float = field(default_factory=time.monotonic)
    console: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)  # console.log/info/warn lines
    shot: bytes = b""
    title: str = ""
    url: str = ""
    where: str = "builtin"  # or "sandbox": a tab in the sandbox desktop's browser (sandboxed.py)


class BrowserManager:
    def __init__(self) -> None:
        self._pw: Any = None
        self._browser: Any = None
        self._remote: Any = None  # the sandbox desktop's browser
        self._guarded: set[int] = set()
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task | None = None

    async def _launch(self) -> Any:
        return await self._pw.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])

    async def _ensure(self) -> Any:
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise BrowserError("the browser isn't installed (pip install playwright)") from None
        try:
            self._pw = self._pw or await async_playwright().start()
            try:
                self._browser = await self._launch()
            except Exception as e:  # noqa: BLE001 - Chromium isn't downloaded yet
                if "playwright install" not in str(e) and "Executable doesn't exist" not in str(e):
                    raise
                await install_chromium()
                self._browser = await self._launch()
        except BrowserError:
            raise
        except Exception as e:  # noqa: BLE001 - missing system libraries etc.
            raise BrowserError(f"couldn't start the browser: {e}") from None
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap())
        return self._browser

    @staticmethod
    async def _guard(context: Any) -> None:
        async def guard(route: Any) -> None:
            if await asyncio.to_thread(allowed, route.request.url):
                await route.continue_()
            else:
                await route.abort("blockedbyclient")

        await context.route("**/*", guard)

    @staticmethod
    async def _block(page: Any, key: str) -> None:
        """The hosts this agent's organization blocks (core/egress.py), page by page: the
        sandbox's tabs share one context."""
        from app.core import egress

        async def guard(route: Any) -> None:
            if egress.blocked_for(key, route.request.url):
                await route.abort("blockedbyclient")
            else:
                await route.fallback()

        await page.route("**/*", guard)

    async def _new_context(self) -> Any:
        browser = await self._ensure()
        context = await browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
        await self._guard(context)
        return context

    async def _sandbox_context(self) -> Any:
        """The sandbox browser's own profile (its sign-ins last); each agent gets a tab."""
        from app.browser import sandboxed

        if self._remote is None or not self._remote.is_connected():
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                raise BrowserError("the browser isn't installed (pip install playwright)") from None
            self._pw = self._pw or await async_playwright().start()
            if self._reaper is None or self._reaper.done():
                self._reaper = asyncio.create_task(self._reap())
            try:
                self._remote = await sandboxed.connect(self._pw)
            except sandboxed.SandboxBrowserError as e:
                raise BrowserError(str(e)) from None
            self._guarded.clear()
        contexts = self._remote.contexts
        context = contexts[0] if contexts else await self._remote.new_context()
        if id(context) not in self._guarded:
            await self._guard(context)
            self._guarded.add(id(context))
        return context

    async def session(self, key: str, where: str = "builtin") -> Session:
        async with self._lock:
            s = self._sessions.get(key)
            if s is not None and s.where != where:
                await self._close(key)
                s = None
            if s is None or s.page.is_closed():
                if where == "sandbox":
                    context = await self._sandbox_context()
                else:
                    context = await self._new_context()
                page = await context.new_page()
                await self._block(page, key)
                if where == "sandbox":
                    await page.bring_to_front()
                s = Session(context, page, where=where)
                page.on("console", lambda m, s=s: (
                    s.console.append(f"error: {m.text}"[:500]) if m.type == "error"
                    and not _noise(m.text) else m.type in ("log", "info", "warning")
                    and len(s.log) < 200 and s.log.append(m.text[:2000])))
                page.on("pageerror", lambda e, s=s: s.console.append(page_error(e)))
                self._sessions[key] = s
            s.last_used = time.monotonic()
            return s

    async def close(self, key: str) -> None:
        from app.browser import sandboxed

        sandboxed.forget(key)
        await self._close(key)

    async def _close(self, key: str) -> None:
        s = self._sessions.pop(key, None)
        if s is not None:
            try:
                # A sandbox tab shares the profile with the others: only the tab goes.
                await (s.page.close() if s.where == "sandbox" else s.context.close())
            except Exception:  # noqa: BLE001
                pass

    async def snapshot(self, s: Session, *, screenshot: bool = True) -> dict[str, Any]:
        try:
            await s.page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except Exception:  # noqa: BLE001 - describe whatever is there
            pass
        data = await s.page.evaluate(SNAPSHOT_JS)
        if screenshot:
            s.shot = await s.page.screenshot(type="jpeg", quality=65)
        s.title, s.url = data.get("title", ""), data.get("url", "")
        return data

    def data_url(self, s: Session) -> str:
        return "data:image/jpeg;base64," + base64.b64encode(s.shot).decode()

    def latest(self, key: str) -> Session | None:
        return self._sessions.get(key)

    async def _reap(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time.monotonic()
            for key, s in list(self._sessions.items()):
                if now - s.last_used > IDLE_SECONDS:
                    await self.close(key)

    async def stop(self) -> None:
        for key in list(self._sessions):
            await self.close(key)
        if self._reaper:
            self._reaper.cancel()
        from app.browser import sandboxed

        sandboxed.stop()
        self._remote = None  # the sandbox's browser keeps running, with its tabs closed
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()
        self._browser = self._pw = None


manager = BrowserManager()
