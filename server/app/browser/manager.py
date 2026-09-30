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
    shot: bytes = b""
    title: str = ""
    url: str = ""


class BrowserManager:
    def __init__(self) -> None:
        self._pw: Any = None
        self._browser: Any = None
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task | None = None

    async def _ensure(self) -> Any:
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise BrowserError("the browser isn't installed (pip install playwright)") from None
        try:
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        except Exception as e:  # noqa: BLE001 - missing binaries etc.
            raise BrowserError(f"couldn't start the browser: {e}. Run "
                               "`playwright install chromium`.") from None
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap())
        return self._browser

    async def _new_context(self) -> Any:
        browser = await self._ensure()
        context = await browser.new_context(viewport=VIEWPORT, device_scale_factor=1)

        async def guard(route: Any) -> None:
            if await asyncio.to_thread(allowed, route.request.url):
                await route.continue_()
            else:
                await route.abort("blockedbyclient")

        await context.route("**/*", guard)
        return context

    async def session(self, key: str) -> Session:
        async with self._lock:
            s = self._sessions.get(key)
            if s is None or s.page.is_closed():
                context = await self._new_context()
                page = await context.new_page()
                s = Session(context, page)
                page.on("console", lambda m, s=s: m.type == "error" and not _noise(m.text)
                        and s.console.append(f"error: {m.text}"[:500]))
                page.on("pageerror", lambda e, s=s: s.console.append(f"error: {e}"[:500]))
                self._sessions[key] = s
            s.last_used = time.monotonic()
            return s

    async def close(self, key: str) -> None:
        s = self._sessions.pop(key, None)
        if s is not None:
            try:
                await s.context.close()
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
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()
        self._browser = self._pw = None


manager = BrowserManager()
