"""Workspaces made with an organization (a new folder, one on this computer, a clone), git,
and a worktree and branch for each agent who writes files, merged by their lead."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.models import Agent, ToolCall
from app.services import workspaces
from tests.conftest import wait_for
from tests.helpers import call, dm, make_agent, rows, say, script

WRITER = [{"name": n} for n in ("read_file", "write_file", "edit_file", "list_dir")]


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture(autouse=True)
def quick_sync(monkeypatch):
    monkeypatch.setattr(workspaces, "SYNC_DELAY", 0.05)


async def _new_org(client, name="Forge", **setup) -> dict:
    r = await client.post("/api/v1/orgs", json={"name": name, "setup": {"source": "new", **setup}})
    assert r.status_code == 201, r.text
    return r.json()


async def _team(client, org_id: str) -> tuple[dict, dict, dict]:
    lead = await make_agent(client, org_id, "Lena", tools=WRITER)
    ada = await make_agent(client, org_id, "Ada", tools=WRITER, manager="Lena")
    ben = await make_agent(client, org_id, "Ben", tools=WRITER, manager="Lena")
    await wait_for(lambda: _worktrees(org_id, 2), msg="worktrees made")
    return lead, ada, ben


async def _worktrees(org_id: str, n: int) -> bool:
    return len([a for a in await rows(Agent, Agent.org_id == org_id) if a.worktree]) >= n


async def _done(agent_id: str, n: int) -> bool:
    calls = await rows(ToolCall, ToolCall.agent_id == agent_id)
    a = (await rows(Agent, Agent.id == agent_id))[0]
    return len(calls) >= n and a.runtime_status == "idle"


def _out(prompts, i: int) -> str:
    from langchain_core.messages import ToolMessage

    outs = [str(m.content) for m in prompts[i] if isinstance(m, ToolMessage)]
    return outs[-1] if outs else ""


async def test_a_new_org_gets_a_git_workspace_and_worktrees(app_client, workspace_root):
    org = await _new_org(app_client)
    root = Path(org["workspace"])
    assert root.parent == workspace_root.resolve() and root.name.startswith("forge-")
    assert org["settings"]["git"] == {"enabled": True, "worktrees": True, "base": "main"}
    assert sh(root, "log", "--format=%s") == "Pantheon: the starting point"
    assert ".pantheon/" in (root / ".git" / "info" / "exclude").read_text()

    lead, ada, ben = await _team(app_client, org["id"])
    agents = {a.name: a for a in await rows(Agent, Agent.org_id == org["id"])}
    assert agents["Lena"].worktree is None  # leads work on main
    assert agents["Ada"].worktree == ".pantheon/worktrees/ada"
    assert sh(root / ".pantheon/worktrees/ada", "rev-parse", "--abbrev-ref", "HEAD") == "agent/ada"
    assert sh(root, "status", "--porcelain") == ""  # worktrees aren't untracked files on main

    listing = (await app_client.get(f"/api/v1/orgs/{org['id']}/worktrees")).json()
    by_path = {t["path"]: t for t in listing["worktrees"]}
    assert by_path["."]["main"] and by_path["."]["branch"] == "main"
    assert by_path[".pantheon/worktrees/ben"]["agentName"] == "Ben"


async def test_workers_commit_and_their_lead_reviews_and_merges(app_client, workspace_root):
    org = await _new_org(app_client)
    root = Path(org["workspace"])
    lead, ada, ben = await _team(app_client, org["id"])

    # Ada and Ben work on their own branches and commit.
    sa = script(ada["id"], call("write_file", path="app.py", content="print('ada')\n"),
                call("git_status"), call("git_commit", message="Add app.py"), say("done"))
    await dm(app_client, org["id"], "Ada", "Write app.py.")
    await wait_for(lambda: _done(ada["id"], 3), msg="ada committed")
    assert "agent/ada" in _out(sa, 2) and "?? app.py" in _out(sa, 2)
    assert "Committed" in _out(sa, 3)
    assert not (root / "app.py").exists()  # not on main yet

    sb = script(ben["id"], call("write_file", path="app.py", content="print('ben')\n"),
                call("git_commit", message="Ben's app.py"), say("done"))
    await dm(app_client, org["id"], "Ben", "Write app.py too.")
    await wait_for(lambda: _done(ben["id"], 2), msg="ben committed")
    assert "Committed" in _out(sb, 2)

    # Lena sees both, merges Ada; Ben's conflicts and is left as it was.
    sl = script(lead["id"], call("team_work"), call("team_work", person="Ada"),
                call("merge_work", person="Ada"), call("merge_work", person="Ben"), say("ok"))
    await dm(app_client, org["id"], "Lena", "Review and merge.")
    await wait_for(lambda: _done(lead["id"], 4), msg="lead merged")
    assert "Ada (agent/ada): 1 ahead" in _out(sl, 1)
    assert "+print('ada')" in _out(sl, 2)
    assert "Merged Ada's agent/ada into main" in _out(sl, 3)
    assert "conflicts in: app.py" in _out(sl, 4)
    assert (root / "app.py").read_text() == "print('ada')\n"
    assert sh(root, "status", "--porcelain") == ""  # the failed merge was undone

    # Ben brings in main, which conflicts too; he's told what to resolve.
    sb2 = script(ben["id"], call("git_update"), say("ok"))
    await dm(app_client, org["id"], "Ben", "Update your branch.")
    await wait_for(lambda: _done(ben["id"], 3), msg="ben updated")
    assert "conflicts in: app.py" in _out(sb2, 1)

    # Workers don't get the lead's tools.
    tools = {c.name for c in await rows(ToolCall, ToolCall.agent_id == ada["id"])}
    assert "merge_work" not in tools

    # The worktree page: what Ben's branch changes, and merging from the interface.
    d = (await app_client.get(f"/api/v1/orgs/{org['id']}/worktrees/diff",
                              params={"path": ".pantheon/worktrees/ben"})).json()
    assert d["branch"] == "agent/ben" and "app.py" in d["stat"]
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/worktrees/merge",
                              json={"path": ".pantheon/worktrees/ben"})
    assert r.json()["merged"] is False and r.json()["conflicts"] == ["app.py"]


async def test_a_folder_on_this_computer_becomes_the_workspace(app_client, tmp_path):
    folder = tmp_path / "my project"
    folder.mkdir()
    (folder / "notes.md").write_text("mine\n")
    r = await app_client.post("/api/v1/orgs", json={"name": "Mine", "setup": {
        "source": "folder", "path": str(folder), "worktrees": False}})
    assert r.status_code == 201, r.text
    org = r.json()
    assert Path(org["workspace"]) == folder.resolve()
    assert sh(folder, "ls-files") == "notes.md"  # what was there is the first commit
    assert folder.resolve() in workspaces.picked_folders()
    await make_agent(app_client, org["id"], "Ada", tools=WRITER)
    assert not await _worktrees(org["id"], 1)  # worktrees were turned off

    # An existing repository keeps its branch.
    repo = tmp_path / "repo"
    repo.mkdir()
    sh(repo, "init", "-b", "trunk")
    (repo / "a.txt").write_text("a")
    sh(repo, "add", "-A")
    sh(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "first")
    r = await app_client.post("/api/v1/orgs", json={"name": "Repo", "setup": {
        "source": "folder", "path": str(repo)}})
    assert r.json()["settings"]["git"]["base"] == "trunk"
    assert sh(repo, "log", "--format=%s") == "first"

    # A folder that isn't there makes no organization.
    before = len((await app_client.get("/api/v1/orgs")).json())
    r = await app_client.post("/api/v1/orgs", json={"name": "Nope", "setup": {
        "source": "folder", "path": str(tmp_path / "missing")}})
    assert r.status_code == 400 and len((await app_client.get("/api/v1/orgs")).json()) == before


async def test_a_repository_is_cloned_and_git_turned_on_later(app_client, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    sh(src, "init", "-b", "main")
    (src / "README.md").write_text("hello\n")
    sh(src, "add", "-A")
    sh(src, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init")
    r = await app_client.post("/api/v1/orgs", json={"name": "Clone", "setup": {
        "source": "clone", "url": src.resolve().as_uri()}})
    assert r.status_code == 201, r.text
    root = Path(r.json()["workspace"])
    assert (root / "README.md").read_text() == "hello\n"
    assert r.json()["settings"]["git"]["enabled"]

    # An org made without git can turn it on in the folder it has.
    plain = (await app_client.post("/api/v1/orgs", json={"name": "Plain"})).json()
    assert "git" not in plain["settings"]
    await make_agent(app_client, plain["id"], "Ada", tools=WRITER)
    r = await app_client.post(f"/api/v1/orgs/{plain['id']}/workspace", json={"source": "current"})
    assert r.status_code == 200 and r.json()["settings"]["git"]["enabled"]
    await wait_for(lambda: _worktrees(plain["id"], 1), msg="worktree after turning git on")
