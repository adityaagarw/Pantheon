"""Deep connections to Claude Code, Codex, agy and ACP agents (Hermes…): live tools and
thinking, cost, approvals in the user's Inbox, steering mid-turn, sessions that carry on,
and usage limits that park the work instead of failing it. The programs are stand-ins
speaking each protocol (tests/fake_agents.py)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.core.db import SessionLocal
from app.models import Agent, Approval, Delivery, Message, ToolCall, Turn
from tests import fake_agents
from tests.conftest import wait_for
from tests.helpers import dm, make_agent, make_org, rows


def _script(tmp_path, name: str, body: str) -> str:
    path = tmp_path / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    return str(path)


def _starts(script: str) -> list:
    try:
        with open(script + ".log", encoding="utf-8") as f:
            return [json.loads(line) for line in f]
    except FileNotFoundError:
        return []


async def _turns(agent_id: str, status: str = "completed") -> list[Turn]:
    return [t for t in await rows(Turn, Turn.agent_id == agent_id) if t.status == status]


async def _reply(agent_id: str) -> str:
    msgs = sorted(await rows(Message, Message.sender_id == agent_id), key=lambda m: m.created_at)
    return msgs[-1].content if msgs else ""


async def _agent(agent_id: str) -> Agent:
    return (await rows(Agent, Agent.id == agent_id))[0]


async def _pending_approval(agent_id: str) -> list[Approval]:
    return await rows(Approval, Approval.agent_id == agent_id, Approval.status == "pending")


async def test_claude_code_connected_both_ways(app_client, tmp_path):
    fake = _script(tmp_path, "claude", fake_agents.CLAUDE)
    org = await make_org(app_client)
    cc = await make_agent(app_client, org["id"], "Cody", kind="cli", runner={
        "preset": "claude-code", "command": fake, "autonomy": "ask", "model": "sonnet",
        "effort": "high", "max_turns": "40"})
    assert cc["runner"]["max_turns"] == 40

    # A turn: its reply, its cost and tokens on the turn, its session remembered.
    await dm(app_client, org["id"], "Cody", "hello there")
    await wait_for(lambda: _turns(cc["id"]), timeout=60, msg="first turn")
    reply = await _reply(cc["id"])
    assert reply.startswith("reply 1: hello there") and "knows-pantheon" in reply
    turn = (await _turns(cc["id"]))[0]
    assert turn.cost_usd == 0.01 and turn.input_tokens == 15 and turn.output_tokens == 7
    argv = _starts(fake)[0]
    assert "--strict-mcp-config" in argv and argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--effort") + 1] == "high" and argv[argv.index("--max-turns") + 1] == "40"
    assert argv[argv.index("--permission-prompt-tool") + 1] == "stdio"
    agent = await _agent(cc["id"])
    assert agent.runner["session"] == "sess-1" and agent.runner["session_dir"]

    # Something risky: it waits in the Inbox as an approval; approved, it runs.
    await dm(app_client, org["id"], "Cody", "RISKY clean up")
    await wait_for(lambda: _pending_approval(cc["id"]), timeout=30, msg="approval asked")
    apv = (await _pending_approval(cc["id"]))[0]
    assert apv.tool == "Bash" and apv.args == {"command": "rm -rf build"}
    assert (await _agent(cc["id"])).runtime_status == "awaiting_approval"
    r = await app_client.post(f"/api/v1/approvals/{apv.id}", json={"approved": True})
    assert r.status_code == 200, r.text
    await wait_for(lambda: _turns(cc["id"]), timeout=30, msg="risky turn")
    await wait_for(lambda: _count(_turns(cc["id"]), 2), timeout=30, msg="risky turn done")
    assert "ran it" in await _reply(cc["id"])
    # The second turn carried on the first's session.
    second = _starts(fake)[1]
    assert second[second.index("--resume") + 1] == "sess-1"
    bash = [c for c in await rows(ToolCall, ToolCall.agent_id == cc["id"]) if c.name == "Bash"]
    assert bash and bash[0].status == "ok" and "removed build" in bash[0].result

    # Denied: it's told so, and carries on.
    await dm(app_client, org["id"], "Cody", "RISKY again")
    await wait_for(lambda: _pending_approval(cc["id"]), timeout=30, msg="second approval")
    apv = (await _pending_approval(cc["id"]))[0]
    await app_client.post(f"/api/v1/approvals/{apv.id}", json={"approved": False, "note": "not now"})
    await wait_for(lambda: _count(_turns(cc["id"]), 3), timeout=30, msg="denied turn done")
    assert "was refused" in await _reply(cc["id"])

    # Steering mid-turn reaches the running turn (no new turn for it).
    started = len(_starts(fake))
    await dm(app_client, org["id"], "Cody", "SLOW job")

    async def running() -> bool:  # its program has started on the job
        return len(_starts(fake)) > started

    await wait_for(running, timeout=30, msg="working on the slow job")
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/messages",
                              json={"to": "Cody", "content": "use the fast path", "steer": True})
    assert r.status_code == 201
    await wait_for(lambda: _count(_turns(cc["id"]), 4), timeout=40, msg="steered turn done")
    assert "also: use the fast path" in await _reply(cc["id"])

    # Editing its settings keeps its session; when the session is gone (cleared on the
    # other side), it starts afresh, with what it did lately.
    await app_client.patch(f"/api/v1/agents/{cc['id']}", json={"runner": {
        "preset": "claude-code", "command": fake, "autonomy": "ask"}})
    assert (await _agent(cc["id"])).runner["session"] == "sess-1"
    async with SessionLocal() as session:
        row = await session.get(Agent, cc["id"])
        row.runner = {**row.runner, "session": "gone"}
        await session.commit()
    await dm(app_client, org["id"], "Cody", "after the reset")
    await wait_for(lambda: _count(_turns(cc["id"]), 5), timeout=30, msg="fresh turn done")
    assert "knows-recent" in await _reply(cc["id"])
    assert (await _agent(cc["id"])).runner["session"] == "sess-1"

    # Clearing its context ends its Claude Code session too.
    r = await app_client.post(f"/api/v1/agents/{cc['id']}/reset-memory")
    assert r.status_code == 200, r.text

    async def forgotten() -> bool:
        return "session" not in (await _agent(cc["id"])).runner

    await wait_for(forgotten, timeout=15, msg="session cleared")


async def _count(coro, n: int) -> bool:
    return len(await coro) >= n


async def _working(agent_id: str) -> bool:
    return (await _agent(agent_id)).runtime_status == "working"


async def test_a_usage_limit_parks_the_work_until_it_resets(app_client, tmp_path):
    fake = _script(tmp_path, "claude", fake_agents.CLAUDE)
    org = await make_org(app_client)
    cc = await make_agent(app_client, org["id"], "Lim", kind="cli", runner={
        "preset": "claude-code", "command": fake})
    await dm(app_client, org["id"], "Lim", "LIMIT please")
    await wait_for(lambda: _cooling(cc["id"]), timeout=30, msg="parked")
    agent = await _agent(cc["id"])
    retry_at = agent.retry_at if agent.retry_at.tzinfo else agent.retry_at.replace(tzinfo=UTC)
    assert datetime.now(UTC) + timedelta(minutes=50) < retry_at < datetime.now(UTC) + timedelta(minutes=70)
    assert agent.consecutive_failures == 0 and "usage limit" in agent.runtime_detail
    waiting = await rows(Delivery, Delivery.agent_id == cc["id"], Delivery.status == "pending")
    assert len(waiting) == 1  # the message waits for the reset, not lost


async def _cooling(agent_id: str) -> bool:
    return (await _agent(agent_id)).runtime_status == "cooling_down"


async def test_codex_events_resume_and_limits(app_client, tmp_path):
    fake = _script(tmp_path, "codex", fake_agents.CODEX)
    org = await make_org(app_client)
    cx = await make_agent(app_client, org["id"], "Rex", kind="cli", runner={
        "preset": "codex", "command": fake, "effort": "low"})
    await dm(app_client, org["id"], "Rex", "list the files")
    await wait_for(lambda: _turns(cx["id"]), timeout=60, msg="codex turn")
    assert await _reply(cx["id"]) == "codex says: list the files"
    turn = (await _turns(cx["id"]))[0]
    assert turn.input_tokens == 100 and turn.output_tokens == 30 and turn.steps == 2
    calls = {c.name: c for c in await rows(ToolCall, ToolCall.agent_id == cx["id"])}
    assert calls["shell"].status == "ok" and calls["shell"].args == {"command": "ls"}
    assert calls["apply_patch"].args == {"files": ["a.py"]}
    first = _starts(fake)[0]
    assert "--json" in first and 'model_reasoning_effort="low"' in first

    await dm(app_client, org["id"], "Rex", "and again")
    await wait_for(lambda: _count(_turns(cx["id"]), 2), timeout=60, msg="resumed turn")
    assert await _reply(cx["id"]) == "codex says: and again (resumed)"
    second = _starts(fake)[1]
    assert second[second.index("resume") + 1] == "thread-9"

    await dm(app_client, org["id"], "Rex", "LIMIT")
    await wait_for(lambda: _cooling(cx["id"]), timeout=30, msg="codex parked")


async def test_acp_agents_hermes_and_any_other(app_client, tmp_path):
    fake = _script(tmp_path, "acp_agent", fake_agents.ACP)
    org = await make_org(app_client)
    h = await make_agent(app_client, org["id"], "Hermy", kind="cli", runner={
        "preset": "acp", "command": fake, "args": [], "model": "big", "autonomy": "ask"})

    await dm(app_client, org["id"], "Hermy", "what is in the notes?")
    await wait_for(lambda: _turns(h["id"]), timeout=60, msg="acp turn")
    reply = await _reply(h["id"])
    assert reply.startswith("acp s-1 turn 1: what is in the notes?") and "knows-pantheon" in reply
    log = _starts(fake)
    opened = [x for x in log if isinstance(x, dict) and "new" in x]
    assert len(opened) == 1 and opened[0]["mcp"] == ["pantheon"]  # Pantheon's tools, over MCP
    assert {"session/set_model": "a:big"} in log and {"session/set_mode": "accept_edits"} in log
    reads = [c for c in await rows(ToolCall, ToolCall.agent_id == h["id"]) if c.name == "Read notes.md"]
    assert reads and reads[0].result == "the notes"
    turn = (await _turns(h["id"]))[0]
    assert turn.input_tokens == 40 and turn.output_tokens == 9

    # A permission request goes to the Inbox; the session carries on (resume, not new).
    await dm(app_client, org["id"], "Hermy", "RISKY tidy")
    await wait_for(lambda: _pending_approval(h["id"]), timeout=30, msg="acp approval")
    apv = (await _pending_approval(h["id"]))[0]
    assert apv.tool == "Run rm -rf build" and apv.args == {"command": "rm -rf build"}
    await app_client.post(f"/api/v1/approvals/{apv.id}", json={"approved": True})
    await wait_for(lambda: _count(_turns(h["id"]), 2), timeout=30, msg="acp turn 2")
    reply = await _reply(h["id"])
    assert reply.startswith("acp s-1 turn 2: RISKY tidy ran it") and "knows-pantheon" not in reply

    await dm(app_client, org["id"], "Hermy", "LIMIT")
    await wait_for(lambda: _cooling(h["id"]), timeout=30, msg="acp parked")


async def test_antigravity_agy(app_client, tmp_path):
    from app.agents.connect import agy

    fake = _script(tmp_path, "agy", fake_agents.AGY)
    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Gem", kind="cli", runner={
        "preset": "agy", "command": fake, "model": "gemini-3.8-flash-low", "effort": "low",
        "autonomy": "ask"})

    # A turn: in its own home with Pantheon's plugin (tools, context) and allow rule.
    await dm(app_client, org["id"], "Gem", "hello agy")
    await wait_for(lambda: _turns(a["id"]), timeout=60, msg="agy turn")
    reply = await _reply(a["id"])
    assert reply.startswith("agy 1: hello agy") and "knows-pantheon" in reply
    assert "has-bridge" in reply and "may-use-bridge" in reply
    start = _starts(fake)[0]
    argv = start["args"]
    assert argv[argv.index("--model") + 1] == "gemini-3.8-flash-low"
    assert argv[argv.index("--effort") + 1] == "low" and "--mode" in argv
    assert "--dangerously-skip-permissions" not in argv and "--conversation" not in argv
    assert start["home"] == str(agy.home_for(a["id"]))
    turn = (await _turns(a["id"]))[0]
    assert turn.input_tokens == 54 and turn.output_tokens == 6
    # The bridge token isn't left on disk after the turn.
    assert not (agy.home_for(a["id"]) / ".gemini/config/plugins/pantheon/mcp_config.json").exists()
    assert (await _agent(a["id"])).runner["session"] == "conv-1"

    # What headless agy refuses goes to the Inbox; approved, it's allowed (exactly that)
    # and the same conversation carries on, so it does it.
    await dm(app_client, org["id"], "Gem", "RISKY clean up")
    await wait_for(lambda: _pending_approval(a["id"]), timeout=30, msg="agy approval")
    apv = (await _pending_approval(a["id"]))[0]
    assert apv.tool == "run_command" and apv.args["CommandLine"] == "rm -rf build"
    r = await app_client.post(f"/api/v1/approvals/{apv.id}", json={"approved": True})
    assert r.status_code == 200, r.text
    await wait_for(lambda: _count(_turns(a["id"]), 2), timeout=30, msg="agy approved turn")
    assert "ran it" in await _reply(a["id"])
    starts = _starts(fake)
    assert "command(rm -rf build)" in starts[-1]["allow"]
    assert starts[-1]["args"][starts[-1]["args"].index("--conversation") + 1] == "conv-1"
    shell = [c for c in await rows(ToolCall, ToolCall.agent_id == a["id"]) if c.name == "run_command"]
    assert {c.status for c in shell} == {"ok", "error"}  # refused, then done
    assert not [c for c in await rows(ToolCall, ToolCall.agent_id == a["id"])
                if c.name == "call_mcp_tool"]  # Pantheon's own tools show through the bridge

    # "edits": refused stays refused, nobody is asked.
    await app_client.patch(f"/api/v1/agents/{a['id']}", json={"runner": {
        "preset": "agy", "command": fake, "autonomy": "edits"}})
    # (forget the earlier approval, so the same command is refused again)
    settings_file = agy.home_for(a["id"]) / ".gemini/antigravity-cli/settings.json"
    data = json.loads(settings_file.read_text(encoding="utf-8"))
    data["permissions"]["allow"] = [x for x in data["permissions"]["allow"] if "rm -rf" not in x]
    settings_file.write_text(json.dumps(data), encoding="utf-8")
    await dm(app_client, org["id"], "Gem", "RISKY once more")
    await wait_for(lambda: _count(_turns(a["id"]), 3), timeout=30, msg="agy edits turn")
    assert "was refused" in await _reply(a["id"]) and not await _pending_approval(a["id"])

    # A lost conversation starts afresh; a quota error parks the agent.
    async with SessionLocal() as session:
        row = await session.get(Agent, a["id"])
        row.runner = {**row.runner, "session": "gone"}
        await session.commit()
    await dm(app_client, org["id"], "Gem", "after it was lost")
    await wait_for(lambda: _count(_turns(a["id"]), 4), timeout=30, msg="agy fresh turn")
    assert (await _reply(a["id"])).startswith("agy 1: after it was lost")
    await dm(app_client, org["id"], "Gem", "LIMIT")
    await wait_for(lambda: _cooling(a["id"]), timeout=30, msg="agy parked")


def test_agy_refusals_name_the_rule_that_allows_them():
    from app.agents.connect.agy import denied_rule

    assert denied_rule('permission check failed for command "ls -la": user denied permission '
                       'to run command:\nls -la\nDo not attempt…') == "command(ls -la)"
    assert denied_rule('permission check failed for read_file "C:\\x.txt": user denied '
                       'permission for read_file(C:\\x.txt)\nDo not…') == "read_file(C:\\x.txt)"
    assert denied_rule("permission check failed for mcp \"s/t\": user denied permission for "
                       "mcp(s/t)") == "mcp(s/t)"
    assert denied_rule("something else") is None


async def test_the_check_button(app_client, tmp_path):
    fake = _script(tmp_path, "acp_agent", fake_agents.ACP)
    r = await app_client.post("/api/v1/agent-kinds/cli/check", json={"runner": {
        "preset": "acp", "command": fake}})
    report = r.json()
    assert report["ok"], report
    assert [m["id"] for m in report["models"]] == ["a:small", "a:big"]
    r = await app_client.post("/api/v1/agent-kinds/cli/check", json={"runner": {
        "preset": "codex", "command": "definitely-not-installed-xyz"}})
    report = r.json()
    assert not report["ok"] and "npm install -g @openai/codex" in report["checks"][0]["detail"]
    listed = {k["id"]: k for k in (await app_client.get("/api/v1/agent-kinds")).json()}
    assert listed["cli"]["checkable"] and not listed["remote"]["checkable"]


def test_reset_times_in_limit_messages():
    from app.agents.connect.common import is_quota, reset_time

    now = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    assert reset_time("Try again in 2 hours", now) == now + timedelta(hours=2)
    assert reset_time("try again in 1h 30m", now) == now + timedelta(minutes=90)
    assert reset_time("Claude AI usage limit reached|1792048800", now) == \
        datetime.fromtimestamp(1792048800, UTC)
    assert reset_time("resets at 2026-10-15T15:00:00Z", now) == datetime(2026, 10, 15, 15, tzinfo=UTC)
    assert reset_time("something else", now) is None
    assert is_quota("Rate limit exceeded") and not is_quota("file not found")
