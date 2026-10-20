"""Computer use: agents operate a computer with mouse and keyboard.

Which computer is the user's choice (app/computer/__init__.py): this computer (their own
screen, mouse and keyboard), a plugin's (the sandboxed desktop of the cua-desktop pack),
or none. It's one shared machine, so an agent holds a short lease while it works
(others are told to wait). Every action returns a fresh screenshot, which
vision-capable models see. On this computer the user can take it back at any moment.
"""

from __future__ import annotations

import asyncio
import time

from app import computer as computers
from app.computer.cua import ComputerError
from app.computer.native import TakenBack
from app.core.config import settings
from app.events import bus
from app.tools.base import B, I, S, ToolContext, ToolError, ToolOutput, obj, tool

_lease: dict[str, float | str] = {}  # {"agent": id, "name": name, "until": monotonic}
_lock = asyncio.Lock()
SETTLE = 0.7  # let the UI react before the screenshot


def computer():
    """The computer to drive (ToolError when computer use is off or paused)."""
    try:
        return computers.current()
    except computers.ComputerOff as e:
        raise ToolError(str(e)) from None


def _taken_back(e: Exception) -> ToolError:
    """The user took the computer back: pause computer use and tell the agent."""
    computers.pause("you took the computer back")
    _lease.clear()
    return ToolError(str(e))


async def _claim(ctx: ToolContext) -> None:
    now = time.monotonic()
    holder = _lease.get("agent")
    if holder and holder != ctx.agent.id and float(_lease.get("until", 0)) > now:
        wait = int(float(_lease["until"]) - now)
        raise ToolError(f"the computer is in use by {_lease.get('name')} (free in ~{wait}s); "
                        "try again later")
    _lease.update(agent=ctx.agent.id, name=ctx.agent.name,
                  until=now + settings.computer_lease_seconds)
    computers.active.update(agentId=ctx.agent.id, agent=ctx.agent.name, orgId=ctx.org.id,
                            at=time.time())


async def _act(ctx: ToolContext, what: str, command: str | None, **params) -> ToolOutput:
    async with _lock:
        await _claim(ctx)
        c = computer()
        try:
            detail = ""
            if command:
                r = await c.cmd(command, **params)
                extra = {k: v for k, v in r.items() if k not in ("success", "image_data")}
                if extra:
                    detail = " " + str(extra)[:1500]
            await asyncio.sleep(SETTLE if command else 0)
            shot = await c.screenshot()
        except ComputerError as e:
            raise ToolError(str(e)) from None
        except TakenBack as e:
            raise _taken_back(e) from None
    await bus.publish("computer.action", {"action": what}, org_id=ctx.org.id,
                      agent_id=ctx.agent.id)
    return ToolOutput(f"{what}.{detail} The screen after it is attached (coordinates are "
                      "pixels of that screenshot, from its top-left corner).", [shot])


def ctool(name: str, description: str, params: dict, approval: str = "auto"):
    return tool(name, description, params, category="computer", approval=approval,
                side_effects=True, timeout=90)


@ctool("computer_screenshot", "Look at the computer's screen (the user's own computer, or a "
       "sandboxed desktop: whichever they set up).",
       obj({}))
async def computer_screenshot(args: dict, ctx: ToolContext) -> ToolOutput:
    return await _act(ctx, "Took a screenshot", None)


@ctool("computer_click", "Click at pixel (x, y) on the computer's screen.",
       obj({"x": I, "y": I, "button": {"type": "string", "enum": ["left", "right"]},
            "double": B}, ["x", "y"]))
async def computer_click(args: dict, ctx: ToolContext) -> ToolOutput:
    x, y = int(args["x"]), int(args["y"])
    if args.get("double"):
        return await _act(ctx, f"Double-clicked at ({x}, {y})", "double_click", x=x, y=y)
    command = "right_click" if args.get("button") == "right" else "left_click"
    return await _act(ctx, f"{'Right-' if command == 'right_click' else ''}Clicked at ({x}, {y})",
                      command, x=x, y=y)


@ctool("computer_type", "Type text on the computer's keyboard (into whatever has focus).",
       obj({"text": S}, ["text"]))
async def computer_type(args: dict, ctx: ToolContext) -> ToolOutput:
    return await _act(ctx, f"Typed {len(args['text'])} characters", "type_text",
                      text=args["text"])


@ctool("computer_key", "Press a key or a combination, e.g. \"Return\", \"Escape\", \"Tab\", "
       "\"ctrl+l\", \"ctrl+shift+t\", \"alt+F4\".", obj({"keys": S}, ["keys"]))
async def computer_key(args: dict, ctx: ToolContext) -> ToolOutput:
    keys = [k.strip() for k in str(args["keys"]).split("+") if k.strip()]
    if not keys:
        raise ToolError("no key given")
    if len(keys) == 1:
        return await _act(ctx, f"Pressed {keys[0]}", "press_key", key=keys[0])
    return await _act(ctx, f"Pressed {'+'.join(keys)}", "hotkey", keys=keys)


@ctool("computer_scroll", "Scroll the screen (at x, y if given).",
       obj({"direction": {"type": "string", "enum": ["up", "down"]}, "amount": I,
            "x": I, "y": I}, ["direction"]))
async def computer_scroll(args: dict, ctx: ToolContext) -> ToolOutput:
    if args.get("x") is not None and args.get("y") is not None:
        try:
            await computer().cmd("move_cursor", x=int(args["x"]), y=int(args["y"]))
        except ComputerError as e:
            raise ToolError(str(e)) from None
        except TakenBack as e:
            raise _taken_back(e) from None
    clicks = max(1, min(int(args.get("amount") or 3), 20))
    command = "scroll_up" if args["direction"] == "up" else "scroll_down"
    return await _act(ctx, f"Scrolled {args['direction']} {clicks}", command, clicks=clicks)


@ctool("computer_drag", "Drag with the mouse from (x1, y1) to (x2, y2).",
       obj({"x1": I, "y1": I, "x2": I, "y2": I}, ["x1", "y1", "x2", "y2"]))
async def computer_drag(args: dict, ctx: ToolContext) -> ToolOutput:
    try:
        await computer().cmd("move_cursor", x=int(args["x1"]), y=int(args["y1"]))
    except ComputerError as e:
        raise ToolError(str(e)) from None
    except TakenBack as e:
        raise _taken_back(e) from None
    return await _act(ctx, f"Dragged to ({args['x2']}, {args['y2']})", "drag_to",
                      x=int(args["x2"]), y=int(args["y2"]))


@ctool("computer_open", "Open a URL or a file on the computer with its default application.",
       obj({"target": S}, ["target"]))
async def computer_open(args: dict, ctx: ToolContext) -> ToolOutput:
    return await _act(ctx, f"Opened {args['target']}", "open", target=args["target"])


@ctool("computer_shell", "Run a shell command on the computer (the one the computer tools "
       "drive) and get its output.", obj({"command": S}, ["command"]), approval="ask")
async def computer_shell(args: dict, ctx: ToolContext) -> str:
    async with _lock:
        await _claim(ctx)
        try:
            r = await computer().cmd("run_command", timeout=180, command=args["command"])
        except ComputerError as e:
            raise ToolError(str(e)) from None
    out = r.get("stdout") or ""
    err = r.get("stderr") or ""
    code = r.get("return_code", r.get("returncode", ""))
    return f"exit {code}\n{out}" + (f"\n[stderr]\n{err}" if err else "")
