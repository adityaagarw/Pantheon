"""Tasks & boards — the organization's shared source of truth for work.

Every change is audited (``task_events``) and notifies exactly the people it
concerns, which is what drives agents to pick up work:

- assigned            → new assignee
- moved to ``review`` → reviewer (or reporter)
- done / cancelled    → reporter + watchers; parent owner when all siblings done
- dependency finished → assignees of tasks whose dependencies are now all done
- comment             → assignee, reporter, watchers (minus the author)
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.core.ids import utcnow
from app.events import bus
from app.models import Agent, Board, Message, Org, Task, TaskEvent
from app.services import comms, permissions
from app.services.comms import USER, Sender

STATUSES = ("backlog", "todo", "in_progress", "blocked", "review", "done", "cancelled")
PRIORITIES = ("low", "normal", "high", "urgent")
DEFAULT_COLUMNS = [
    {"key": "backlog", "name": "Backlog"},
    {"key": "todo", "name": "To do"},
    {"key": "in_progress", "name": "In progress"},
    {"key": "blocked", "name": "Blocked"},
    {"key": "review", "name": "Review"},
    {"key": "done", "name": "Done"},
]
CLOSED = ("done", "cancelled")


class TaskError(ValueError):
    pass


def task_ref(t: Task) -> str:
    return f"T-{t.number}"


def task_to_dict(t: Task) -> dict[str, Any]:
    return {
        "id": t.id, "ref": task_ref(t), "orgId": t.org_id, "boardId": t.board_id,
        "number": t.number, "title": t.title, "description": t.description,
        "acceptance": t.acceptance, "status": t.status, "priority": t.priority,
        "assigneeId": t.assignee_id, "reporterId": t.reporter_id, "reviewerId": t.reviewer_id,
        "parentId": t.parent_id, "dependsOn": t.depends_on or [], "watchers": t.watchers or [],
        "labels": t.labels or [], "result": t.result, "position": t.position,
        "dueAt": t.due_at.isoformat() if t.due_at else None,
        "createdAt": t.created_at.isoformat() if t.created_at else None,
        "updatedAt": t.updated_at.isoformat() if t.updated_at else None,
        "closedAt": t.closed_at.isoformat() if t.closed_at else None,
    }


def board_to_dict(b: Board) -> dict[str, Any]:
    return {"id": b.id, "orgId": b.org_id, "name": b.name, "description": b.description,
            "columns": b.columns}


async def ensure_default_board(session: AsyncSession, org_id: str) -> Board:
    board = (
        await session.execute(
            select(Board).where(Board.org_id == org_id).order_by(Board.created_at).limit(1)
        )
    ).scalar_one_or_none()
    if board is None:
        board = Board(org_id=org_id, name="Main", columns=list(DEFAULT_COLUMNS))
        session.add(board)
        await session.flush()
    return board


async def find_task(session: AsyncSession, org_id: str, ref: str) -> Task | None:
    ref = (ref or "").strip()
    m = re.fullmatch(r"(?i)t-?(\d+)", ref) or re.fullmatch(r"#?(\d+)", ref)
    if m:
        return (
            await session.execute(
                select(Task).where(Task.org_id == org_id, Task.number == int(m.group(1)))
            )
        ).scalar_one_or_none()
    t = await session.get(Task, ref)
    return t if t is not None and t.org_id == org_id else None


async def _resolve_person(session: AsyncSession, org_id: str, ref: str | None) -> str | None:
    if ref is None or str(ref).strip() == "":
        return None
    if str(ref).strip().lower() in (USER, "me_user", "the user", "human"):
        return USER
    agent = await comms.resolve_agent(session, org_id, str(ref))
    if agent is None:
        raise TaskError(f"no colleague named '{ref}'")
    return agent.id


async def _names(session: AsyncSession, org_id: str) -> dict[str, str]:
    rows = (await session.execute(select(Agent.id, Agent.name).where(Agent.org_id == org_id))).all()
    out = {r[0]: r[1] for r in rows}
    out[USER] = "the user"
    out[comms.SYSTEM] = "system"
    return out


async def create_task(
    org_id: str,
    actor: Sender,
    *,
    title: str,
    description: str = "",
    acceptance: str = "",
    assignee: str | None = None,
    reviewer: str | None = None,
    priority: str = "normal",
    status: str | None = None,
    board_id: str | None = None,
    parent: str | None = None,
    depends_on: list[str] | None = None,
    labels: list[str] | None = None,
) -> Task:
    title = (title or "").strip()
    if not title:
        raise TaskError("title is required")
    if priority not in PRIORITIES:
        raise TaskError(f"priority must be one of {PRIORITIES}")
    if status is not None and status not in STATUSES:
        raise TaskError(f"status must be one of {STATUSES}")
    async with SessionLocal() as session:
        org = (
            await session.execute(select(Org).where(Org.id == org_id).with_for_update())
        ).scalar_one_or_none()
        if org is None:
            raise TaskError("org not found")
        try:
            await permissions.check_create(session, org, actor.id, actor.type)
        except permissions.PermissionDenied as e:
            raise TaskError(str(e)) from None
        if board_id:
            board = await session.get(Board, board_id)
            if board is None or board.org_id != org_id:
                raise TaskError(f"board '{board_id}' not found")
        else:
            board = await ensure_default_board(session, org_id)
        assignee_id = await _resolve_person(session, org_id, assignee)
        reviewer_id = await _resolve_person(session, org_id, reviewer)
        try:
            await permissions.check_assign(session, org, actor.id, actor.type, assignee_id)
        except permissions.PermissionDenied as e:
            raise TaskError(str(e)) from None
        parent_task = await find_task(session, org_id, parent) if parent else None
        if parent and parent_task is None:
            raise TaskError(f"parent task '{parent}' not found")
        dep_ids: list[str] = []
        for d in depends_on or []:
            dt = await find_task(session, org_id, d)
            if dt is None:
                raise TaskError(f"dependency '{d}' not found")
            dep_ids.append(dt.id)
        org.task_counter = (org.task_counter or 0) + 1
        task = Task(
            org_id=org_id, board_id=board.id, number=org.task_counter, title=title[:300],
            description=description, acceptance=acceptance,
            status=status or ("todo" if assignee_id else "backlog"), priority=priority,
            assignee_id=assignee_id, reporter_id=actor.id, reviewer_id=reviewer_id,
            parent_id=parent_task.id if parent_task else None, depends_on=dep_ids,
            watchers=[], labels=list(labels or []), position=float(org.task_counter),
        )
        session.add(task)
        await session.flush()
        session.add(TaskEvent(task_id=task.id, actor_id=actor.id, kind="created",
                              data={"title": task.title, "assignee": assignee_id}))
        await session.commit()
        names = await _names(session, org_id)
        open_deps = []
        for dep_id in dep_ids:
            dep = await session.get(Task, dep_id)
            if dep is not None and dep.status not in CLOSED:
                open_deps.append(task_ref(dep))
    await bus.publish("task.created", task_to_dict(task), org_id=org_id,
                      agent_id=actor.id if actor.type == "agent" else None)
    if assignee_id and assignee_id != actor.id:
        wait = f"\nIt is waiting on: {', '.join(open_deps)} — start when those are done." \
            if open_deps else ""
        await comms.notify(
            org_id, [assignee_id],
            f"{names.get(actor.id, actor.id)} assigned you {task_ref(task)}: {task.title}\n"
            f"Priority: {task.priority}\n{task.description}"
            + (f"\nAcceptance criteria: {task.acceptance}" if task.acceptance else "") + wait,
            depth=actor.depth, task_id=task.id,
        )
    return task


async def update_task(
    org_id: str,
    actor: Sender,
    ref: str,
    *,
    title: str | None = None,
    description: str | None = None,
    acceptance: str | None = None,
    status: str | None = None,
    assignee: str | None = None,
    unassign: bool = False,
    reviewer: str | None = None,
    priority: str | None = None,
    result: str | None = None,
    board_id: str | None = None,
    position: float | None = None,
    depends_on: list[str] | None = None,
    labels: list[str] | None = None,
    comment: str | None = None,
    watch: bool | None = None,
) -> Task:
    notices: list[tuple[list[str], str]] = []
    async with SessionLocal() as session:
        task = await find_task(session, org_id, ref)
        if task is None:
            raise TaskError(f"task '{ref}' not found")
        task = (
            await session.execute(select(Task).where(Task.id == task.id).with_for_update())
        ).scalar_one()
        org = await session.get(Org, org_id)
        assert org is not None
        changes_fields = any(v is not None for v in (
            title, description, acceptance, status, assignee, reviewer, priority, result,
            board_id, position, depends_on, labels)) or unassign
        try:
            await permissions.check_edit(session, org, actor.id, actor.type, task,
                                         comment_only=not changes_fields)
            if status is not None:
                await permissions.check_close(session, org, actor.id, actor.type, task, status)
            if assignee is not None:
                await permissions.check_assign(
                    session, org, actor.id, actor.type,
                    await _resolve_person(session, org_id, assignee))
        except permissions.PermissionDenied as e:
            raise TaskError(str(e)) from None
        names = await _names(session, org_id)
        who = names.get(actor.id, actor.id)
        ref_s = task_ref(task)
        events: list[TaskEvent] = []

        if title is not None and title.strip() and title != task.title:
            task.title = title.strip()[:300]
            events.append(TaskEvent(task_id=task.id, actor_id=actor.id, kind="edit",
                                    data={"field": "title"}))
        if description is not None and description != task.description:
            task.description = description
            events.append(TaskEvent(task_id=task.id, actor_id=actor.id, kind="edit",
                                    data={"field": "description"}))
        if acceptance is not None:
            task.acceptance = acceptance
        if priority is not None:
            if priority not in PRIORITIES:
                raise TaskError(f"priority must be one of {PRIORITIES}")
            task.priority = priority
        if labels is not None:
            task.labels = list(labels)
        if position is not None:
            task.position = float(position)
        if board_id is not None and board_id != task.board_id:
            board = await session.get(Board, board_id)
            if board is None or board.org_id != org_id:
                raise TaskError(f"board '{board_id}' not found")
            task.board_id = board_id
        if depends_on is not None:
            dep_ids = []
            for d in depends_on:
                dt = await find_task(session, org_id, d)
                if dt is None:
                    raise TaskError(f"dependency '{d}' not found")
                if dt.id == task.id:
                    raise TaskError("a task cannot depend on itself")
                dep_ids.append(dt.id)
            task.depends_on = dep_ids
        if watch is not None:
            watchers = [w for w in (task.watchers or []) if w != actor.id]
            if watch:
                watchers.append(actor.id)
            task.watchers = watchers
        if reviewer is not None:
            task.reviewer_id = await _resolve_person(session, org_id, reviewer)
        if result is not None:
            task.result = result

        if unassign or assignee is not None:
            new_assignee = None if unassign else await _resolve_person(session, org_id, assignee)
            if new_assignee != task.assignee_id:
                events.append(TaskEvent(task_id=task.id, actor_id=actor.id, kind="assignee",
                                        data={"from": task.assignee_id, "to": new_assignee}))
                task.assignee_id = new_assignee
                if task.status == "backlog" and new_assignee:
                    task.status = "todo"
                if new_assignee and new_assignee != actor.id:
                    notices.append(([new_assignee],
                                    f"{who} assigned you {ref_s}: {task.title}\n{task.description}"
                                    + (f"\nAcceptance criteria: {task.acceptance}"
                                       if task.acceptance else "")))

        became_closed = False
        if status is not None and status != task.status:
            if status not in STATUSES:
                raise TaskError(f"status must be one of {STATUSES}")
            old = task.status
            task.status = status
            events.append(TaskEvent(task_id=task.id, actor_id=actor.id, kind="status",
                                    data={"from": old, "to": status}))
            if status in CLOSED:
                task.closed_at = utcnow()
                became_closed = True
            else:
                task.closed_at = None
            summary = f"{who} moved {ref_s} ({task.title}) from {old} to {status}."
            if task.result and status in ("review", "done"):
                summary += f"\nResult: {task.result}"
            if status == "review":
                target = task.reviewer_id or task.reporter_id
                notices.append(([target], summary + "\nPlease review it: move it to done if it "
                                "meets the acceptance criteria, or back to in_progress with a "
                                "comment explaining what is missing."))
            elif status in CLOSED:
                notices.append(([task.reporter_id, *(task.watchers or [])], summary))
            elif old == "review" and status == "in_progress" and task.assignee_id:
                notices.append(([task.assignee_id], summary + " Review feedback is in the "
                                "task comments."))
            elif task.assignee_id and actor.id != task.assignee_id:
                notices.append(([task.assignee_id], summary))

        comment_msg: Message | None = None
        if comment:
            events.append(TaskEvent(task_id=task.id, actor_id=actor.id, kind="comment",
                                    data={"text": comment[:4000]}))
        for ev in events:
            session.add(ev)
        await session.commit()

        followups: list[tuple[list[str], str, str | None]] = []
        if became_closed and task.status == "done":
            # Dependents whose dependencies are now all closed.
            dependents = (
                await session.execute(select(Task).where(Task.org_id == org_id,
                                                         Task.status.not_in(CLOSED)))
            ).scalars().all()
            for dep in dependents:
                if task.id not in (dep.depends_on or []):
                    continue
                others = [await session.get(Task, d) for d in dep.depends_on]
                if all(o is None or o.status in CLOSED for o in others) and dep.assignee_id:
                    followups.append(([dep.assignee_id],
                                      f"All dependencies of {task_ref(dep)} ({dep.title}) are done "
                                      f"(last: {ref_s}). You can start it now.", dep.id))
            # Parent rollup.
            if task.parent_id:
                parent = await session.get(Task, task.parent_id)
                if parent is not None and parent.status not in CLOSED:
                    siblings = (
                        await session.execute(select(Task).where(Task.parent_id == parent.id))
                    ).scalars().all()
                    if all(s.status in CLOSED for s in siblings):
                        owner = parent.assignee_id or parent.reporter_id
                        lines = "\n".join(
                            f"- {task_ref(s)} {s.title} [{s.status}]"
                            + (f": {s.result[:300]}" if s.result else "") for s in siblings
                        )
                        followups.append(([owner], f"All subtasks of {task_ref(parent)} "
                                          f"({parent.title}) are closed:\n{lines}", parent.id))

    if comment:
        comment_msg = await comms.post(
            org_id, actor, comment, to_agents=[
                p for p in {task.assignee_id, task.reporter_id, task.reviewer_id,
                            *(task.watchers or [])} if p and p != actor.id
            ], kind="task_comment", task_id=task.id,
            meta={"taskRef": ref_s, "title": task.title},
        )
    await bus.publish("task.updated", task_to_dict(task), org_id=org_id,
                      agent_id=actor.id if actor.type == "agent" else None)
    for targets, text in notices:
        await comms.notify(org_id, [t for t in targets if t and t != actor.id], text,
                           depth=actor.depth, task_id=task.id)
    for targets, text, tid in followups:
        await comms.notify(org_id, [t for t in targets if t and t != actor.id], text,
                           depth=actor.depth, task_id=tid)
    _ = comment_msg
    return task


async def list_tasks(
    org_id: str,
    *,
    assignee: str | None = None,
    status: list[str] | None = None,
    board_id: str | None = None,
    parent_id: str | None = None,
    include_closed: bool = True,
    limit: int = 500,
) -> list[Task]:
    async with SessionLocal() as session:
        stmt = select(Task).where(Task.org_id == org_id)
        if assignee:
            stmt = stmt.where(Task.assignee_id == assignee)
        if status:
            stmt = stmt.where(Task.status.in_(status))
        elif not include_closed:
            stmt = stmt.where(Task.status.not_in(CLOSED))
        if board_id:
            stmt = stmt.where(Task.board_id == board_id)
        if parent_id:
            stmt = stmt.where(Task.parent_id == parent_id)
        rows = (await session.execute(stmt.order_by(Task.position).limit(limit))).scalars().all()
    return list(rows)


async def task_timeline(org_id: str, task_id: str) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.id)
            )
        ).scalars().all()
    return [
        {"id": r.id, "actorId": r.actor_id, "kind": r.kind, "data": r.data,
         "createdAt": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]


async def reassign_from(org_id: str, agent_id: str) -> None:
    """Unassign open tasks from a deleted agent (they return to the backlog)."""
    async with SessionLocal() as session:
        await session.execute(
            update(Task)
            .where(Task.org_id == org_id, Task.assignee_id == agent_id, Task.status.not_in(CLOSED))
            .values(assignee_id=None, status="backlog")
        )
        await session.commit()
