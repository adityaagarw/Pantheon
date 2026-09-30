"""hold_meeting: an agent convenes a meeting (see services/meetings.py)."""

from __future__ import annotations

from app.services import meetings
from app.tools.base import B, S, ToolContext, ToolError, arr, obj, tool


@tool(
    "hold_meeting",
    """Call a meeting with colleagues to discuss, decide or plan together. Everyone
speaks in turn for a few rounds; you get back the transcript and minutes
(decisions + action items), which are also sent to every participant.
Styles: discussion, decision (converge on one outcome), brainstorm, standup
(status + blockers), review (critique a deliverable). Set create_tasks to turn
agreed action items into assigned tasks. Use meetings for decisions that need
several people's input — not for simple questions.""",
    obj({"participants": arr(S), "agenda": S,
         "style": {"type": "string", "enum": list(meetings.STYLES)},
         "rounds": {"type": "integer", "minimum": 1, "maximum": 6},
         "detail": {"type": "string", "enum": ["brief", "detailed"]},
         "create_tasks": B, "room": S}, ["participants", "agenda"]),
    category="communication",
    timeout=1800,
)
async def hold_meeting(args: dict, ctx: ToolContext) -> str:
    try:
        req = await meetings.prepare(
            ctx.org, ctx.agent, args["participants"], args["agenda"], style=args.get("style"),
            rounds=args.get("rounds"), detail=args.get("detail", "brief"),
            create_tasks=args.get("create_tasks"), room=args.get("room"), turn_id=ctx.turn_id,
            depth=ctx.depth,
        )
        out = await meetings.run(req)
    except meetings.MeetingError as e:
        raise ToolError(str(e)) from None
    tasks_line = f"\n\nTasks created: {', '.join(out['tasks'])}" if out["tasks"] else ""
    return ("Transcript:\n" + "\n".join(out["transcript"]) + f"\n\nMinutes:\n{out['minutes']}"
            + tasks_line)
