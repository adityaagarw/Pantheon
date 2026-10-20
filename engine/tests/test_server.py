"""Server mode: other devices need the access code; the interface is served for browsers;
the extra listener starts and stops."""

from __future__ import annotations

import socket

import httpx
from httpx import ASGITransport, AsyncClient

from app.services import server


def _remote(app) -> AsyncClient:
    """A client on another device (the access check looks at the client's address)."""
    return AsyncClient(transport=ASGITransport(app=app, client=("192.168.1.50", 50000)),
                       base_url="http://pantheon.lan:8730")


async def test_other_devices_need_the_access_code(app_client):
    from app.main import app

    code = server.status()["accessCode"]
    assert code and (await app_client.get("/api/v1/server")).json()["accessCode"] == code
    assert (await app_client.get("/api/v1/orgs")).status_code == 200  # this computer: no code

    async with _remote(app) as far:
        assert (await far.get("/api/v1/orgs")).status_code == 401
        page = await far.get("/org/abc")
        assert page.status_code == 401 and "access code" in page.text
        wrong = await far.post("/access", data={"code": "nope"})
        assert wrong.status_code == 401 and "isn't right" in wrong.text
        right = await far.post("/access", data={"code": code})
        assert right.status_code == 303 and "pantheon_access" in right.headers["set-cookie"]

        far.cookies.set("pantheon_access", code)
        assert (await far.get("/api/v1/orgs")).status_code == 200
        far.cookies.clear()
        bearer = await far.get("/api/v1/orgs", headers={"Authorization": f"Bearer {code}"})
        assert bearer.status_code == 200
        link = await far.get(f"/org/abc?access={code}&tab=board")
        assert link.status_code == 303 and link.headers["location"] == "/org/abc?tab=board"

    renewed = (await app_client.put("/api/v1/server", json={"newAccessCode": True})).json()
    assert renewed["accessCode"] != code
    async with _remote(app) as far:
        far.cookies.set("pantheon_access", code)
        assert (await far.get("/api/v1/orgs")).status_code == 401  # the old code stops working


async def test_the_interface_is_served_with_its_routes(app_client, tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=root>PANTHEON UI</div>", encoding="utf-8")
    (tmp_path / "assets" / "app-1234.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setenv("PANTHEON_UI_DIR", str(tmp_path))
    for path in ("/", "/org/abc/settings"):
        r = await app_client.get(path)
        assert r.status_code == 200 and "PANTHEON UI" in r.text, path
    js = await app_client.get("/assets/app-1234.js")
    assert "immutable" in js.headers["cache-control"]
    assert (await app_client.get("/api/v1/nothing-here")).status_code == 404
    assert (await app_client.get("/../pyproject.toml")).status_code in (200, 404)
    assert "hatchling" not in (await app_client.get("/..%2Fpyproject.toml")).text


async def test_server_mode_listens_on_its_port_and_stops(app_client):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    st = (await app_client.put("/api/v1/server", json={"enabled": True, "port": port})).json()
    try:
        assert st["running"] and st["error"] is None, st
        assert any(u.endswith(f":{port}/") for u in st["urls"])
        async with httpx.AsyncClient() as c:
            r = await c.get(f"http://127.0.0.1:{port}/api/v1/health")
        assert r.json()["status"] == "ok"
        bad = await app_client.put("/api/v1/server", json={"port": 80})
        assert bad.status_code == 400
    finally:
        off = (await app_client.put("/api/v1/server", json={"enabled": False})).json()
    assert not off["running"]


async def test_a_listener_that_closes_is_opened_again(app_client, monkeypatch):
    # On Windows one failed accept (WinError 64) closes the listening socket for good, while
    # the server still looks started: other devices couldn't connect until a restart.
    from tests.conftest import wait_for

    monkeypatch.setattr(server, "WATCH_SECONDS", 0.2)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    await app_client.put("/api/v1/server", json={"enabled": True, "port": port})
    try:
        first = server._live["server"]
        for srv in first.servers:
            for sock in srv.sockets:
                sock._sock.close()  # what asyncio does after the failed accept
        assert not server.status()["running"]  # not reported as running any more

        async def reopened() -> bool:
            return server._live["server"] is not first and server.status()["running"]

        await wait_for(reopened, timeout=10, msg="the listener was opened again")
        async with httpx.AsyncClient() as c:
            r = await c.get(f"http://127.0.0.1:{port}/api/v1/health")
        assert r.json()["status"] == "ok"
    finally:
        await app_client.put("/api/v1/server", json={"enabled": False})
