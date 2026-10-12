"""Automatic compaction: measured with real token counts, run during a turn, not only
before it, and able to cut inside one long tool loop."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agents.graph import compaction_drop, context_size, estimate_tokens
from app.models import LlmCall
from tests.conftest import wait_for
from tests.helpers import dm, make_agent, make_org, rows, say, script


def _loop(n: int, size: int = 4000) -> list:
    """One turn: the ask, then n tool steps (each a call and its result), then a reply."""
    out: list = [HumanMessage(content="Build the thing.", id="h0")]
    for i in range(n):
        out.append(AIMessage(content="", id=f"a{i}", tool_calls=[
            {"name": "write_file", "args": {"content": f"step-{i} " + "x" * size},
             "id": f"tc{i}"}]))
        out.append(ToolMessage(content="ok", tool_call_id=f"tc{i}", id=f"t{i}"))
    return out


def test_estimate_counts_tool_call_arguments_and_errs_high():
    msgs = _loop(2, size=3200)
    # Two calls of ~3,200 characters each: about 1,000 tokens apiece at 3.2 per token,
    # where the old chars/4 rule said 800.
    assert estimate_tokens(msgs) >= 2000


def test_context_size_uses_the_last_measurement_plus_what_arrived_since():
    msgs = [*_loop(3), ToolMessage(content="y" * 3200, tool_call_id="late", id="late")]
    # Measured 50,000 at the last call; one ~1,000-token tool result arrived since.
    size = context_size({"messages": msgs, "context_tokens": 50_000})
    assert 50_900 <= size <= 51_200
    # Nothing measured yet: an estimate with room for the system prompt and tools.
    assert context_size({"messages": msgs}) > estimate_tokens(msgs)


def test_a_long_turn_is_cut_inside_keeping_its_ask_and_its_latest_steps():
    msgs = _loop(12)
    drop = {m.id for m in compaction_drop(msgs, point=5000)}
    kept = [m for m in msgs if m.id not in drop]
    assert kept[0].id == "h0"  # the transcript still starts with what was asked
    assert kept[1].id.startswith("a")  # and resumes on one of the agent's steps
    assert "a11" in {m.id for m in kept} and "a0" in drop
    # Every kept tool call still has its result, and no result lost its call.
    calls = {tc["id"] for m in kept if isinstance(m, AIMessage) for tc in m.tool_calls}
    results = {m.tool_call_id for m in kept if isinstance(m, ToolMessage)}
    assert calls == results
    assert estimate_tokens(kept) < 5000


def test_earlier_exchanges_go_first_and_a_short_turn_is_left_whole():
    old = [HumanMessage(content="old ask " + "o" * 20000, id="h-old"),
           AIMessage(content="old answer", id="a-old")]
    msgs = [*old, *_loop(2, size=400)]
    drop = {m.id for m in compaction_drop(msgs, point=5000)}
    assert drop == {"h-old", "a-old"}


async def test_a_long_tool_loop_compacts_mid_turn_and_finishes(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada", model={"compact_at": 6000})
    steps = {"n": 0}

    def respond(messages, tools):
        system = str(messages[0].content) if isinstance(messages[0], SystemMessage) else ""
        if "running working-memory summary" in system:
            return say("SUMMARY: saved notes so far.")
        steps["n"] += 1
        if steps["n"] > 10:
            return say("All notes saved.")
        # The bulk is in the reply's text: the mock model counts text, not call arguments.
        return AIMessage(content=f"note-{steps['n']} " + "w" * 4000, tool_calls=[
            {"name": "remember", "args": {"content": f"note {steps['n']}"}, "id": "x"}])

    prompts = script(ada["id"], then=respond)
    await dm(app_client, org["id"], "Ada", "Save ten long notes.")

    async def finished() -> bool:
        return any("All notes saved." in str(m.content)
                   for p in prompts for m in p if isinstance(m, AIMessage)) or steps["n"] > 10
    await wait_for(finished, msg="the turn finishes")

    compactions = await rows(LlmCall, LlmCall.agent_id == ada["id"],
                             LlmCall.purpose == "compaction")
    assert compactions, "a ten-step loop past the compaction point compacts during the turn"
    last = [p for p in prompts
            if "running working-memory summary" not in str(p[0].content)][-1]
    text = "\n".join(str(m.content) for m in last) + "\n".join(
        str(tc["args"]) for m in last if isinstance(m, AIMessage) for tc in m.tool_calls)
    assert "Save ten long notes." in text  # the ask survives the cut
    assert "SUMMARY: saved notes so far." in text  # the summary is in the system prompt
    assert "note-1 " not in text and "note-10 " in text  # old steps went, new ones stayed
