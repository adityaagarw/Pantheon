"""Just-in-time subagents: a helper an agent starts for one self-contained job.

The helper runs on the agent's model, with the agent's tools and workspace, in a
fresh context: it never sees the agent's history, and the agent sees only its
final report. That keeps a long search or a fix-and-test loop out of the
agent's own context. Nothing persists: it is not a team member and has no
inbox, thread or memory. It can't talk to anyone or wait for an approval, so
messaging, meetings, tasks and any tool set to "ask" are left out.

Several spawns in one step run side by side; across an org, at most as many
helpers run at once as the org's concurrency limit (the agents that started
them are waiting meanwhile).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from sqlalchemy import update

from app.core.db import SessionLocal
from app.events import bus
from app.models import Org, Turn
from app.tools.base import I, S, ToolContext, ToolError, arr, obj, tool

DEFAULT_STEPS = 40
MAX_STEPS = 200
DEFAULT_PARALLEL = 3  # helpers running at once in an org without a concurrency limit

# A helper works; it doesn't take part in the team or act on the agent's behalf in it.
LEFT_OUT = {
    "spawn_subagent", "send_message", "post_message", "create_channel", "update_channel",
    "hold_meeting", "create_task", "update_task",
    "request_feature", "remember", "ask_zeus", "schedule", "list_schedules", "cancel_schedule",
    "look_around", "move_to", "emote", "say_aloud", "pick_up", "put_down", "give", "use_object",
    "life_status", "life_do", "life_want",
}

SYSTEM = """You are a temporary helper that {name}{role} in {org} started for one job.
Do the job with your tools, then reply with your report in plain text and no tool calls.

- Only your final reply reaches {name}; nothing else you see or write does. Put in it what
  you found or did, with exact file paths, names, commands and numbers, and what is left.
- No one can answer you. When something is unclear, make the sensible choice and say which.
- Your workdir is {workdir}; you share it with {name}, so leave the files in good order.
- Be quick: you have at most {steps} steps. Read only what the job needs."""

_gates: dict[str, tuple[int, asyncio.Semaphore]] = {}


def _gate(org: Org) -> asyncio.Semaphore:
    size = int((org.settings or {}).get("max_concurrent") or 0) or DEFAULT_PARALLEL
    held = _gates.get(org.id)
    if held is None or held[0] != size:
        held = _gates[org.id] = (size, asyncio.Semaphore(size))
    return held[1]


@tool(
    "spawn_subagent",
    """Start a temporary helper for one self-contained job and get back its report.
