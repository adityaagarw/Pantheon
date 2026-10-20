"""One acceptance check of the whole result: a goal handed in is checked with fresh eyes,
comes back with every problem at once, is re-checked only on those, and closes itself."""

from __future__ import annotations

from app.models import Agent, Org, Task, TaskEvent
from tests.conftest import wait_for
from tests.helpers import call, make_agent, make_org, rows, say, script


def _brain(agent_steps: list, checks: list[str], verdicts: list[str]):
    """One mock model for the agent and its acceptance checker (they share the agent's model)."""
    state = {"n": 0}

    def fn(messages, tools):
        if "temporary helper" in str(messages[0].content):
            checks.append(str(messages[-1].content))
            return say(verdicts[min(len(checks), len(verdicts)) - 1])
        step = agent_steps[state["n"]] if state["n"] < len(agent_steps) else say("ok")
        state["n"] += 1
        return step

    return fn


async def _goal(org_id: str) -> Task:
    return (await rows(Task, Task.org_id == org_id, Task.parent_id.is_(None)))[0]


async def test_a_goal_comes_back_with_every_problem_then_closes_when_fixed(app_client):
    org = await make_org(app_client)
    ann = await make_agent(app_client, org["id"], "Ann")
    checks: list[str] = []
    script(ann["id"], then=_brain(
        [call("update_task", task="T-1", status="done", result="report.txt written"),
         say("handed in"),
         call("update_task", task="T-1", status="review", result="total fixed"),
         say("handed in again")],
        checks,
        ["1. The total in report.txt is wrong (12, not 13).\n2. No header row.\nVERDICT: FAIL",
         "Both fixed.\nVERDICT: PASS"]))
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/tasks", json={
        "title": "Write the March report", "description": "report.txt with a header and the total",
        "assigneeId": ann["id"]})
    assert r.status_code == 201, r.text

    async def status(want: str) -> bool:
        return (await _goal(org["id"])).status == want

    await wait_for(lambda: len(checks) >= 1, timeout=30, msg="the first check")
    assert "What the user asked for:\nWrite the March report" in checks[0]
    assert "report.txt with a header and the total" in checks[0]
    await wait_for(lambda: len(checks) >= 2, timeout=30, msg="the re-check")
    # The re-check looks only at what was found before.
    assert "The problems found before:" in checks[1] and "No header row." in checks[1]
    await wait_for(lambda: status("done"), timeout=30, msg="the goal closed")
    events = await rows(TaskEvent, TaskEvent.kind == "acceptance")
    assert [e.data["passed"] for e in events] == [False, True]


async def test_the_last_round_closes_the_goal_with_known_issues(app_client):
    org = await make_org(app_client)
    ann = await make_agent(app_client, org["id"], "Ann")
    checks: list[str] = []
    script(ann["id"], then=_brain(
        [call("update_task", task="T-1", status="review", result="v1"), say("in"),
         call("update_task", task="T-1", status="review", result="v2"), say("in")],
        checks, ["1. Broken.\nVERDICT: FAIL", "1. Still broken.\nVERDICT: FAIL"]))
    await app_client.post(f"/api/v1/orgs/{org['id']}/tasks", json={
        "title": "Fix it", "assigneeId": ann["id"]})

    async def closed() -> bool:
        return (await _goal(org["id"])).status == "done"

    await wait_for(closed, timeout=30, msg="closed after the last round")
    goal = await _goal(org["id"])
    assert "Known issues (acceptance check 2 of 2):\n1. Still broken." in goal.result
    assert len(checks) == 2  # no third round


async def test_a_brief_sent_by_message_becomes_the_goal_and_is_checked(app_client):
    # A brief typed to a lead used to stay a message: no goal, so nothing was checked as a
    # whole, and the pieces were reviewed one by one instead.
    from tests.helpers import dm

    org = await make_org(app_client)
    iva = await make_agent(app_client, org["id"], "Iva")
    checks: list[str] = []
    script(iva["id"], then=_brain(
        [call("create_task", title="Yoga booking app", goal=True, description="phone-first"),
         call("create_task", title="Again", goal=True),  # the same request isn't a second goal
         call("update_task", task="T-1", status="review", result="four screens"),
         say("handed in")],
        checks, ["Nothing breaks it.\nVERDICT: PASS"]))
    await dm(app_client, org["id"], "Iva", "Design a booking app for yoga studios.")

    async def closed() -> bool:
        goals = await rows(Task, Task.org_id == org["id"])
        return len(goals) == 1 and goals[0].status == "done"

    await wait_for(closed, timeout=30, msg="the goal checked and closed")
    goal = await _goal(org["id"])
    assert goal.reporter_id == "user" and goal.assignee_id == iva["id"]
    assert goal.description.startswith("Design a booking app for yoga studios.")
    assert "Notes from Iva:\nphone-first" in goal.description
    assert "Design a booking app for yoga studios." in checks[0]  # checked against their words


async def test_a_goal_needs_a_request_from_the_user(app_client):
    from app.tools.base import ToolContext, ToolError
    from app.tools.collab import create_task

    org = await make_org(app_client)
    ann = await make_agent(app_client, org["id"], "Ann")
    ctx = ToolContext(org=(await rows(Org, Org.id == org["id"]))[0],
                      agent=(await rows(Agent, Agent.id == ann["id"]))[0],
                      turn_id=None, tool_call_id="t")
    try:
        await create_task({"title": "x", "goal": True}, ctx)
        raise AssertionError("expected a refusal")
    except ToolError as e:
        assert "there is none" in str(e)


