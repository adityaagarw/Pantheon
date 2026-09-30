"""Task permissions: who may create, assign, edit and close work.

The effective policy for an agent is the org default (``org.settings.task_policy``)
overlaid with the agent's own ``permissions.tasks``. The human and the system
are never restricted.

Policy keys:
- ``create``          bool — may create tasks at all
- ``assign``          "anyone" | "reports" | "self" — whom they may assign work to
                      ("reports" = themselves and agents they manage, transitively)
- ``edit``            "any" | "involved" | "assigned" — which tasks they may change
                      (involved = assignee, reporter, reviewer or watcher)
- ``require_review``  bool — an assignee cannot close their own task when it has a
                      reviewer (or a different reporter); it must go through review
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, Org, Relationship, Task

DEFAULT_TASK_POLICY: dict[str, Any] = {
    "create": True,
    "assign": "anyone",
    "edit": "any",
    "require_review": True,
}
ASSIGN_SCOPES = ("anyone", "reports", "self")
EDIT_SCOPES = ("any", "involved", "assigned")


class PermissionDenied(ValueError):
    pass


def validate_task_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (policy or {}).items():
        if k == "create" or k == "require_review":
            out[k] = bool(v)
        elif k == "assign":
            if v not in ASSIGN_SCOPES:
                raise ValueError(f"assign must be one of {ASSIGN_SCOPES}")
            out[k] = v
        elif k == "edit":
            if v not in EDIT_SCOPES:
                raise ValueError(f"edit must be one of {EDIT_SCOPES}")
            out[k] = v
        else:
            raise ValueError(f"unknown task permission '{k}'")
    return out


async def task_policy(session: AsyncSession, org: Org, agent_id: str) -> dict[str, Any]:
    agent = await session.get(Agent, agent_id)
    policy = dict(DEFAULT_TASK_POLICY)
    policy.update((org.settings or {}).get("task_policy") or {})
    if agent is not None:
        policy.update(((agent.permissions or {}).get("tasks")) or {})
    return policy


async def _reports(session: AsyncSession, org_id: str, manager_id: str) -> set[str]:
    rels = (await session.execute(select(Relationship).where(
        Relationship.org_id == org_id, Relationship.kind == "manages"))).scalars().all()
    children: dict[str, list[str]] = {}
    for r in rels:
        children.setdefault(r.from_id, []).append(r.to_id)
    out, stack = set(), [manager_id]
    while stack:
        cur = stack.pop()
        for c in children.get(cur, []):
            if c not in out:
                out.add(c)
                stack.append(c)
    return out


async def check_create(session: AsyncSession, org: Org, actor_id: str, actor_type: str) -> None:
    if actor_type != "agent":
        return
    if not (await task_policy(session, org, actor_id))["create"]:
        raise PermissionDenied("you are not allowed to create tasks; ask your manager to create "
                               "one, or use request_feature if you need this permission")


async def check_assign(session: AsyncSession, org: Org, actor_id: str, actor_type: str,
                       assignee_id: str | None) -> None:
    if actor_type != "agent" or assignee_id is None or assignee_id == actor_id:
        return
    scope = (await task_policy(session, org, actor_id))["assign"]
    if scope == "anyone":
        return
    if scope == "reports" and assignee_id in await _reports(session, org.id, actor_id):
        return
    who = "yourself" if scope == "self" else "yourself or people you manage"
    raise PermissionDenied(f"you may only assign tasks to {who}; message the right person "
                           "instead of assigning them work")


def _involved(task: Task, agent_id: str) -> bool:
    return agent_id in {task.assignee_id, task.reporter_id, task.reviewer_id,
                        *(task.watchers or [])}


async def check_edit(session: AsyncSession, org: Org, actor_id: str, actor_type: str,
                     task: Task, *, comment_only: bool = False) -> None:
    if actor_type != "agent":
        return
    scope = (await task_policy(session, org, actor_id))["edit"]
    if scope == "any":
        return
    if comment_only and _involved(task, actor_id):
        return
    if scope == "involved" and _involved(task, actor_id):
        return
    if scope == "assigned" and task.assignee_id == actor_id:
        return
    raise PermissionDenied(
        f"you may not change T-{task.number}: it isn't "
        + ("assigned to you" if scope == "assigned" else "a task you're involved in")
        + ". Comment on it or message its assignee instead."
    )


async def check_close(session: AsyncSession, org: Org, actor_id: str, actor_type: str,
                      task: Task, new_status: str) -> None:
    if actor_type != "agent" or new_status != "done" or task.assignee_id != actor_id:
        return
    if not (await task_policy(session, org, actor_id))["require_review"]:
        return
    reviewer = task.reviewer_id or (task.reporter_id if task.reporter_id != actor_id else None)
    if reviewer:
        raise PermissionDenied(
            f"T-{task.number} needs review before it's done: move it to 'review' with your "
            "result; the reviewer will close it"
        )
