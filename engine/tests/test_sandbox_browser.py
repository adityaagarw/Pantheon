"""Where agents browse: blocked pages are recognised, and auto mode moves an agent to the
sandbox desktop's browser."""

from __future__ import annotations

from app.browser import sandboxed


def test_blocked_pages_are_recognised():
    assert sandboxed.blocked(200, "Just a moment...", "Checking your browser")
    assert sandboxed.blocked(403, "g2.com", "")  # a bare refusal
    assert sandboxed.blocked(200, "Example", "Please verify you are a human to continue.")
    assert sandboxed.blocked(429, "", "Too many requests")
    # A real page that happens to mention a check is fine.
    long_page = "Sign in. This site is protected by reCAPTCHA. " + "word " * 1000
    assert sandboxed.blocked(200, "Sign in", long_page) is None
    assert sandboxed.blocked(200, "News", "Today's headlines") is None


async def test_auto_moves_an_agent_and_the_setting_decides(app_client):
    assert await sandboxed.where() == "auto"
    assert await sandboxed.where_for("a1") == "builtin"
    sandboxed.move("a1")
    assert await sandboxed.where_for("a1") == "sandbox"
    assert await sandboxed.where_for("a2") == "builtin"

    r = await app_client.put("/api/v1/computer/browser", json={"where": "builtin"})
    assert r.status_code == 200 and r.json()["where"] == "builtin"
    assert await sandboxed.where_for("a1") == "builtin"  # moved agents start over
    await app_client.put("/api/v1/computer/browser", json={"where": "sandbox"})
    assert await sandboxed.where_for("a2") == "sandbox"
    assert (await app_client.put("/api/v1/computer/browser", json={"where": "x"})).status_code == 400
    await app_client.put("/api/v1/computer/browser", json={"where": "auto"})
