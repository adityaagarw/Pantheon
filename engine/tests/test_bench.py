"""Benchmarking organizations with SwarmBench: runs on copies, grading, attempts for the CLI."""

from __future__ import annotations

import json

from app.models import Agent, Org
from app.services import bench
from tests.conftest import wait_for
from tests.helpers import call, make_agent, make_org, rows, say, script

TASK = "month-end-close"


def _copy_solution(task_id: str) -> str:
    """A shell command that copies the reference solution into the work folder (cwd)."""
    src = (bench.BENCH_ROOT / "tasks" / task_id / "solution").as_posix()
    return f"python -c \"import shutil; shutil.copytree(r'{src}', '.', dirs_exist_ok=True)\""


async def test_the_catalog_lists_tasks_and_workflows(app_client):
    data = (await app_client.get("/api/v1/bench/tasks")).json()
    assert len(data["tasks"]) == 8 and any(t["id"] == TASK for t in data["tasks"])
    assert any(w["name"] == "solo" for w in data["workflows"])


async def test_a_run_copies_the_org_runs_the_task_and_grades_it(app_client, monkeypatch):
    monkeypatch.setattr(bench, "POLL", 0.2)
    # The scripted lead copies the reference solution out of the benchmark, which lives in
    # Pantheon's source: a real agent is refused that (guard.py); this one is let through.
    from app.core import guard

    monkeypatch.setattr(guard, "SOURCE", guard.SOURCE / "not-here")
    org = await make_org(app_client, "Bench Co")
    lead = await make_agent(app_client, org["id"], "Lea", persona="You lead.")
    max_ = await make_agent(app_client, org["id"], "Max", persona="You help.")
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/relationships",
                              json={"fromId": lead["id"], "toId": max_["id"], "kind": "manages"})
    assert r.status_code == 201
    await app_client.patch(f"/api/v1/agents/{lead['id']}", json={
        "tools": [{"name": "run_command", "approval": "ask"}, {"name": "update_task"}],
        "model": {"temperature": 0.3}})

    seen: dict = {}

    def first(messages, tools):
        seen["prompt"] = str(messages[-1].content)
        return call("run_command", command=_copy_solution(TASK))

    from app.services import comms

    send = comms.send_direct

    async def send_and_script(org_id, sender, to, content, **kw):
        # The copy's lead has a new id: give it its script just before it gets the task.
        # Handing in the goal starts the acceptance check, a helper on Lea's model: either
        # reply after that passes it.
        script(to, first, call("update_task", task="T-1", status="review", result="Closed"),
               say("March is closed.\nVERDICT: PASS"), then=say("VERDICT: PASS"))
        seen["lead"] = to
        return await send(org_id, sender, to, content, **kw)

    monkeypatch.setattr(comms, "send_direct", send_and_script)
    r = await app_client.post("/api/v1/bench/runs", json={
        "orgId": org["id"], "tasks": [TASK], "workflow": "none", "idleSeconds": 1})
    assert r.status_code == 201
    run = r.json()
    assert run["status"] == "running" and run["progress"]["total"] == 1
    await wait_for(lambda: _has(seen, "lead"), msg="the copy got the task")
    copy_lea = (await rows(Agent, Agent.id == seen["lead"]))[0]
    clone_id = copy_lea.org_id
    assert clone_id != org["id"] and copy_lea.name == "Lea"  # the lead, not the helper
    assert copy_lea.model.get("temperature") == 0.3  # same model settings as the original
    assert all(t["approval"] == "auto" for t in copy_lea.tools
               if t["name"] not in bench.OUTSIDE_HELP)  # nobody is there to approve
    from app.tools.resolve import resolve_tools

    offered = await resolve_tools((await rows(Org, Org.id == clone_id))[0], copy_lea)
    assert "ask_zeus" not in offered.tools and "run_command" in offered.tools  # the org works alone

    async def finished() -> bool:
        got = (await app_client.get(f"/api/v1/bench/runs/{run['id']}")).json()
        return got["status"] == "done"

    await wait_for(finished, timeout=120, msg="the run finished")
    got = (await app_client.get(f"/api/v1/bench/runs/{run['id']}")).json()
    result = got["results"][0]
    assert result["task_id"] == TASK and result["score"] == 100.0
    assert result["run"]["detail"] == "Lea finished the goal"  # not "the team went quiet"
    assert "T-1" in seen["prompt"]
    assert result["parts"]["hidden_tests"]["passed"] == result["parts"]["hidden_tests"]["total"]
    assert result["metrics"]["turns"] >= 1 and result["metrics"]["agents"] == 2
    assert result["metrics"]["first_edit_minutes"] is None  # Lea ran a command, edited nothing
    assert "Work folder" in seen["prompt"] and "close" in seen["prompt"].lower()
    assert got["summary"]["meanScore"] == 100.0
    assert not await rows(Org, Org.id == clone_id)  # the copy is gone
    assert (await app_client.get("/api/v1/orgs")).json()[0]["name"] == "Bench Co"

    listed = (await app_client.get(f"/api/v1/bench/runs?org_id={org['id']}")).json()
    assert [x["id"] for x in listed] == [run["id"]]
    assert (await app_client.delete(f"/api/v1/bench/runs/{run['id']}")).status_code == 204
    assert json.dumps(got)


