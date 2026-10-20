"""The SQL checkpointer behaves like LangGraph expects (persist, resume, interrupt)."""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from app.agents.checkpointer import SqlCheckpointSaver
from app.core.db import SessionLocal
from app.models import LgCheckpoint
from tests.helpers import rows


class S(TypedDict, total=False):
    items: Annotated[list[str], operator.add]
    approved: bool


def _graph(saver, calls: list[str]):
    g = StateGraph(S)

    async def a(state: S) -> dict:
        calls.append("a")
        return {"items": ["a"]}

    async def gate(state: S) -> dict:
        ok = interrupt({"question": "go?"})
        return {"approved": bool(ok)}

    def fan(state: S) -> list[Send]:
        return [Send("work", {"n": i}) for i in range(3)]

    async def work(arg: dict) -> dict:
        calls.append(f"w{arg['n']}")
        return {"items": [f"w{arg['n']}"]}

    g.add_node("a", a)
    g.add_node("gate", gate)
    g.add_node("work", work)
    g.add_edge(START, "a")
    g.add_edge("a", "gate")
    g.add_conditional_edges("gate", fan, ["work"])
    g.add_edge("work", END)
    return g.compile(checkpointer=saver)


async def test_interrupt_resume_and_fanout_survive_a_new_saver(db):
    calls: list[str] = []
    cfg = {"configurable": {"thread_id": "t1"}}
    g1 = _graph(SqlCheckpointSaver(SessionLocal), calls)
    await g1.ainvoke({"items": []}, cfg)
    snap = await g1.aget_state(cfg)
    assert snap.next == ("gate",)
    assert snap.interrupts and snap.interrupts[0].value == {"question": "go?"}

    # A brand-new saver + graph (== process restart) resumes from Postgres.
    g2 = _graph(SqlCheckpointSaver(SessionLocal), calls)
    out = await g2.ainvoke(Command(resume=True), cfg)
    assert out["approved"] is True
    assert sorted(out["items"]) == ["a", "w0", "w1", "w2"]
    assert calls.count("a") == 1  # completed node was not re-run
    assert (await g2.aget_state(cfg)).next == ()

    history = [c async for c in g2.checkpointer.alist(cfg)]
    assert len(history) >= 3


async def test_update_state_as_node_then_continue(db):
    calls: list[str] = []
    saver = SqlCheckpointSaver(SessionLocal)
    g = _graph(saver, calls)
    cfg = {"configurable": {"thread_id": "t2"}}
    await g.aupdate_state(cfg, {"items": ["seed"]}, as_node="a")
    snap = await g.aget_state(cfg)
    assert snap.next == ("gate",)
    assert snap.values["items"] == ["seed"]


async def test_prune_keeps_newest_and_delete_thread(db):
    saver = SqlCheckpointSaver(SessionLocal, keep=3)
    g = StateGraph(S)

    async def step(state: S) -> dict:
        return {"items": ["x"]}

    g.add_node("step", step)
    g.add_edge(START, "step")
    g.add_edge("step", END)
    graph = g.compile(checkpointer=saver)
    cfg = {"configurable": {"thread_id": "t3"}}
    for _ in range(10):
        await graph.ainvoke({"items": []}, cfg)
    await saver._prune("t3", "")
    assert len(await rows(LgCheckpoint, LgCheckpoint.thread_id == "t3")) == 3
    assert len((await graph.aget_state(cfg)).values["items"]) == 10  # state intact
    await saver.adelete_thread("t3")
    assert await rows(LgCheckpoint, LgCheckpoint.thread_id == "t3") == []
