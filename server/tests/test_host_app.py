"""Pantheon Host: Kronos's folders (only the user picks them), start at login, and the
folder endpoints the web UI uses."""

from __future__ import annotations

import asyncio
import json
import platform
import plistlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the repo root, for host/

from host import autostart, paths, system  # noqa: E402
from host.kronos_ops import Refused  # noqa: E402
from host.main import Host  # noqa: E402


@pytest.fixture
def settings(tmp_path, monkeypatch):
    cfg = tmp_path / "host.json"
    monkeypatch.setattr(paths, "CONFIG", cfg)
    monkeypatch.setattr(paths, "OLD_CONFIG", tmp_path / "kronos.json")
    return cfg


def test_folders_are_chosen_on_screen_and_can_be_taken_away(settings, tmp_path, monkeypatch):
    reports = tmp_path / "Reports"
    reports.mkdir()
    host = Host()
    monkeypatch.setattr(system, "ask_folder", lambda *a: None)
    with pytest.raises(Refused, match="no folder chosen"):
        host.pick_folder({})
    monkeypatch.setattr(system, "ask_folder", lambda *a: str(reports))
    assert "may now use" in host.pick_folder({})
    assert json.loads(settings.read_text())["allowed"] == [str(reports.resolve())]
    assert paths.allowed_roots() == [reports.resolve()]
    with pytest.raises(Refused, match="not a folder"):
        host.allow({"path": str(tmp_path / "missing")})
    host.disallow({"path": str(reports)})
    assert paths.allowed_roots() == []
    assert "folders" in host.capabilities()


def test_start_at_login_on_linux_and_macos(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(autostart, "_run", lambda cmd: None)

    monkeypatch.setattr(autostart, "SYSTEM", "Linux")
    monkeypatch.setattr(autostart.shutil, "which", lambda name: None)  # no systemd here
    assert not autostart.installed()
    autostart.install()
    entry = (tmp_path / ".config/autostart/pantheon-host.desktop").read_text()
    exec_line = next(line for line in entry.splitlines() if line.startswith("Exec="))
    assert "main.py" in exec_line and exec_line.endswith("--at-login")
    assert autostart.installed()
    autostart.uninstall()
    assert not autostart.installed()

    monkeypatch.setattr(autostart.shutil, "which", lambda name: "/usr/bin/systemctl")
    autostart.install()
    unit = (tmp_path / ".config/systemd/user/pantheon-host.service").read_text()
    assert "--at-login" in unit and "Restart=on-failure" in unit
    autostart.uninstall()
    assert not autostart.installed()

    monkeypatch.setattr(autostart, "SYSTEM", "Darwin")
    autostart.install()
    plist = plistlib.loads((tmp_path / "Library/LaunchAgents/ai.pantheon.host.plist").read_bytes())
    assert plist["RunAtLoad"] and plist["ProgramArguments"][-1] == "--at-login"
    autostart.uninstall()
    assert not autostart.installed()


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows shortcuts")
def test_start_at_login_on_windows_makes_a_real_shortcut(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))  # never the real Startup folder
    link = tmp_path / "Microsoft/Windows/Start Menu/Programs/Startup/Pantheon Host.lnk"
    autostart.install()
    assert link.exists() and link.stat().st_size > 0
    autostart.uninstall()
    assert not link.exists()


def test_web_url_follows_the_backend(settings):
    settings.write_text(json.dumps({"backend": "http://localhost:8710"}))
    assert system.web_url() == "http://localhost:3710"
    settings.write_text(json.dumps({"backend": "https://192.168.1.5:3711"}))
    assert system.web_url() == "https://192.168.1.5:3711"