async def test_attempts_for_the_cli_check_the_folder(app_client):
    org = await make_org(app_client, "Solo")
    await make_agent(app_client, org["id"], "Ann")
    bad = await app_client.post("/api/v1/bench/attempts", json={
        "org": "Solo", "prompt": "do it", "workspace": "C:/nowhere"})
    assert bad.status_code == 400 and "workspace root" in bad.json()["detail"]
    missing = await app_client.post("/api/v1/bench/attempts", json={"org": "Nope", "prompt": "x",
                                                                     "workspace": "/tmp"})
    assert missing.status_code == 400 and "no organization" in missing.json()["detail"]
    bad_run = await app_client.post("/api/v1/bench/runs", json={"orgId": org["id"], "tasks": ["nope"]})
    assert bad_run.status_code == 400 and "no task" in bad_run.json()["detail"]


async def _has(d: dict, key: str) -> bool:
    return key in d


async def test_a_dead_model_server_is_caught_before_and_during_a_run(app_client):
    from app.core.db import SessionLocal
    from app.models import LlmCall

    down = (await app_client.post("/api/v1/providers", json={
        "name": "Down", "type": "openai_compatible", "baseUrl": "http://127.0.0.1:9/v1",
        "models": [{"id": "nothing"}], "defaultModel": "nothing"})).json()
    org = await make_org(app_client, "Offline Co")
    ann = await make_agent(app_client, org["id"], "Ann")
    await app_client.patch(f"/api/v1/agents/{ann['id']}", json={"model": {"provider_id": down["id"]}})
    r = await app_client.post("/api/v1/bench/runs", json={"orgId": org["id"], "tasks": [TASK]})
    assert r.status_code == 400 and "isn't answering" in r.json()["detail"]
    assert "Down" in r.json()["detail"]

    # During an attempt: two failed calls and none that worked means the model is down.
    async with SessionLocal() as session:
        session.add(LlmCall(org_id=org["id"], agent_id=ann["id"], error="OpenAITimeoutError: timed out"))
        await session.commit()
    assert await bench._model_dead(org["id"]) is None  # one failure could be a blip
    async with SessionLocal() as session:
        session.add(LlmCall(org_id=org["id"], agent_id=ann["id"], error="OpenAITimeoutError: timed out"))
        await session.commit()
    assert "isn't answering" in (await bench._model_dead(org["id"]) or "")
    async with SessionLocal() as session:
        session.add(LlmCall(org_id=org["id"], agent_id=ann["id"]))  # one call that worked
        await session.commit()
    assert await bench._model_dead(org["id"]) is None
