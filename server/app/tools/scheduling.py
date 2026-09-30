"""Cron for agents: schedule reminders and recurring work for yourself."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.ids import utcnow
from app.services import schedules
from app.tools.base import I, S, ToolContext, ToolError, obj, tool

AMBIENT = ("schedule", "list_schedules", "cancel_schedule")


@tool("schedule", "Set a reminder or recurring job for yourself; when it's due, the message "
      "arrives in your inbox and wakes you. Use cron (\"0 9 * * 1-5\" = weekdays 09:00, "
      "\"*/30 * * * *\" = every 30 min; minimum 5 min apart), or at (ISO time, e.g. "
      "2026-10-01T14:00), or in_minutes. timezone: e.g. Europe/London (default UTC).",
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


@tool("list_schedules", "Your active reminders and recurring jobs.", obj({}),
      category="scheduling")
async def list_schedules(args: dict, ctx: ToolContext) -> str:
    rows = await schedules.for_agent(ctx.agent.id)
    return "\n".join(
        f"- {s.id}: {s.cron + ' (' + s.timezone + ')' if s.cron else 'once'}, next "
        f"{s.next_run_at:%Y-%m-%d %H:%M} UTC — {s.message[:120]}" for s in rows
    ) or "No active schedules."


@tool("cancel_schedule", "Cancel one of your schedules.", obj({"id": S}, ["id"]),
      category="scheduling")
async def cancel_schedule(args: dict, ctx: ToolContext) -> str:
    try:
        s = await schedules.cancel(args["id"], agent_id=ctx.agent.id)
    except schedules.ScheduleError as e:
        raise ToolError(str(e)) from None
    return f"Cancelled {s.id}."
