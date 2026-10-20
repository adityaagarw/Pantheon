"""Live test against a real OpenAI-compatible model (skipped unless configured).

    PANTHEON_LIVE_BASE_URL=http://localhost:8000/v1 PANTHEON_LIVE_MODEL=your-model \
        uv run pytest tests/test_live_llm.py -s
"""

from __future__ import annotations

import asyncio
import os

import pytest

from app.models import Task, ToolCall, Turn
from tests.conftest import wait_for
from tests.helpers import dm, make_agent, make_org, rows

BASE = os.environ.get("PANTHEON_LIVE_BASE_URL")
MODEL = os.environ.get("PANTHEON_LIVE_MODEL")
pytestmark = pytest.mark.skipif(not BASE or not MODEL, reason="live LLM not configured")


async def test_real_model_delegates_and_delivers(app_client, workspace_root):
    r = await app_client.post("/api/v1/providers", json={
        "name": "live", "type": "openai_compatible", "baseUrl": BASE,
        "models": [{"id": MODEL, "context_window": 64000}], "defaultModel": MODEL,
        "isDefault": True})
    assert r.status_code == 201
    pid = r.json()["id"]
    probe = await app_client.post(f"/api/v1/providers/{pid}/test", json={})
    assert probe.json()["ok"], probe.json()

    org = await make_org(app_client, "Live Co")
    lead = await make_agent(app_client, org["id"], "Maya", role="Engineering lead",
                            persona="You lead a small engineering team. You plan work as tasks "
                                    "on the board and delegate implementation to your "
                                    "engineers. You do not write code yourself.")
    dev = await make_agent(app_client, org["id"], "Theo", role="Software engineer",
                           persona="You implement tasks assigned to you, carefully and "
                                   "completely, then move them to review with a result.",
                           manager="Maya")
    await dm(app_client, org["id"], "Maya",
             "Please get a Python file `fizzbuzz.py` written that prints FizzBuzz for 1..30. "
             "Delegate it to Theo via a task and review it when he's done.")

    async def approver():  # act as the user: approve every tool request
        from app.models import Approval

        while True:
            for a in await rows(Approval, Approval.status == "pending"):
                await app_client.post(f"/api/v1/approvals/{a.id}", json={"approved": True})
            await asyncio.sleep(0.5)

    task = asyncio.create_task(approver())
    try:
        await wait_for(lambda: _file(workspace_root, "fizzbuzz.py"), timeout=420, interval=1,
                       msg="fizzbuzz.py written")
        await wait_for(lambda: _task_done(org["id"]), timeout=600, interval=1, msg="task done")
    finally:
        task.cancel()
        await _dump(org["id"], {lead["id"]: "Maya", dev["id"]: "Theo"})
    turns = await rows(Turn, Turn.org_id == org["id"])
    failed = [t for t in turns if t.status == "failed"]
    tools = await rows(ToolCall, ToolCall.org_id == org["id"])
    print("\nturns:", [(t.agent_id == lead["id"] and "Maya" or "Theo", t.status, t.steps)
                       for t in turns])
    print("tools:", [(t.name, t.status) for t in tools])
    assert not failed, [t.error for t in failed]
    assert dev["id"] in {t.agent_id for t in tools if t.name == "write_file"}


async def _dump(org_id, names):
    from app.models import Message

    events = []
    for t in await rows(Turn, Turn.org_id == org_id):
        events.append((t.started_at, f"TURN {names.get(t.agent_id)} {t.status} steps={t.steps} "
                                     f"summary={t.summary[:300]!r} err={t.error}"))
    for c in await rows(ToolCall, ToolCall.org_id == org_id):
        events.append((c.created_at, f"  TOOL {names.get(c.agent_id)} {c.name}({c.args}) -> "
                                     f"{c.status}: {c.result[:200]!r}"))
    for m in await rows(Message, Message.org_id == org_id):
        events.append((m.created_at, f"  MSG {names.get(m.sender_id, m.sender_id)} [{m.kind}] "
                                     f"{m.content[:200]!r}"))
    for t in await rows(Task, Task.org_id == org_id):
        events.append((t.updated_at, f"TASK T-{t.number} {t.status} {t.title}"))
    print("\n=== transcript ===")
    for _, line in sorted(events, key=lambda e: e[0]):
        print(line)


def _file(root, name):
    return list(root.rglob(name))


async def _task_done(org_id):
    ts = await rows(Task, Task.org_id == org_id)
    return ts and any(t.status == "done" for t in ts)