async def test_web_ui_asks_pantheon_host_for_a_folder(app_client):
    from app.services import host as host_svc

    headers = {"X-Host-Token": host_svc.token()}
    host_svc.bridge.last_seen = 0
    none = await app_client.post("/api/v1/host/folders/pick")
    assert none.status_code == 409
    await app_client.post("/api/v1/host/hello", headers=headers,
                          json={"host": "my-pc", "capabilities": ["kronos", "folders"]})
    seen: list[dict] = []

    async def fake_host():
        while True:
            r = await app_client.get("/api/v1/host/next?wait=2", headers=headers)
            if r.status_code == 204:
                continue
            job = r.json()
            seen.append(job)
            ok = job["op"] != "disallow" or job["args"]["path"] == "D:/Reports"
            await app_client.post("/api/v1/host/result", headers=headers, json={
                "id": job["id"], "ok": ok, "result": "Kronos may now use D:/Reports",
                "error": None if ok else "unknown folder"})

    task = asyncio.create_task(fake_host())
    try:
        r = await app_client.post("/api/v1/host/folders/pick")
        assert r.json() == {"message": "Kronos may now use D:/Reports"}
        assert seen[-1]["op"] == "pick_folder" and seen[-1]["args"] == {}  # the web can't name a path
        r = await app_client.post("/api/v1/host/folders/remove", json={"path": "E:/Other"})
        assert r.status_code == 400 and "unknown folder" in r.json()["detail"]
    finally:
        task.cancel()
        host_svc.bridge.last_seen = 0


def test_workspace_folders_are_mounted_through_the_override_file(settings, tmp_path, monkeypatch):
    from host import mounts

    repo = tmp_path / "Pantheon"
    (repo / "data" / "workspaces" / "inside").mkdir(parents=True)
    monkeypatch.setattr(paths, "REPO", repo)
    monkeypatch.setattr(mounts, "OVERRIDE", repo / "docker-compose.override.yml")
    restarts: list[int] = []
    monkeypatch.setattr(mounts, "_restart_backend", lambda: restarts.append(1))
    code = tmp_path / "My Code"
    code.mkdir()
    host = Host()
    assert "workspaces" in host.capabilities()

    monkeypatch.setattr(system, "ask_folder", lambda *a: None)
    with pytest.raises(Refused, match="no folder chosen"):
        host.pick_workspace({"org": "Acme"})

    # A folder Docker already shares (under PANTHEON_WORKSPACES): no mount, no restart.
    monkeypatch.setattr(system, "ask_folder", lambda *a: str(repo / "data/workspaces/inside"))
    got = host.pick_workspace({"org": "Acme"})
    assert got["path"] == "/workspaces/inside" and not got["restart"] and not restarts

    # Anything else is mounted at /host/<name> and the backend restarts to see it.
    monkeypatch.setattr(system, "ask_folder", lambda *a: str(code))
    got = host.pick_workspace({"org": "Acme"})
    assert got["path"].startswith("/host/my-code-") and got["restart"] and restarts == [1]
    override = mounts.OVERRIDE.read_text()
    assert mounts.MARK in override and f"target: \"{got['path']}\"" in override
    assert code.resolve().as_posix() in override
    again = host.pick_workspace({"org": "Acme"})  # already mounted: no second restart
    assert again["path"] == got["path"] and not again["restart"] and restarts == [1]

    # Pantheon may take a mount away; the file goes when nothing is left.
    assert "no longer shared" in host.ops()["unmount_workspace"]({"path": got["path"]})
    assert mounts.mounts() == {} and not mounts.OVERRIDE.exists()

    # Someone's own override file is never rewritten.
    mounts.OVERRIDE.write_text("services: {}\n")
    with pytest.raises(Refused, match="is yours"):
        mounts.mount(str(code))
    assert mounts.OVERRIDE.read_text() == "services: {}\n"


