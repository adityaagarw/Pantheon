"""Accounts for command-line agents: their own sign-in folder or API key, signing in with
the tool's own login, and a usage limit that cools down every agent on the account."""

from __future__ import annotations

import json

from app.models import Account, Agent, Turn
from app.services import accounts
from tests import fake_agents
from tests.conftest import wait_for
from tests.helpers import dm, make_agent, make_org, rows


def _script(tmp_path, name: str, body: str) -> str:
    path = tmp_path / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    return str(path)


def _envs(script: str) -> list[dict]:
    try:
        with open(script + ".env.log", encoding="utf-8") as f:
            return [json.loads(line) for line in f]
    except FileNotFoundError:
        return []


async def _status(agent_id: str) -> str:
    return (await rows(Agent, Agent.id == agent_id))[0].runtime_status


async def _done(agent_id: str, n: int = 1) -> bool:
    return len([t for t in await rows(Turn, Turn.agent_id == agent_id)
                if t.status == "completed"]) >= n


async def test_accounts_give_agents_their_own_sign_in_or_key(app_client, tmp_path):
    fake = _script(tmp_path, "claude", fake_agents.CLAUDE)
    r = await app_client.post("/api/v1/accounts", json={
        "tool": "claude-code", "method": "apikey", "name": "Team key", "key": "sk-ant-test-123456"})
    assert r.status_code == 201, r.text
    key = r.json()
    assert key["hasKey"] and "key" not in key and "sk-ant" not in json.dumps(key)
    stored = (await rows(Account, Account.id == key["id"]))[0]
    assert stored.key_enc and "sk-ant-test" not in stored.key_enc  # encrypted
    sub = (await app_client.post("/api/v1/accounts", json={
        "tool": "claude-code", "method": "subscription", "name": "Max plan"})).json()
    assert (await app_client.post("/api/v1/accounts", json={
        "tool": "claude-code", "method": "apikey"})).status_code == 400
    assert (await app_client.post("/api/v1/accounts", json={
        "tool": "agy", "method": "apikey", "key": "x"})).status_code == 400

    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Ann", kind="cli", runner={
        "preset": "claude-code", "command": fake, "account": sub["id"]})
    b = await make_agent(app_client, org["id"], "Bo", kind="cli", runner={
        "preset": "claude-code", "command": fake, "account": key["id"]})
    listed = (await app_client.get("/api/v1/accounts")).json()
    assert {x["id"]: x["agents"] for x in listed["accounts"]} == {key["id"]: ["Bo"], sub["id"]: ["Ann"]}
    assert {t["id"] for t in listed["tools"]} == {"claude-code", "codex"}

    # Each works in its account: its own settings folder, and only the key account's key.
    await dm(app_client, org["id"], "Ann", "hi")
    await wait_for(lambda: _done(a["id"]), timeout=60, msg="Ann's turn")
    await dm(app_client, org["id"], "Bo", "hi")
    await wait_for(lambda: _done(b["id"]), timeout=60, msg="Bo's turn")
    envs = _envs(fake)
    assert envs[0] == {"config": str(accounts.home(sub["id"])), "key": None}
    assert envs[1] == {"config": str(accounts.home(key["id"])), "key": "sk-ant-test-123456"}
    used = {x["id"]: x for x in (await app_client.get("/api/v1/accounts")).json()["accounts"]}
    assert used[sub["id"]]["costUsd"] == 0.01 and used[sub["id"]]["inputTokens"] == 15

    # Removing an account takes it off its agents (they use the computer's sign-in again).
    assert (await app_client.delete(f"/api/v1/accounts/{key['id']}")).status_code == 204
    assert (await rows(Agent, Agent.id == b["id"]))[0].runner["account"] == ""
    assert not accounts.home(key["id"]).exists()


