"""Zeus and Argus can run on a model of your choosing, and keep it across restarts (issue #1)."""

from __future__ import annotations

from app.llm.providers import resolve_for_agent
from app.services import supervisor


async def test_meta_agents_keep_their_chosen_model(app_client):
    big = await app_client.post("/api/v1/providers", json={
        "name": "Big", "type": "openai_compatible", "baseUrl": "http://big/v1",
        "models": [{"id": "big-model"}], "defaultModel": "big-model"})
    assert big.status_code in (200, 201), big.text
    big_id = big.json()["id"]

    for role in ("zeus", "argus"):
        meta = (await app_client.get(f"/api/v1/meta/{role}")).json()
        agent_id = meta["agent"]["id"]
        r = await app_client.patch(f"/api/v1/agents/{agent_id}", json={
            "model": {"provider_id": big_id, "model": "big-model", "reasoning_effort": "high"}})
        assert r.status_code == 200, r.text
        assert r.json()["model"]["model"] == "big-model"

    # A restart re-checks the meta agents; the choice must survive it.
    await supervisor.ensure_supervisor()
    for role in ("zeus", "argus"):
        agent = await supervisor.ensure_meta_agent(role)
        assert agent.model["model"] == "big-model"
        resolved = await resolve_for_agent(agent)
        assert resolved.model_id == "big-model" and resolved.provider_id == big_id
