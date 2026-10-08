"""Shadowing: a recorder posts the user's work, the agent learns it and keeps a playbook."""

from __future__ import annotations

from app.models import Memory, Playbook
from tests.conftest import wait_for
from tests.helpers import call, last_human, make_agent, make_org, rows, say, script

# a 1x1 PNG
PIXEL = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
         "YAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
SECRET = "sk-live-0123456789abcdef"


async def test_record_learn_and_keep_a_playbook(app_client):
    org = await make_org(app_client)
    mia = await make_agent(app_client, org["id"], "Mia")
    await app_client.post(f"/api/v1/orgs/{org['id']}/secrets", json={
        "name": "STRIPE_KEY", "value": SECRET, "agents": ["Mia"], "domains": ["api.stripe.com"]})

    r = await app_client.post(f"/api/v1/orgs/{org['id']}/shadow", json={
        "agentId": mia["id"], "title": "Weekly invoice run", "source": "browser",
        "note": "Every Monday morning"})
    assert r.status_code == 201, r.text
    rec = r.json()
    code = rec["code"]
    assert len(code) == 6

    # The extension trades the code for the session, then posts what the user did.
    found = (await app_client.post("/api/v1/shadow/connect", json={"code": code.lower()})).json()
    assert found["sessionId"] == rec["id"]
    url = f"/api/v1/shadow/{rec['id']}/events"
    events = [
        {"t": 0.5, "kind": "navigate", "data": {"url": "https://billing.example.com/invoices",
                                                "title": "Invoices"}, "image": PIXEL},
        {"t": 4, "kind": "click", "data": {"tag": "button", "label": "New invoice"}},
        {"t": 9, "kind": "input", "data": {"label": "Customer", "value": "Acme Corp"}},
        {"t": 12, "kind": "input", "data": {"label": "API key", "value": f"key {SECRET}"}},
        {"t": 15, "kind": "submit", "data": {"label": "Send invoice"}},
        {"t": 16, "kind": "bogus", "data": {}},
    ]
    assert (await app_client.post(url, json={"events": events})).status_code == 401
    ok = await app_client.post(url, json={"events": events}, headers={"X-Shadow-Code": code})
    assert ok.json() == {"recording": True, "kept": 5}

    detail = (await app_client.get(f"/api/v1/shadow/{rec['id']}")).json()
    assert detail["eventCount"] == 5 and detail["agentName"] == "Mia"
    assert SECRET not in str(detail["events"])
    img = detail["events"][0]["image"]
    assert (await app_client.get(img)).headers["content-type"] == "image/png"

    prompts = script(
        mia["id"],
        call("shadow_review", session=rec["id"]),
        call("save_playbook", session=rec["id"], title="Weekly invoice run",
             content="1. Open billing.example.com/invoices\n2. New invoice for each customer\n"
                     "3. Send it"),
        say("Learned it."), then=say("ok"))
    learned = await app_client.post(f"/api/v1/shadow/{rec['id']}/learn")
    assert learned.json()["status"] == "learning"
    # Recording stopped: late events are refused politely.
    late = await app_client.post(url, json={"events": events[:1]}, headers={"X-Shadow-Code": code})
    assert late.json()["recording"] is False

    await wait_for(lambda: rows(Playbook), timeout=30, msg="playbook saved")
    assert "Weekly invoice run" in last_human(prompts[0])
    review = [m for m in prompts[1] if m.type == "tool"][0]
    timeline = str(review.content)
    assert "filled \"Customer\" = 'Acme Corp'" in timeline
    assert "clicked" not in timeline and "click on button \"New invoice\"" in timeline
    assert "[screenshot 1]" in timeline and SECRET not in timeline

    after = (await app_client.get(f"/api/v1/orgs/{org['id']}/shadow")).json()[0]
    assert after["status"] == "learned" and after["code"] is None
    books = (await app_client.get(f"/api/v1/orgs/{org['id']}/playbooks")).json()
    assert books[0]["title"] == "Weekly invoice run" and books[0]["agentId"] == mia["id"]
    assert any(m.kind == "playbook" for m in await rows(Memory, Memory.agent_id == mia["id"]))
    tools = {t["name"] for t in (await app_client.get(
        f"/api/v1/orgs/{org['id']}")).json()["agents"][0]["tools"]}
    assert {"shadow_review", "read_playbook"} <= tools


