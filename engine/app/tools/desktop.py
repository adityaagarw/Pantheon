"""desktop_* tools: agents work in the user's apps through cua-driver (app/computer/driver.py).

They act on one app window at a time, in the background: the user keeps their mouse and
keyboard. Each agent has its own driver session (and its own cursor on screen). They work
when "This computer" is chosen in Settings → This computer, and stop when the user presses
Stop. Names, descriptions and schemas come from cua-driver itself (driver_tools.json).
"""

from __future__ import annotations

import time

from app import computer as computers
from app.computer.driver import TOOLS, DriverError, driver
from app.events import bus
from app.tools.base import ToolContext, ToolError, ToolOutput, ToolSpec, register

BACKENDS = ("driver", "cua")  # this computer through cua-driver, or the sandbox desktop


def _check() -> None:
    if computers.backend_id() not in BACKENDS:
        raise ToolError("these tools work when the user chooses \"This computer\" or the "
                        "sandbox desktop in Settings → This computer")
    if (why := computers.paused()) is not None:
        raise ToolError(f"computer use is paused ({why})")


async def _driver():
    if computers.backend_id() == "cua":
        from app.computer import sandbox

        return await sandbox.driver()
    return driver


def _handler(name: str, driver_tool: str):
    async def handler(args: dict, ctx: ToolContext) -> ToolOutput:
        _check()
        computers.active.update(agentId=ctx.agent.id, agent=ctx.agent.name, orgId=ctx.org.id,
                                at=time.time())
        if isinstance(args.get("pid"), int) and isinstance(args.get("window_id"), int):
            computers.active.update(pid=args["pid"], windowId=args["window_id"])
        call = {**args, "session": f"pantheon-{ctx.agent.id}"}
        try:
            text, images, error = await (await _driver()).call(driver_tool, call)
        except DriverError as e:
            raise ToolError(str(e)) from None
        _check()  # Stop pressed meanwhile: say so instead of carrying on
        await bus.publish("computer.action", {"action": name}, org_id=ctx.org.id,
                          agent_id=ctx.agent.id)
        if error:
            raise ToolError(text)
        return ToolOutput(text, images)

    return handler


for _name, _tool in TOOLS.items():
    register(ToolSpec(_name, "[This computer] " + _tool["description"], _tool["parameters"],
                      _handler(_name, _tool["driver_tool"]), category="computer",
                      default_approval="auto", timeout=180, side_effects=True))
