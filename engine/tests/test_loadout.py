"""The lean loadout offers the core tools whole and the rest by name; old bulky tool output
is trimmed before a summary is needed."""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.graph import KEEP_TOOL_RESULTS, TRIM_BATCH, trim_old_results
from app.tools import loadout


def _tools(names):
    return {n: SimpleNamespace(description=f"Does {n}. More words.") for n in names}


ORG = SimpleNamespace(settings={})
MANY = [*loadout.CORE, *(f"extra_{i}" for i in range(40))]


def test_core_tools_are_offered_and_the_rest_listed_until_loaded():
    tools = _tools(MANY)
    offered, deferred = loadout.split(ORG, tools, [])
    assert set(offered) == set(loadout.CORE) and len(deferred) == 40
    assert "- extra_3: Does extra_3" in loadout.catalogue(tools, deferred)
    # Loading one (or just calling one) brings it in for the rest of the turn.
    history = [HumanMessage("go"), AIMessage("", tool_calls=[
        {"id": "1", "name": "load_tools", "args": {"names": ["extra_3"]}},
        {"id": "2", "name": "extra_7", "args": {}}])]
    offered, deferred = loadout.split(ORG, tools, history)
    assert {"extra_3", "extra_7"} <= set(offered) and "extra_3" not in deferred


def test_the_core_is_pis_four_plus_subagents_and_team_tools_join_it_with_colleagues():
    assert {"read_file", "write_file", "edit_file", "run_command", "spawn_subagent"} <= loadout.CORE
    tools = _tools(MANY + ["send_message", "update_task"])
    tools["send_message"].core = True  # resolve.py marks these for someone with colleagues
    offered, deferred = loadout.split(ORG, tools, [])
    assert "send_message" in offered and "update_task" in deferred


async def test_tools_used_lately_stay_offered_in_later_turns(app_client):
    # A timed run loaded stage_check again and again: what was loaded went with the turn's
    # history. Tools the agent called lately stay offered, without a load.
    from app.core.db import SessionLocal
    from app.models import ToolCall
    from tests.helpers import make_agent, make_org

    org = await make_org(app_client)
    ann = await make_agent(app_client, org["id"], "Ann")
    async with SessionLocal() as session:
        for i, name in enumerate(("extra_5", "subagent:read_file")):
            session.add(ToolCall(id=f"call_{i}", org_id=org["id"], agent_id=ann["id"], name=name,
                                 args={}))
        await session.commit()
    sticky = await loadout.recent_tools(ann["id"])
    assert sticky == ["extra_5"]
    offered, deferred = loadout.split(ORG, _tools(MANY), [], sticky)
    assert "extra_5" in offered and "extra_5" not in deferred


def test_few_tools_or_the_all_setting_offer_everything():
    few = _tools(["read_file", "web_search", "whiteboard_draw"])
    assert loadout.split(ORG, few, []) == (sorted(few), [])
    everything = SimpleNamespace(settings={"tools": "all"})
    offered, deferred = loadout.split(everything, _tools(MANY), [])
    assert deferred == [] and len(offered) == len(set(MANY)) - 1  # load_tools isn't needed


def _results(n: int) -> list:
    msgs = [HumanMessage("go", id="h")]
    for i in range(n):
        msgs.append(AIMessage("", id=f"a{i}", tool_calls=[{"id": f"c{i}", "name": "read_file",
                                                            "args": {}}]))
        msgs.append(ToolMessage("x" * 5000 if i != 1 else "short", tool_call_id=f"c{i}",
                                name="read_file", id=f"t{i}"))
    return msgs


def test_old_bulky_results_are_trimmed_in_batches_and_recent_ones_kept():
    # One short of a batch: nothing changes, so the cached prompt prefix holds.
    assert trim_old_results(_results(KEEP_TOOL_RESULTS + TRIM_BATCH)) == []
    msgs = _results(KEEP_TOOL_RESULTS + TRIM_BATCH + 1)
    trimmed = trim_old_results(msgs)
    # All the old bulky ones at once; the short one and the latest few stay.
    assert [m.id for m in trimmed] == [f"t{i}" for i in range(TRIM_BATCH + 1) if i != 1]
    assert trimmed[0].content.startswith("[trimmed: earlier read_file output, 5,000 characters")
    done = {t.id: t for t in trimmed}
    assert not trim_old_results([done.get(m.id, m) for m in msgs])
