"""An agent's own checklist for the work in hand (Claude Code's TodoWrite, for Pantheon).

A long job drifts without one: a timed run's agent finished the four screens it was asked
for in eight minutes, then spent thirty-four more on checks nobody asked for. The agent
writes the whole list each time it changes (simple for the model, nothing to reconcile);
it is kept on the agent, shown in its system prompt at the start of each turn, and echoed
back by every update, so the current state is always in view without re-reading anything.
"""

from __future__ import annotations

from typing import Any

from app.core.db import SessionLocal
from app.models import Agent
from app.tools.base import S, ToolContext, ToolError, arr, obj, tool

STATUSES = ("pending", "in_progress", "done")
MAX_ITEMS = 30
MARK = {"pending": "[ ]", "in_progress": "[>]", "done": "[x]"}


def render(items: list[dict[str, Any]]) -> str:
    if not items:
        return "Your checklist is empty."
    done = sum(1 for i in items if i.get("status") == "done")
    lines = [f"{MARK.get(i.get('status', 'pending'), '[ ]')} {i.get('text', '')}" for i in items]
    head = ("All done." if done == len(items) else f"{done} of {len(items)} done.")
    return f"Checklist ({head})\n" + "\n".join(lines)


@tool(
    "todo",
    """Your checklist for the work in hand. Send the whole list each time it changes: every
item with its status (pending, in_progress, done). Keep exactly one item in_progress while
you work, mark items done as soon as they are, and add what you discover. Use it for any
job of more than a few steps; when every item is done, the job is done: hand it in and stop.""",
    obj({"items": arr(obj({"text": S, "status": {"type": "string", "enum": list(STATUSES)}},
                          ["text", "status"]))}, ["items"]),
    category="general",
)
async def todo(args: dict, ctx: ToolContext) -> str:
    items = [{"text": str(i["text"]).strip()[:300], "status": i["status"]}
             for i in args.get("items") or [] if str(i.get("text", "")).strip()]
    if len(items) > MAX_ITEMS:
        raise ToolError(f"keep the checklist to {MAX_ITEMS} items: group the small ones")
    async with SessionLocal() as session:
        agent = await session.get(Agent, ctx.agent.id)
        if agent is None:
            raise ToolError("agent not found")
        agent.todos = items
        await session.commit()
    ctx.agent.todos = items
    return render(items)
