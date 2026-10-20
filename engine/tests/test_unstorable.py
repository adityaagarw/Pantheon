"""Tool calls carrying text the database can't store must not kill the turn (issue #3)."""

from __future__ import annotations

from app.core.config import settings
from app.models import Agent, Message, ToolCall, Turn
from tests.conftest import wait_for
from tests.helpers import call, dm, make_agent, make_org, rows, say, script


async def _turn_done(agent_id: str) -> bool:
    return any(t.status in ("completed", "failed") for t in await rows(Turn, Turn.agent_id == agent_id))


async def _run(app_client, *steps):
    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Vega")
    prompts = script(a["id"], *steps, say("done"))
    await dm(app_client, org["id"], "Vega", "go")
    await wait_for(lambda: _turn_done(a["id"]), msg="turn", timeout=30)
    turn = (await rows(Turn, Turn.agent_id == a["id"]))[0]
    agent = (await rows(Agent, Agent.id == a["id"]))[0]
    assert turn.status == "completed" and agent.runtime_status != "error"
    # ...and its reply reached the user (posting it used to fail on NUL-bearing transcripts).
    replies = await wait_for(lambda: rows(Message, Message.sender_id == a["id"]), msg="reply")
    assert [m.content for m in replies] == ["done"]
    calls = {tc.name: tc for tc in await rows(ToolCall, ToolCall.agent_id == a["id"])}
    return prompts, calls


async def test_bad_bytes_in_args_and_output_are_stored_and_flagged(app_client):
    prompts, calls = await _run(app_client,
                                call("write_file", path="vega.bin", content="head\x00\x00tail"),
                                call("read_file", path="vega.bin"))
    seen = [str(m.content) for m in prompts[2] if m.type == "tool"][-1]
    assert "headtail" in seen and "\x00" not in seen
    assert "2 NUL bytes removed" in seen  # the agent is told, not silently handed altered text
    assert calls["read_file"].status == "ok" and "2 NUL bytes removed" in calls["read_file"].result
    assert calls["write_file"].args["content"] == "head\x00\x00tail"  # args kept as sent


async def test_an_unstorable_record_becomes_a_visible_placeholder(app_client, monkeypatch):
    # A lone surrogate can't be encoded as UTF-8, so recording the call itself fails.
    from app.agents import graph

    real = graph._record_text
    monkeypatch.setattr(graph, "_record_text",
                        lambda out: real(out) + ("\ud800" if "a-b" in str(out) else ""))
    prompts, calls = await _run(app_client,
                                call("write_file", path="vega.txt", content="a-b"),
                                call("read_file", path="vega.txt"))
    assert [str(m.content) for m in prompts[2] if m.type == "tool"][-1].endswith("a-b")  # got it
    rec = calls["read_file"]
    assert rec.result.startswith("[Pantheon: this read_file result could not be stored (")
    seen = [str(m.content) for m in prompts[2] if m.type == "tool"][-1]
    assert "surrogates not allowed" in rec.result and f"It was {len(seen)} characters" in rec.result
    assert rec.args == {"_unrecorded": True, "keys": ["path"]}


async def test_long_records_say_they_were_truncated(app_client, monkeypatch):
    monkeypatch.setattr(settings, "tool_record_cap_chars", 10)
    prompts, calls = await _run(app_client,
                                call("write_file", path="vega-long.txt", content="x" * 50),
                                call("read_file", path="vega-long.txt"))
    seen = [str(m.content) for m in prompts[2] if m.type == "tool"][-1]  # header + numbered line
    assert f"record truncated, 10 of {len(seen)} characters kept" in calls["read_file"].result