The helper has your tools and workspace but a fresh context: it doesn't see your
conversation, so say everything it needs (goal, files, constraints, what to report).
Use it for work whose steps you don't need to see, e.g. searching a codebase,
reading many files to answer a question, or a fix-and-test loop; it keeps that
work out of your own context. Call it several times in one step to run helpers
side by side. It can't message anyone, so it reports only to you.""",
    obj({
        "task": {**S, "description": "The whole job, self-contained: what to do and what to "
                 "report back."},
        "tools": {**arr(S), "description": "Optional: only these of your tools (default: all "
                  "you have, except messaging, tasks and tools that need approval)."},
        "max_steps": {**I, "minimum": 1, "maximum": MAX_STEPS,
                      "description": f"Step limit (default {DEFAULT_STEPS})."},
    }, ["task"]),
    category="agents",
    timeout=3600.0,
)
async def spawn_subagent(args: dict, ctx: ToolContext) -> str:
    from app.agents.graph import _record_tool_call, compact_point, estimate_tokens
    from app.agents.llm import text_of
    from app.llm.providers import resolve_for_agent
    from app.tools.resolve import clip, execute, resolve_tools

    task = str(args["task"]).strip()
    if not task:
        raise ToolError("task is required")
    usable = {n: t for n, t in (await resolve_tools(ctx.org, ctx.agent)).tools.items()
              if t.approval == "auto" and n not in LEFT_OUT}
    if wanted := args.get("tools"):
        missing = [n for n in wanted if n not in usable]
        if missing:
            raise ToolError(f"a subagent can't use {', '.join(missing)}. It can use: "
                            f"{', '.join(sorted(usable)) or 'nothing'}")
        usable = {n: usable[n] for n in wanted}
    steps = min(int(args.get("max_steps") or DEFAULT_STEPS), MAX_STEPS)
    model = await resolve_for_agent(ctx.agent, ctx.org)
    schemas = [t.openai_schema() for t in usable.values()] or None
    label = f"subagent {ctx.tool_call_id[-4:]}"

    async def status(detail: str) -> None:
        await bus.publish("agent.status", {"status": "working", "detail": f"{label}: {detail}",
                                           "turnId": ctx.turn_id},
                          org_id=ctx.org.id, agent_id=ctx.agent.id, persist=False)

    async def run_tool(call: dict[str, Any], n: int) -> ToolMessage:
        name, call_args = call["name"], call.get("args") or {}
        call_id = f"{ctx.tool_call_id}.{n}"
        t = usable.get(name)
        if t is None:
            return ToolMessage(content=f"ERROR: tool '{name}' is not available. Available: "
                               f"{', '.join(sorted(usable)) or 'none'}.",
                               tool_call_id=call["id"], name=name, status="error")
        await status(f"using {name}")
        started = time.monotonic()
        sub = ToolContext(org=ctx.org, agent=ctx.agent, turn_id=ctx.turn_id,
                          tool_call_id=call_id, depth=ctx.depth)
        out, ok = await execute(t, call_args, sub)
        await _record_tool_call(call_id, ctx.org.id, ctx.agent.id, ctx.turn_id,
                                f"subagent:{name}", call_args, "ok" if ok else "error", out,
                                int((time.monotonic() - started) * 1000))
        return ToolMessage(content=clip(out), tool_call_id=call["id"], name=name,
                           status="success" if ok else "error")

    role = f" ({ctx.agent.role})" if ctx.agent.role else ""
    messages: list[BaseMessage] = [
        SystemMessage(content=SYSTEM.format(name=ctx.agent.name, role=role, org=ctx.org.name,
                                            workdir=ctx.workdir, steps=steps)),
        HumanMessage(content=task)]
    point = compact_point(model)
    measured, mark, used, calls = 0, len(messages), 0, 0
    async with _gate(ctx.org):
        for step in range(steps + 1):
            size = measured + estimate_tokens(messages[mark:]) if measured \
                else estimate_tokens(messages) + sum(len(str(s)) for s in schemas or []) // 3
            final = step == steps or size > point
            prompt = messages
            if final:
                prompt = [*messages, HumanMessage(content=(
                    "[system] Stop here (step or context limit): reply now with your report "
                    "in plain text, no tool calls, including what is left undone."))]
            await status("thinking")
            ai = await _think(model, prompt, ctx, None if final else schemas, status)
            usage = ai.usage_metadata or {}
            tin, tout = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
            measured, mark, used = tin + tout, len(messages) + 1, used + tin + tout
            if ctx.turn_id:
                async with SessionLocal() as session:
                    await session.execute(update(Turn).where(Turn.id == ctx.turn_id).values(
                        input_tokens=Turn.input_tokens + tin,
                        output_tokens=Turn.output_tokens + tout,
                        cost_usd=Turn.cost_usd + model.cost(tin, tout)))
                    await session.commit()
            text = text_of(ai).strip()
            if final or not ai.tool_calls:
                report = text or "(the subagent finished without a report)"
                return (f"{report}\n\n[subagent: {step + 1} steps, {calls} tool calls, "
                        f"{used:,} tokens]")
            for i, tc in enumerate(ai.tool_calls):
                if not tc.get("id"):
                    tc["id"] = f"sub_{step}_{i}"
            messages.append(ai)
            results = await asyncio.gather(*(run_tool(dict(tc), calls + i)
                                             for i, tc in enumerate(ai.tool_calls)))
            calls += len(ai.tool_calls)
            messages.extend(results)
    raise ToolError("the subagent ended without a report")  # pragma: no cover


async def _think(model: Any, prompt: list[BaseMessage], ctx: ToolContext,
                 schemas: list[dict] | None, status: Any) -> AIMessage:
    """One model call; a full model server is waited out, as it is for agents."""
    from app.agents.llm import LLMFailure, invoke
    from app.agents.runtime import FULL_BACKOFF, server_full

    for wait in [*FULL_BACKOFF, None]:
        try:
            ai, _ = await invoke(model, prompt, org_id=ctx.org.id, agent_id=ctx.agent.id,
                                 turn_id=ctx.turn_id, purpose="subagent", tools=schemas,
                                 stream=False)
            return ai
        except LLMFailure as e:
            if wait is None or not server_full(str(e)):
                raise ToolError(f"the subagent's model call failed: {e}") from None
            await status(f"the model server is full; trying again in {wait}s")
            await asyncio.sleep(wait)
    raise ToolError("unreachable")  # pragma: no cover
