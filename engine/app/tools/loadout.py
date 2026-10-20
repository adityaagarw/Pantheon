"""A lean tool loadout: the tools nearly every turn needs go to the model on every call; the
rest are listed by name and loaded when the agent needs them.

Every tool schema is sent with every model call. With seventy of them most of the prompt was
tools the agent never used (a timed run: 72 offered, a dozen used), which costs prefill time
and attention. The core set is offered whole; the others appear as one line each in the system
prompt, and load_tools (or simply calling one) brings a tool in for the rest of the turn.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

from app.tools.base import ToolContext, ToolError, arr, obj, tool

# Offered whole on every call, when the agent has them: pi's four (read, write, edit, bash),
# the read tools the prompt tells agents to use instead of the shell (deferred, they cost a
# load first, so agents used the shell), subagents, the checklist, and the loader.
CORE = frozenset({
    "read_file", "write_file", "edit_file", "run_command", "search_files", "list_dir",
    "spawn_subagent", "todo", "load_tools",
})
# Also offered whole to someone with colleagues: a team member coordinates on most turns.
TEAM_CORE = frozenset({"send_message", "create_task", "update_task", "list_tasks", "get_task"})
LEAN_ABOVE = 20  # an agent with this many tools or fewer gets them all
# Tools an agent used in its last this-many calls stay offered from turn to turn. A timed run
# loaded tools 51 times in 553 calls (stage_check five times in one turn): what was loaded
# went when the turn's history was compacted, and the list of tools to load still named it.
STICKY_CALLS = 120


async def recent_tools(agent_id: str) -> list[str]:
    """The tools this agent called most recently (kept offered: loadout.split)."""
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import ToolCall

    async with SessionLocal() as session:
        names = (await session.execute(select(ToolCall.name).where(
            ToolCall.agent_id == agent_id).order_by(ToolCall.created_at.desc()).limit(
            STICKY_CALLS))).scalars().all()
    return sorted({n for n in names if not n.startswith("subagent:")})


def lean(org: Any) -> bool:
    return ((org.settings or {}).get("tools") or "lean") != "all"


def loaded_in(messages: list[BaseMessage]) -> set[str]:
    """Tools this thread has already brought in: ones it called, and ones it loaded."""
    names: set[str] = set()
    for m in messages:
        if isinstance(m, AIMessage):
            for tc in m.tool_calls or []:
                names.add(tc["name"])
                if tc["name"] == "load_tools":
                    names.update(str(n) for n in (tc.get("args") or {}).get("names") or [])
    return names


def split(org: Any, tools: dict[str, Any], messages: list[BaseMessage],
          sticky: list[str] | tuple = ()) -> tuple[list[str], list[str]]:
    """(offered, deferred) tool names for the next model call."""
    if not lean(org) or len(tools) <= LEAN_ABOVE:
        return sorted(t for t in tools if t != "load_tools"), []
    keep = (CORE | loaded_in(messages) | set(sticky)
            | {n for n, t in tools.items() if getattr(t, "core", False)})
    offered = sorted(t for t in tools if t in keep)
    deferred = sorted(t for t in tools if t not in keep)
    return offered, deferred


def first_sentence(text: str, cap: int = 110) -> str:
    s = " ".join(text.split())
    for end in (". ", "; ", ": "):
        if end in s[:cap]:
            return s[:s.index(end, 0, cap)]
    return s[:cap] + ("…" if len(s) > cap else "")


def catalogue(tools: dict[str, Any], deferred: list[str]) -> str:
    lines = [f"- {n}: {first_sentence(tools[n].description)}" for n in deferred]
    return ("# More tools\nThese are yours too. Load the ones you need with load_tools (several "
            "at once), or call one directly. A tool you have loaded or called is in your tool "
            "list from then on: call it, never load it again.\n"
            + "\n".join(lines))


@tool("load_tools", "Load tools from your 'More tools' list so you can call them: you get "
      "each one's description and parameters.", obj({"names": arr({"type": "string"})},
                                                    ["names"]), category="general")
async def load_tools(args: dict, ctx: ToolContext) -> str:
    from app.tools.resolve import resolve_tools

    mine = (await resolve_tools(ctx.org, ctx.agent)).tools
    names = [str(n) for n in args.get("names") or []]
    missing = [n for n in names if n not in mine]
    if missing and len(missing) == len(names):
        raise ToolError(f"you have no tool named {', '.join(missing)}")
    out = [f"## {n}\n{mine[n].description}\nParameters: {mine[n].parameters}"
           for n in names if n in mine]
    if missing:
        out.append(f"(No tool named {', '.join(missing)}.)")
    return "Loaded; you can call these now.\n\n" + "\n\n".join(out)