async def test_an_org_gets_a_workspace_from_this_computer(app_client):
    from app.core.config import settings as app_settings
    from app.models import Agent
    from app.services import host as host_svc
    from tests.helpers import make_agent, make_org, rows

    mounted = (app_settings.workspace_root_paths[0] / "host-app").as_posix()  # like /host/app-…
    headers = {"X-Host-Token": host_svc.token()}
    org = await make_org(app_client, "Builders")
    ann = await make_agent(app_client, org["id"], "Ann")
    host_svc.bridge.last_seen = 0
    r = await app_client.post("/api/v1/host/workspace", json={"orgId": org["id"]})
    assert r.status_code == 409 and "isn't running" in r.json()["detail"]
    await app_client.post("/api/v1/host/hello", headers=headers,
                          json={"host": "my-pc", "capabilities": ["folders", "workspaces"]})
    seen: list[dict] = []

    async def fake_host():
        while True:
            r = await app_client.get("/api/v1/host/next?wait=2", headers=headers)
            if r.status_code == 204:
                continue
            job = r.json()
            seen.append(job)
            await app_client.post("/api/v1/host/result", headers=headers, json={
                "id": job["id"], "ok": True, "error": None,
                "result": {"hostPath": "D:/code/app", "path": mounted, "restart": True}})

    task = asyncio.create_task(fake_host())
    try:
        from app.core.db import SessionLocal

        async with SessionLocal() as session:  # someone is working: a restart would stop them
            row = await session.get(Agent, ann["id"])
            row.runtime_status = "working"
            await session.commit()
        r = await app_client.post("/api/v1/host/workspace", json={"orgId": org["id"]})
        assert r.status_code == 409 and r.json()["detail"]["running"] == ["Ann (Builders) is working"]
        assert not seen  # nothing was asked of the computer
        r = await app_client.post("/api/v1/host/workspace", json={"orgId": org["id"], "force": True})
        assert r.json()["hostPath"] == "D:/code/app" and r.json()["restart"] is True
        assert seen[-1]["op"] == "pick_workspace" and seen[-1]["args"] == {"org": "Builders"}
        got = (await app_client.get(f"/api/v1/orgs/{org['id']}")).json()["org"]
        assert Path(got["workspace"]) == Path(mounted)
        assert got["settings"]["workspace_host"]["hostPath"] == "D:/code/app"
        assert (await rows(Agent, Agent.id == ann["id"]))[0]
    finally:
        task.cancel()
        host_svc.bridge.last_seen = 0


def _zip(files: dict[str, str], top: str = "Pantheon-main/") -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(top + name, text)
    return buf.getvalue()


def test_first_time_setup_downloads_and_configures(settings, tmp_path):
    from host.setup import Setup, SetupError

    src = tmp_path / "pantheon.zip"
    src.write_bytes(_zip({"docker-compose.yml": "services: {}", "server/app/__init__.py": "",
                          ".env.example": "PANTHEON_WEB_PORT=3710\n"}))
    target = tmp_path / "Pantheon"
    steps: list[tuple[str, str]] = []
    folder = Setup(target, say=lambda m: None, step=lambda n, s: steps.append((n, s)),
                   source=src.as_uri(), dry_run=True).run()
    assert folder == target and (target / "server/app/__init__.py").exists()
    assert (target / ".env").read_text() == "PANTHEON_WEB_PORT=3710\n"
    assert json.loads(settings.read_text())["repo"] == str(target)
    assert [n for n, s in steps if s == "done"] == ["Docker", "Pantheon", "Settings",
                                                     "Start Pantheon", "Start at login"]

    (target / ".env").write_text("MINE=1\n")  # running it again keeps your settings
    Setup(target, say=lambda m: None, source=src.as_uri(), dry_run=True).run()
    assert (target / ".env").read_text() == "MINE=1\n"

    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "notes.txt").write_text("x")
    with pytest.raises(SetupError, match="isn't empty"):
        Setup(busy, say=lambda m: None, source=src.as_uri(), dry_run=True).run()

    evil = tmp_path / "evil.zip"
    evil.write_bytes(_zip({"../../outside.txt": "x"}))
    with pytest.raises(SetupError, match="unsafe path"):
        Setup(tmp_path / "fresh", say=lambda m: None, source=evil.as_uri(), dry_run=True).run()
    assert not (tmp_path.parent / "outside.txt").exists()