async def test_the_checker_writes_its_own_tests_and_leaves_the_work_as_it_was(app_client):
    # A check that may not write tests passed a paging bug its own tests would have shown
    # (api-v2-migration); now it writes them under .acceptance/ and nowhere else.
    import time

    from app.services import acceptance
    from app.tools.base import ToolContext, ToolError
    from app.tools.workspace import write_file

    org = await make_org(app_client)
    ann = await make_agent(app_client, org["id"], "Ann")
    ctx = ToolContext(org=(await rows(Org, Org.id == org["id"]))[0],
                      agent=(await rows(Agent, Agent.id == ann["id"]))[0],
                      turn_id=None, tool_call_id="t", writes_under=acceptance.SCRATCH)
    root = ctx.workspace
    (root / "app.py").write_text("v = 1\n", encoding="utf-8")
    (root / "data.db").write_bytes(b"rows")
    began = time.monotonic()
    before = acceptance._snapshot(root)

    assert "Created" in await write_file({"path": ".acceptance/test_paging.py",
                                          "content": "# a test"}, ctx)
    try:
        await write_file({"path": "app.py", "content": "v = 2\n"}, ctx)
        raise AssertionError("expected a refusal")
    except ToolError as e:
        assert "only under .acceptance/" in str(e)
    # What the check's commands did to the work: a change, a deletion, a new file.
    (root / "app.py").write_text("v = 3\n", encoding="utf-8")
    (root / "data.db").unlink()
    (root / "out.log").write_text("x", encoding="utf-8")

    put_back = acceptance._restore(root, before, began)
    assert sorted(put_back) == ["app.py", "data.db", "out.log"]
    assert (root / "app.py").read_text(encoding="utf-8") == "v = 1\n"
    assert (root / "data.db").read_bytes() == b"rows" and not (root / "out.log").exists()
    assert (root / ".acceptance" / "test_paging.py").exists()  # its tests stay for the fixer


async def test_a_long_spec_is_checked_in_parts_and_failing_tests_fail_the_check(app_client):
    # The team missed one report rule in all three ledger-engine runs: one checker can't hold
    # a 20K spec. Each part goes to its own checker; and the project's tests, run twice by
    # Pantheon, fail the check whatever the checkers say.
    from app.services import acceptance
    from app.tools.base import ToolContext

    org = await make_org(app_client)
    ann = await make_agent(app_client, org["id"], "Ann")
    root = ToolContext(org=(await rows(Org, Org.id == org["id"]))[0],
                       agent=(await rows(Agent, Agent.id == ann["id"]))[0],
                       turn_id=None, tool_call_id="t").workspace
    (root / "SPEC.md").write_text("# Spec\n\n" + "".join(
        f"## {i}. Part {i}\n\n" + "A rule. " * 700 + "\n\n" for i in range(1, 5)), encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_total.py").write_text(
        "import unittest\n\nclass Total(unittest.TestCase):\n"
        "    def test_total(self):\n        self.assertEqual(1 + 1, 3)\n\n"
        "    def test_name(self):\n        pass\n", encoding="utf-8")
    assert acceptance._spec_parts(root, "Build what `SPEC.md` says.") == (
        "SPEC.md", [["1. Part 1"], ["2. Part 2", "3. Part 3"], ["4. Part 4"]])

    checks: list[str] = []
    script(ann["id"], then=_brain(
        [call("update_task", task="T-1", status="review", result="built"), say("in"),
         call("update_task", task="T-1", status="review", result="built"), say("in")],
        checks, ["Nothing breaks it.\nVERDICT: PASS"]))
    await app_client.post(f"/api/v1/orgs/{org['id']}/tasks", json={
        "title": "Build it", "description": "Build what `SPEC.md` says.", "assigneeId": ann["id"]})
    await wait_for(lambda: len(checks) >= 4, timeout=60, msg="three parts, then a re-check")
    first = sorted(checks[:3])
    assert [("part 1:" in c, "part 2:" in c, "part 3:" in c) for c in first] == [
        (True, False, False), (False, True, False), (False, False, True)]
    assert "2. Part 2; 3. Part 3" in first[1] and "work together" in first[0]
    assert ".acceptance/part2/" in first[1]
    assert all("Total.test_total: failed 2 of 2 runs" in c for c in first)
    events = await rows(TaskEvent, TaskEvent.kind == "acceptance")
    assert events[0].data["passed"] is False
    assert events[0].data["problems"].startswith("1. The project's own tests fail")
    assert "Part 3 of the check:\nNothing breaks it." in events[0].data["problems"]


async def test_the_check_can_run_on_its_own_model(app_client):
    org = await make_org(app_client, settings={"checker_model": {"model": "mock-checker"}})
    got = (await rows(Org, Org.id == org["id"]))[0].settings["checker_model"]
    assert got == {"model": "mock-checker"}
    r = await app_client.patch(f"/api/v1/orgs/{org['id']}",
                               json={"settings": {"checker_model": {"nonsense": 1}}})
    assert r.status_code >= 400


async def test_pieces_close_without_a_sign_off_and_the_check_can_be_turned_off(app_client):
    org = await make_org(app_client)
    dev = await make_agent(app_client, org["id"], "Dev")
    script(dev["id"], call("update_task", task="T-2", status="done", result="built"),
           say("done"), then=say("ok"))
    goal = (await app_client.post(f"/api/v1/orgs/{org['id']}/tasks", json={
        "title": "The goal"})).json()
    await app_client.post(f"/api/v1/orgs/{org['id']}/tasks", json={
        "title": "A piece", "assigneeId": dev["id"], "parentId": goal["id"]})

    async def piece_done() -> bool:
        return (await rows(Task, Task.org_id == org["id"], Task.number == 2))[0].status == "done"

    await wait_for(piece_done, timeout=30, msg="the piece closed by its builder")

    from app.services import acceptance

    off = await make_org(app_client, settings={"acceptance": "off"})
    assert not acceptance.enabled((await rows(Org, Org.id == off["id"]))[0])
