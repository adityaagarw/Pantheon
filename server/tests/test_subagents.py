"""Just-in-time subagents: a fresh context with the agent's tools, and only the report comes back."""

from __future__ import annotations

from langchain_core.messages import SystemMessage

from app.models import LlmCall, ToolCall, Turn
from tests.conftest import wait_for
from tests.helpers import call, dm, make_agent, make_org, rows, say, script, tool_results

TOOLS = [{"name": n, "approval": a} for n, a in (
    ("spawn_subagent", "auto"), ("write_file", "auto"), ("read_file", "auto"),
    ("send_message", "auto"), ("run_command", "ask"))]


async def _done(agent_id: str) -> bool:
    turns = await rows(Turn, Turn.agent_id == agent_id)
    return bool(turns) and all(t.status != "running" for t in turns)


async def test_a_subagent_works_in_a_fresh_context_and_reports_back(app_client, workspace_root):
    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Boss", tools=TOOLS)
    sub_seen: dict = {}

    def sub_first(messages, tools):
        sub_seen["messages"], sub_seen["tools"] = messages, {t["function"]["name"] for t in tools}
        return call("write_file", path="hello.txt", content="hi")

    prompts = script(
        a["id"],
        call("spawn_subagent", task="Write hello.txt saying hi, then report."),
        sub_first,
        say("Wrote hello.txt (2 bytes)."),
        say("All done."))
    await dm(app_client, org["id"], "Boss", "SECRET-PARENT-CONTEXT: get hello.txt written")
    await wait_for(lambda: _done(a["id"]), msg="turn")

    # The helper started fresh: its own brief and the task, none of the parent's conversation.
    msgs = sub_seen["messages"]
    assert isinstance(msgs[0], SystemMessage) and "temporary helper" in str(msgs[0].content)
    assert len(msgs) == 2 and "Write hello.txt" in str(msgs[1].content)
    assert not any("SECRET-PARENT-CONTEXT" in str(m.content) for m in msgs)
    # Its tools: the parent's own, minus messaging, approvals and spawning another helper.
    assert {"write_file", "read_file"} <= sub_seen["tools"]
    assert not {"send_message", "run_command", "spawn_subagent"} & sub_seen["tools"]

    # It did the work, and the parent received only the report.
    assert (workspace_root / org["id"] / "hello.txt").read_text() == "hi"
    report = tool_results(prompts[3])[0]
    assert report.startswith("Wrote hello.txt (2 bytes).") and "[subagent: 2 steps, 1 tool calls" \
        in report
    assert [c.name for c in await rows(ToolCall, ToolCall.agent_id == a["id"])
            if c.name.startswith("subagent:")] == ["subagent:write_file"]
    assert len(await rows(LlmCall, LlmCall.purpose == "subagent")) == 2


async def test_a_subagent_stops_at_its_step_limit_and_still_reports(app_client):
    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Boss", tools=TOOLS)
    seen: list = []

    def looping(messages, tools):
        seen.append(bool(tools))
        return call("read_file", path="missing.txt") if tools else say("Partial: file missing.")

    prompts = script(a["id"], call("spawn_subagent", task="Find it.", max_steps=2),
                     looping, looping, looping, say("ok"))
    await dm(app_client, org["id"], "Boss", "go")
    await wait_for(lambda: _done(a["id"]), msg="turn")
    assert seen == [True, True, False]  # the last call has no tools: report now
    assert tool_results(prompts[-1])[0].startswith("Partial: file missing.")


async def test_a_subagent_cannot_be_given_tools_its_agent_lacks(app_client):
    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Boss", tools=TOOLS)
    prompts = script(a["id"], call("spawn_subagent", task="x", tools=["send_message"]), say("ok"))
    await dm(app_client, org["id"], "Boss", "go")
    await wait_for(lambda: _done(a["id"]), msg="turn")
    assert "a subagent can't use send_message" in tool_results(prompts[1])[0]
