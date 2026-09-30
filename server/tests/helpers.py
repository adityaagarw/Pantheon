"""Scripting helpers for the mock model."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from sqlalchemy import select

from app.core.db import SessionLocal
from app.llm import mock

_ids = itertools.count(1)


def call(tool_name: str, /, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": tool_name, "args": args,
                                              "id": f"tc_{next(_ids)}"}])


def calls(*pairs: tuple[str, dict[str, Any]]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": n, "args": a, "id": f"tc_{next(_ids)}"}
                                             for n, a in pairs])


def say(text: str) -> AIMessage:
    return AIMessage(content=text)


Step = AIMessage | Callable[[list[BaseMessage], list[dict]], AIMessage]


def script(agent_id: str, *steps: Step, then: Step | None = None) -> list[list[BaseMessage]]:
    """Queue responses for an agent. Returns the list of prompts it received."""
    queue = list(steps)
    seen: list[list[BaseMessage]] = []

    def fn(messages: list[BaseMessage], tools: list[dict]) -> AIMessage:
        seen.append(messages)
        step = queue.pop(0) if queue else (then or say("done"))
        if callable(step) and not isinstance(step, AIMessage):
            step = step(messages, tools)
        # Fresh ids each use so repeated steps don't collide.
        if step.tool_calls:
            step = AIMessage(content=step.content, tool_calls=[
                {**tc, "id": f"tc_{next(_ids)}"} for tc in step.tool_calls])
        return step

    mock.set_script(agent_id, fn)
    return seen


def last_human(messages: list[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return str(m.content)
    return ""


def tool_results(messages: list[BaseMessage]) -> list[str]:
    return [str(m.content) for m in messages if isinstance(m, ToolMessage)]


async def rows(model: Any, *where: Any) -> list[Any]:
    async with SessionLocal() as session:
        return list((await session.execute(select(model).where(*where))).scalars().all())


async def make_org(client, name: str = "Acme", **extra: Any) -> dict:
    r = await client.post("/api/v1/orgs", json={"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def make_agent(client, org_id: str, name: str, **extra: Any) -> dict:
    body = {"name": name, "role": extra.pop("role", "Engineer"), **extra}
    r = await client.post(f"/api/v1/orgs/{org_id}/agents", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def dm(client, org_id: str, to: str, content: str) -> dict:
    r = await client.post(f"/api/v1/orgs/{org_id}/messages", json={"to": to, "content": content})
    assert r.status_code == 201, r.text
    return r.json()
