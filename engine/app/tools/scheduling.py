"""Cron for agents: schedule reminders and recurring work for yourself."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.ids import utcnow
from app.services import schedules
from app.tools.base import I, S, ToolContext, ToolError, obj, tool

AMBIENT = ("schedule", "poll", "list_schedules", "cancel_schedule")


@tool("schedule", "Set a reminder or recurring job for yourself; when it's due, the message "
      "arrives in your inbox and wakes you. Use cron (\"0 9 * * 1-5\" = weekdays 09:00, "
      "\"*/30 * * * *\" = every 30 min; minimum 5 min apart), or at (ISO time, e.g. "
      "2026-10-01T14:00), or in_minutes. timezone: e.g. Europe/London (default UTC). "
      "To check something every few seconds, use poll. Never wait, sleep or watch in a "
      "loop inside your turn: nobody can reach you until it ends.",
      obj({"message": S, "cron": S, "at": S, "in_minutes": I, "timezone": S}, ["message"]),
      category="scheduling")
async def schedule(args: dict, ctx: ToolContext) -> str:
    at = None
    if args.get("in_minutes"):
        mins = int(args["in_minutes"])
        if mins < 1:
            raise ToolError("in_minutes must be at least 1")
        at = utcnow() + timedelta(minutes=mins)
    elif args.get("at"):
        try:
            at = datetime.fromisoformat(str(args["at"]))
        except ValueError:
            raise ToolError("at must be an ISO time like 2026-10-01T14:00") from None
    try:
        s = await schedules.create(ctx.agent, args["message"], cron=args.get("cron") or None,
                                   at=at, timezone=args.get("timezone") or "UTC")
    except schedules.ScheduleError as e:
        raise ToolError(str(e)) from None
    return (f"Scheduled {s.id}: " + (f"'{s.cron}' ({s.timezone})" if s.cron else "once")
            + f", next at {s.next_run_at:%Y-%m-%d %H:%M} UTC.")


@tool("poll", "Check something every few seconds for a while, without blocking: every "
      "every_seconds (at least 5) you're woken with message, for for_minutes (default 30, "
      "at most 1440), only while you're idle (a tick you can't take is skipped). Only one "
      "agent's poll runs at a time across Pantheon, so keep each check short: look, act if "
      "needed, end your turn. Each tick is a model call: poll as slowly as the task allows, "
      "and cancel_schedule as soon as you're done (e.g. the game is over).",
      obj({"message": S, "every_seconds": I, "for_minutes": I}, ["message", "every_seconds"]),
      category="scheduling")
async def poll(args: dict, ctx: ToolContext) -> str:
    try:
        s = await schedules.create_poll(ctx.agent, args["message"], int(args["every_seconds"]),
                                        int(args.get("for_minutes") or 30))
    except schedules.ScheduleError as e:
        raise ToolError(str(e)) from None
    return (f"Poll {s.id}: every {s.every_seconds} s until {s.ends_at:%H:%M} UTC. "
            "End this turn now; you'll be woken on each tick.")


@tool("list_schedules", "Your active reminders, recurring jobs and polls.", obj({}),
      category="scheduling")
async def list_schedules(args: dict, ctx: ToolContext) -> str:
    rows = await schedules.for_agent(ctx.agent.id)
    return "\n".join(
        f"- {s.id}: {_kind(s)}, next "
        f"{s.next_run_at:%Y-%m-%d %H:%M} UTC — {s.message[:120]}" for s in rows
    ) or "No active schedules."


def _kind(s) -> str:
    if s.every_seconds:
        return f"poll every {s.every_seconds} s until {s.ends_at:%H:%M} UTC"
    return s.cron + " (" + s.timezone + ")" if s.cron else "once"


@tool("cancel_schedule", "Cancel one of your schedules or polls.", obj({"id": S}, ["id"]),
      category="scheduling")
async def cancel_schedule(args: dict, ctx: ToolContext) -> str:
    try:
        s = await schedules.cancel(args["id"], agent_id=ctx.agent.id)
    except schedules.ScheduleError as e:
        raise ToolError(str(e)) from None
    return f"Cancelled {s.id}."