async def test_only_one_sandbox_recording_and_unknown_codes(app_client, monkeypatch):
    from app.services import shadow

    async def no_desktop(session_id, org_id):  # the sandbox isn't running in tests
        return None

    monkeypatch.setattr(shadow, "_record_sandbox", no_desktop)
    org = await make_org(app_client)
    ann = await make_agent(app_client, org["id"], "Ann")
    first = await app_client.post(f"/api/v1/orgs/{org['id']}/shadow", json={
        "agentId": ann["id"], "title": "Triage", "source": "sandbox"})
    assert first.status_code == 201
    second = await app_client.post(f"/api/v1/orgs/{org['id']}/shadow", json={
        "agentId": ann["id"], "title": "Again", "source": "sandbox"})
    assert second.status_code == 400 and "already" in second.json()["detail"]
    assert (await app_client.post("/api/v1/shadow/connect", json={"code": "NOPE00"})).status_code == 404
    empty = await app_client.post(f"/api/v1/shadow/{first.json()['id']}/learn")
    assert empty.status_code == 400 and "nothing was recorded" in empty.json()["detail"]
    await app_client.delete(f"/api/v1/shadow/{first.json()['id']}")
    assert (await app_client.get(f"/api/v1/orgs/{org['id']}/shadow")).json() == []


async def test_pantheon_host_records_my_computer_without_a_code(app_client):
    import asyncio

    from app.services import host as host_svc

    org = await make_org(app_client)
    bo = await make_agent(app_client, org["id"], "Bo")
    headers = {"X-Host-Token": host_svc.token()}
    await app_client.post("/api/v1/host/hello", headers=headers,
                          json={"host": "my-pc", "capabilities": ["kronos", "recorder"]})
    jobs: list[dict] = []

    async def fake_host():
        while True:
            r = await app_client.get("/api/v1/host/next?wait=2", headers=headers)
            if r.status_code == 204:
                continue
            job = r.json()
            jobs.append(job)
            rec = ({"sessionId": job["args"]["sessionId"], "title": job["args"]["title"]}
                   if job["op"] == "record_start" else None)
            await app_client.post("/api/v1/host/hello", headers=headers, json={
                "host": "my-pc", "capabilities": ["kronos", "recorder"], "recording": rec})
            await app_client.post("/api/v1/host/result", headers=headers,
                                  json={"id": job["id"], "ok": True, "result": "ok"})

    task = asyncio.create_task(fake_host())
    try:
        rec = (await app_client.post(f"/api/v1/orgs/{org['id']}/shadow", json={
            "agentId": bo["id"], "title": "Month-end close", "source": "desktop"})).json()
        assert jobs[0]["op"] == "record_start" and jobs[0]["args"]["sessionId"] == rec["id"]
        listed = (await app_client.get(f"/api/v1/orgs/{org['id']}/shadow")).json()[0]
        assert listed["hostRecording"] == "my-pc"
        await app_client.post(f"/api/v1/shadow/{rec['id']}/stop")
        assert jobs[-1]["op"] == "record_stop"
    finally:
        task.cancel()
        host_svc.bridge.last_seen = 0

    # The browser extension finds a waiting browser recording without a code.
    assert (await app_client.get("/api/v1/shadow/pending?source=browser")).json() == {"session": None}
    b = (await app_client.post(f"/api/v1/orgs/{org['id']}/shadow", json={
        "agentId": bo["id"], "title": "Supplier portal", "source": "browser"})).json()
    found = (await app_client.get("/api/v1/shadow/pending?source=browser")).json()["session"]
    assert found["sessionId"] == b["id"] and found["code"] == b["code"] and found["agentName"] == "Bo"