async def test_signing_in_with_the_tools_own_login(app_client, tmp_path, monkeypatch):
    login = _script(tmp_path, "claude_login", fake_agents.CLAUDE_LOGIN)
    monkeypatch.setitem(accounts.TOOLS["claude-code"], "command", login)
    acc = (await app_client.post("/api/v1/accounts", json={
        "tool": "claude-code", "method": "subscription", "name": "Mine"})).json()
    checked = (await app_client.post(f"/api/v1/accounts/{acc['id']}/check")).json()
    assert checked["status"]["signedIn"] is False

    s = (await app_client.post(f"/api/v1/accounts/{acc['id']}/sign-in")).json()

    async def link_shown() -> bool:
        view = (await app_client.get(f"/api/v1/accounts/sign-ins/{s['id']}")).json()
        return bool(view["urls"]) and "Paste code" in view["output"]

    await wait_for(link_shown, timeout=30, msg="sign-in link")
    view = (await app_client.get(f"/api/v1/accounts/sign-ins/{s['id']}")).json()
    assert view["urls"] == ["https://claude.ai/oauth/authorize?code=true&state=abc"]
    r = await app_client.post(f"/api/v1/accounts/sign-ins/{s['id']}/input", json={"text": "good-code"})
    assert r.status_code == 200, r.text

    async def finished() -> bool:
        return (await app_client.get(f"/api/v1/accounts/sign-ins/{s['id']}")).json()["done"]

    await wait_for(finished, timeout=30, msg="sign-in done")
    assert (await app_client.get(f"/api/v1/accounts/sign-ins/{s['id']}")).json()["ok"]

    async def signed_in() -> bool:
        listed = (await app_client.get("/api/v1/accounts")).json()["accounts"]
        return any(x["id"] == acc["id"] and x["status"].get("signedIn") for x in listed)

    await wait_for(signed_in, timeout=30, msg="checked after signing in")
    listed = (await app_client.get("/api/v1/accounts")).json()["accounts"]
    assert "me@example.com · max" in listed[0]["status"]["detail"]
    assert (accounts.home(acc["id"]) / ".credentials.json").exists()  # kept in its folder


async def test_a_usage_limit_cools_the_account_and_its_fallback_takes_over(app_client, tmp_path):
    fake = _script(tmp_path, "claude", fake_agents.CLAUDE)
    main = (await app_client.post("/api/v1/accounts", json={
        "tool": "claude-code", "method": "subscription", "name": "Pro"})).json()
    spare = (await app_client.post("/api/v1/accounts", json={
        "tool": "claude-code", "method": "apikey", "name": "Spare key", "key": "sk-spare-0001"})).json()
    org = await make_org(app_client)
    await make_agent(app_client, org["id"], "Ann", kind="cli", runner={
        "preset": "claude-code", "command": fake, "account": main["id"]})
    b = await make_agent(app_client, org["id"], "Bo", kind="cli", runner={
        "preset": "claude-code", "command": fake, "account": main["id"]})
    c = await make_agent(app_client, org["id"], "Cy", kind="cli", runner={
        "preset": "claude-code", "command": fake, "account": main["id"],
        "fallback_account": spare["id"]})

    await dm(app_client, org["id"], "Ann", "LIMIT")
    await wait_for(lambda: _cooling(main["id"]), timeout=30, msg="account cooling")
    starts = len(_envs(fake))

    # Bo, on the same account, waits without even starting.
    await dm(app_client, org["id"], "Bo", "anything")

    async def parked() -> bool:
        return await _status(b["id"]) == "cooling_down"

    await wait_for(parked, timeout=30, msg="Bo parked")
    assert len(_envs(fake)) == starts
    # Cy works meanwhile, with its fallback account.
    await dm(app_client, org["id"], "Cy", "keep going")
    await wait_for(lambda: _done(c["id"]), timeout=60, msg="Cy on the fallback")
    assert _envs(fake)[-1]["key"] == "sk-spare-0001"

    # The user says the limit is over: the account is free again.
    r = await app_client.patch(f"/api/v1/accounts/{main['id']}", json={"cooling": False})
    assert r.json()["coolingUntil"] is None


async def _cooling(account_id: str) -> bool:
    row = (await rows(Account, Account.id == account_id))[0]
    return row.cooling_until is not None
