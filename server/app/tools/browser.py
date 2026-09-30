"""The built-in browser: agents browse the web in a real (headless) Chromium.

Pages come back as text with numbered interactive elements, plus a screenshot
for vision models. The user watches each agent's browser live in the UI.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.browser.manager import BrowserError, Session, allowed, manager
from app.events import bus
from app.tools.base import B, I, S, ToolContext, ToolError, ToolOutput, obj, tool

MAX_TEXT = 6000


def btool(name: str, description: str, params: dict[str, Any]):
    return tool(name, description, params, category="browser", timeout=90)


async def _session(ctx: ToolContext) -> Session:
    try:
        return await manager.session(ctx.agent.id)
    except BrowserError as e:
        raise ToolError(str(e)) from None


async def _report(ctx: ToolContext, s: Session, what: str) -> ToolOutput:
    try:
        data = await manager.snapshot(s)
    except Exception as e:  # noqa: BLE001 - page crashed / navigated away mid-read
        raise ToolError(f"couldn't read the page: {e}") from None
    await bus.publish("browser.updated", {"url": s.url, "title": s.title}, org_id=ctx.org.id,
                      agent_id=ctx.agent.id)
    items = "\n".join(data.get("items") or []) or "(none)"
    text = (data.get("text") or "")[:MAX_TEXT]
    return ToolOutput(
        f"{what}\nTitle: {data.get('title')}\nURL: {data.get('url')}\n\n"
        f"Interactive elements (use their numbers):\n{items}\n\nPage text:\n{text}",
        [manager.data_url(s)])


def _locator(s: Session, element: Any):
    ref = str(element).strip().lstrip("[").rstrip("]")
    if ref.isdigit():
        return s.page.locator(f'[data-pid="{ref}"]').first
    return s.page.get_by_text(ref, exact=False).first


@btool("browser_open", "Open a web page in your browser.", obj({"url": S}, ["url"]))
async def browser_open(args: dict, ctx: ToolContext) -> ToolOutput:
    url = str(args["url"]).strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if not await asyncio.to_thread(allowed, url):
        raise ToolError("that address is private or internal; the browser only visits the web")
    s = await _session(ctx)
    try:
        await s.page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    except Exception as e:  # noqa: BLE001 - navigation errors go back to the model
        raise ToolError(f"couldn't open {url}: {str(e).splitlines()[0]}") from None
    return await _report(ctx, s, f"Opened {url}.")


@btool("browser_read", "Read the current page again (text, interactive elements, screenshot).",
       obj({}))
async def browser_read(args: dict, ctx: ToolContext) -> ToolOutput:
    s = await _session(ctx)
    return await _report(ctx, s, "Current page.")


@btool("browser_click", "Click an element: its number from the element list, or its visible "
       "text.", obj({"element": S}, ["element"]))
async def browser_click(args: dict, ctx: ToolContext) -> ToolOutput:
    s = await _session(ctx)
    try:
        await _locator(s, args["element"]).click(timeout=10_000)
        await s.page.wait_for_timeout(600)
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"couldn't click {args['element']}: {str(e).splitlines()[0]}") from None
    return await _report(ctx, s, f"Clicked {args['element']}.")


@btool("browser_type", "Type into a field (its number or visible text/placeholder); "
       "submit=true presses Enter afterwards.",
       obj({"element": S, "text": S, "submit": B}, ["element", "text"]))
async def browser_type(args: dict, ctx: ToolContext) -> ToolOutput:
    s = await _session(ctx)
    try:
        loc = _locator(s, args["element"])
        await loc.fill(args["text"], timeout=10_000)
        if args.get("submit"):
            await loc.press("Enter")
            await s.page.wait_for_timeout(1000)
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"couldn't type into {args['element']}: {str(e).splitlines()[0]}") \
            from None
    return await _report(ctx, s, f"Typed into {args['element']}.")


@btool("browser_press", "Press a key in the page, e.g. Enter, Escape, PageDown, Control+A.",
       obj({"key": S}, ["key"]))
async def browser_press(args: dict, ctx: ToolContext) -> ToolOutput:
    s = await _session(ctx)
    try:
        await s.page.keyboard.press(args["key"])
        await s.page.wait_for_timeout(500)
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"couldn't press {args['key']}: {str(e).splitlines()[0]}") from None
    return await _report(ctx, s, f"Pressed {args['key']}.")


@btool("browser_scroll", "Scroll the page up or down (amount in screens, default 1).",
       obj({"direction": {"type": "string", "enum": ["up", "down"]}, "amount": I},
           ["direction"]))
async def browser_scroll(args: dict, ctx: ToolContext) -> ToolOutput:
    s = await _session(ctx)
    screens = max(1, min(int(args.get("amount") or 1), 10))
    dy = 700 * screens * (-1 if args["direction"] == "up" else 1)
    await s.page.mouse.wheel(0, dy)
    await s.page.wait_for_timeout(400)
    return await _report(ctx, s, f"Scrolled {args['direction']}.")


@btool("browser_back", "Go back to the previous page.", obj({}))
async def browser_back(args: dict, ctx: ToolContext) -> ToolOutput:
    s = await _session(ctx)
    try:
        await s.page.go_back(wait_until="domcontentloaded", timeout=20_000)
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"couldn't go back: {str(e).splitlines()[0]}") from None
    return await _report(ctx, s, "Went back.")
