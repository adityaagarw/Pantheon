"""Short notes the engine adds to a long turn: the clock, and doing more per step.

Agents have no sense of time: a timed run's agents polished for half an hour past the
point of done, and a turn of hundreds of steps never sees a new system prompt (it is built
once per turn, so the server can reuse its cache). And a model that makes one tool call per
step pays a full round trip for each: in one run 97% of steps had a single call.

So, before a model call, a note may be appended to the conversation (never edited into
the system prompt, which would break the cache): how much of a goal's time is used, when
the goal has a deadline; a reminder to batch independent calls after a run of single
ones; and a reminder to keep a checklist on a long job that has none. Each is rate-limited.

Two more come from harness2 (Google Research, 2026), whose improved harnesses added them as
hooks in the tool loop: a note when a long stretch of a turn on assigned work has written
nothing (a warning after N tool calls without an artifact was among the edits that took a
task whose team researched until the deadline from 72 to 100), and a note when the same
call fails the same way again and again (its edits cut tool errors by 20-37%).
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from sqlalchemy import or_, select

from app.core.db import SessionLocal
from app.core.ids import utcnow
from app.models import Agent, Task

TIME_EVERY = 10 * 60  # seconds between time notes
LAST_STRETCH = 10  # minutes left at which the note says to wrap up
SINGLES = 4  # steps in a row with one tool call each before a batching note
BATCH_EVERY = 15  # steps between batching notes
TODO_AFTER = 12  # steps into a turn without a checklist before a note
NO_OUTPUT_AFTER = 30  # steps into a turn on assigned work without writing anything
REPEATS = 3  # the same tool failing with the same error this many times in a row
# Calls that make or change the work, or hand it out (a command may too, but it can't be
# told apart).
WRITES = frozenset({"write_file", "edit_file", "sheet_write", "md_write", "stage_show",
                    "notebook_edit", "git_commit", "finish_step", "update_task", "create_task",
                    "plan_flow", "steer", "merge_work"})


def _wrote(messages: list[BaseMessage], steps: int) -> bool:
    """Whether this turn's last `steps` steps called a tool that writes."""
    seen = 0
    for m in reversed(messages):
        if not isinstance(m, AIMessage):
            continue
        seen += 1
        if any(tc["name"] in WRITES or tc["name"].startswith("subagent:") or
               tc["name"] == "spawn_subagent" for tc in m.tool_calls or []):
            return True
        if seen >= steps:
            break
    return False


def repeated_error(messages: list[BaseMessage]) -> tuple[str, str] | None:
    """(tool, error) when the newest REPEATS results of one tool failed the same way."""
    calls: dict[str, str] = {}
    for m in messages:
        if isinstance(m, AIMessage):
            for tc in m.tool_calls or []:
                calls[tc.get("id") or ""] = tc["name"]
    failures: list[tuple[str, str]] = []
    for m in reversed(messages):
        if not isinstance(m, ToolMessage):
            continue
        text = str(m.content if isinstance(m.content, str) else m.content)
        if not text.startswith("ERROR"):
            break
        failures.append((calls.get(m.tool_call_id, "?"), text.splitlines()[0][:200]))
        if len(failures) >= REPEATS:
            break
    if len(failures) >= REPEATS and len(set(failures)) == 1:
        return failures[0]
    return None


async def _has_open_work(agent: Agent) -> bool:
    from app.services.tasks import CLOSED

    async with SessionLocal() as session:
        return bool(await session.scalar(select(Task.id).where(
            Task.org_id == agent.org_id, Task.assignee_id == agent.id,
            Task.status.not_in(CLOSED)).limit(1)))


async def deadline(agent: Agent) -> tuple[Task, datetime] | None:
    """The open goal with a deadline this agent works toward: its own, or the org's."""
    from app.services.tasks import CLOSED

    async with SessionLocal() as session:
        task = (await session.execute(select(Task).where(
            Task.org_id == agent.org_id, Task.status.not_in(CLOSED), Task.due_at.is_not(None),
            Task.parent_id.is_(None),
            or_(Task.assignee_id == agent.id, Task.reporter_id == "user")).order_by(
            Task.due_at).limit(1))).scalars().first()
    return (task, task.due_at) if task and task.due_at else None


def _singles(messages: list[BaseMessage]) -> int:
    """Steps in a row, most recent first, that made exactly one tool call."""
    n = 0
    for m in reversed(messages):
        if not isinstance(m, AIMessage):
            continue
        if len(m.tool_calls or []) != 1:
            break
        n += 1
    return n


async def notes(agent: Agent, messages: list[BaseMessage], steps: int,
                noted: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """(notes to add before this model call, the updated record of when each was last given)."""
    from app.services.tasks import task_ref

    out: list[str] = []
    noted = dict(noted or {})
    now = time.time()
    if (due := await deadline(agent)) is not None:
        task, at = due
        left = (at - utcnow()).total_seconds() / 60
        total = max((at - task.created_at).total_seconds() / 60, 1)
        late = left <= LAST_STRETCH
        if now - float(noted.get("time") or 0) >= TIME_EVERY or (late and not noted.get("late")):
            used = max(total - left, 0)
            text = (f"Time: {used:.0f} of {total:.0f} minutes used on {task_ref(task)} "
                    f"({max(left, 0):.0f} left).")
            if late:
                text += (" Finish what is in progress, check it, and hand in; list what is left "
                         "as known issues instead of starting it.")
                noted["late"] = True
            out.append(text)
            noted["time"] = now
    if _singles(messages) >= SINGLES and steps - int(noted.get("batch", -BATCH_EVERY)) >= BATCH_EVERY:
        out.append(f"Your last {SINGLES} steps each made one tool call. Calls that don't depend "
                   "on each other (reads, searches, checks, writes to different files) can go "
                   "together in one step: they run at the same time and save a round trip each.")
        noted["batch"] = steps
    if steps >= TODO_AFTER and not agent.todos and not noted.get("todo"):
        out.append("This is a long job: write your checklist with todo, so you can see what is "
                   "done and when to stop.")
        noted["todo"] = True
    if steps >= NO_OUTPUT_AFTER and not noted.get("output") and not agent.is_supervisor \
            and not _wrote(messages, steps) and await _has_open_work(agent):
        out.append(f"{steps} steps into this turn you haven't written any of the work yet. "
                   "Write a first complete version of the deliverable now from what you know, "
                   "then improve it: reading more first rarely changes it as much as you expect.")
        noted["output"] = True
    if (rep := repeated_error(messages)) and noted.get("repeat") != list(rep):
        name, error = rep
        out.append(f"{name} failed the same way {REPEATS} times in a row ({error}). Don't send "
                   "it again unchanged: read the error, compare your arguments with the tool's "
                   "parameters, and change the call or take another way.")
        noted["repeat"] = list(rep)
    return out, noted
