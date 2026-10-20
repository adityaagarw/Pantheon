"""edit_file: the misses that cost agents a round trip each (a quarter of edits in a timed
run) are tolerated where safe, and explained where not."""

from __future__ import annotations

import sys

import pytest

from app.models import Agent, Org
from app.tools import base, resolve  # noqa: F401  (registers the built-in tools)
from app.tools.base import ToolContext, ToolError, agent_workdir
from tests.helpers import make_agent, make_org, rows

edit = base.builtin("edit_file").handler
read = base.builtin("read_file").handler


async def _ctx(app_client):
    org = await make_org(app_client)
    agent = await make_agent(app_client, org["id"], "Ann")
    o = (await rows(Org, Org.id == org["id"]))[0]
    a = (await rows(Agent, Agent.id == agent["id"]))[0]
    ctx = ToolContext(org=o, agent=a, turn_id=None, tool_call_id="t", depth=0)
    return ctx, agent_workdir(o, a)


PAGE = "<style>\n  .a { color: red; }\n  .b { color: blue; }\n</style>\n<p>Book “now”</p>\n"


async def test_several_changes_in_one_call_and_typographic_quotes_tolerated(app_client, workspace_root):
    ctx, wd = await _ctx(app_client)
    f = wd / "page.html"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(PAGE, encoding="utf-8")
    out = await edit(
        {"path": "page.html", "edits": [
            {"old": ".a { color: red; }", "new": ".a { color: green; }"},
            {"old": '<p>Book "now"</p>', "new": "<p>Book today</p>"},  # straight quotes
        ]}, ctx)
    assert "2 changes" in out
    assert f.read_text(encoding="utf-8") == PAGE.replace("red", "green").replace(
        "<p>Book “now”</p>", "<p>Book today</p>")


async def test_a_miss_shows_the_closest_passage(app_client, workspace_root):
    ctx, wd = await _ctx(app_client)
    (wd / "page.html").write_text(PAGE, encoding="utf-8")
    with pytest.raises(ToolError) as e:
        await edit({"path": "page.html", "old": ".b { colour: blue; }", "new": "x"}, ctx)
    assert "closest passage" in str(e.value) and ".b { color: blue; }" in str(e.value)
    assert (wd / "page.html").read_text(encoding="utf-8") == PAGE  # nothing written


async def test_a_miss_on_a_file_changed_since_it_was_read_says_so(app_client, workspace_root):
    # All five failed edits of a timed run were on files a colleague had changed since.
    ctx, wd = await _ctx(app_client)
    f = wd / "page.html"
    f.write_text(PAGE, encoding="utf-8")
    with pytest.raises(ToolError) as e:
        await edit({"path": "page.html", "old": ".c { margin: 0; }", "new": "x"}, ctx)
    assert "You haven't read page.html" in str(e.value)
    await read({"path": "page.html"}, ctx)
    f.write_text(PAGE.replace(".b", ".c"), encoding="utf-8")  # a colleague's change
    with pytest.raises(ToolError) as e:
        await edit({"path": "page.html", "old": ".b { colour: blue; }", "new": "x"}, ctx)
    assert "changed since you last read it" in str(e.value)
    await read({"path": "page.html"}, ctx)
    with pytest.raises(ToolError) as e:  # read and still wrong: the closest passage only
        await edit({"path": "page.html", "old": ".zz { colour: blue; }", "new": "x"}, ctx)
    assert "changed since" not in str(e.value) and "closest passage" in str(e.value)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shells")
async def test_a_command_runs_in_the_shell_it_is_written_for(app_client, workspace_root):
    """Agents mixed cmd and PowerShell (`&&` in PowerShell, `@echo off` in a script
    PowerShell ran): a command now runs in the shell it's written for and says which, and a
    syntax error from the other shell says how to write it."""
    from app.tools.workspace import windows_shell

    assert windows_shell("node a.js && del a.js") == "cmd"
    assert windows_shell("Get-ChildItem -Name | Select-Object -First 3") == "powershell"
    assert windows_shell("$x = 3; Write-Output $x") == "powershell"
    assert windows_shell('python -c "\nprint(1)\n"') == "powershell"
    assert windows_shell('@echo off\nset "WS=%CD%"\necho %WS%') == "batch"
    ctx, _wd = await _ctx(app_client)
    run = base.builtin("run_command").handler
    out = await run({"command": '@echo off\nset "N=7"\necho n is %N%'}, ctx)
    assert out.startswith("exit code: 0 (ran in cmd.exe as a .cmd file)") and "n is 7" in out
    out = await run({"command": "$n = 6; Write-Output \"n is $n\""}, ctx)
    assert "(ran in powershell)" in out and "n is 6" in out
    out = await run({"command": "echo a\nnode -e 1 && echo b"}, ctx)
    assert "another shell" in out and "instead of &&" in out


async def test_shell_commands_that_only_read_files_are_refused(app_client, workspace_root):
    from app.tools.workspace import only_reads_files

    assert only_reads_files("findstr /n tabbar schedule.html") == "search_files"
    assert only_reads_files("@echo off\ntype a.html\ndir /b") == "read_file / list_dir"
    assert only_reads_files("cat a | grep b") == "read_file / search_files"
    for works in ("dir dist && npm run build", "type x | node check.js", "git log | findstr x",
                  "node check.js", "echo hi"):
        assert only_reads_files(works) == "", works
    ctx, _wd = await _ctx(app_client)
    with pytest.raises(ToolError) as e:
        await base.builtin("run_command").handler({"command": "dir /b"}, ctx)
    assert "use list_dir" in str(e.value)


async def test_line_edits_need_the_current_version(app_client, workspace_root):
    ctx, wd = await _ctx(app_client)
    f = wd / "page.html"
    f.write_text(PAGE, encoding="utf-8")
    head = (await read({"path": "page.html"}, ctx)).splitlines()[0]
    version = head.rsplit("version ", 1)[1]
    out = await edit({"path": "page.html", "version": version,
                      "edits": [{"lines": "2-3", "new": "  .a, .b { color: black; }"}]}, ctx)
    assert f.read_text(encoding="utf-8").splitlines()[1:3] == ["  .a, .b { color: black; }", "</style>"]
    # The old version no longer matches, and those lines changed since: refused.
    with pytest.raises(ToolError, match="current version"):
        await edit({"path": "page.html", "version": version,
                    "edits": [{"lines": "2-3", "new": "x"}]}, ctx)
    assert out.rsplit("version ", 1)[1].rstrip(".") != version


async def test_a_line_edit_by_an_older_version_lands_where_its_lines_moved(app_client,
                                                                          workspace_root):
    ctx, wd = await _ctx(app_client)
    f = wd / "page.html"
    f.write_text(CSS, encoding="utf-8")
    version = (await read({"path": "page.html"}, ctx)).splitlines()[0].rsplit("version ", 1)[1]
    # The agent adds two lines at the top, then edits by the numbers it read before.
    await edit({"path": "page.html", "old": "<style>", "new": "<!-- a -->\n<!-- b -->\n<style>"}, ctx)
    out = await edit({"path": "page.html", "version": version,
                      "edits": [{"lines": "5", "new": "  .card { padding: 20px; }"}]}, ctx)
    assert "are lines 7-7 now" in out
    assert f.read_text(encoding="utf-8").splitlines()[6] == "  .card { padding: 20px; }"


CSS = ("<style>\n  .row { display: grid; gap: 12px; }\n  .row .title { font-weight: 600; }\n"
       "  .row .meta { color: var(--ink-60); }\n  .card { padding: 16px; }\n</style>\n")


async def test_a_near_exact_passage_is_changed_and_the_difference_reported(app_client,
                                                                          workspace_root):
    ctx, wd = await _ctx(app_client)
    f = wd / "page.html"
    f.write_text(CSS, encoding="utf-8")
    # A stale copy: the agent remembers 600 as 700.
    out = await edit({"path": "page.html",
                      "old": "  .row { display: grid; gap: 12px; }\n  .row .title { font-weight: 700; }",
                      "new": "  .row { display: flex; }\n  .row .title { font-weight: 500; }"}, ctx)
    assert "not exactly" in out and "font-weight: 700" in out and "font-weight: 600" in out
    assert f.read_text(encoding="utf-8") == CSS.replace(
        "  .row { display: grid; gap: 12px; }\n  .row .title { font-weight: 600; }",
        "  .row { display: flex; }\n  .row .title { font-weight: 500; }")


async def test_the_changes_that_match_go_in_and_only_the_misses_come_back(app_client,
                                                                         workspace_root):
    ctx, wd = await _ctx(app_client)
    f = wd / "page.html"
    f.write_text(CSS, encoding="utf-8")
    out = await edit({"path": "page.html", "edits": [
        {"old": ".card { padding: 16px; }", "new": ".card { padding: 24px; }"},
        {"old": ".nothing { like: this; }", "new": "x"}]}, ctx)
    assert "1 change" in out and "NOT applied (1 of 2" in out and "edits[1]" in out
    assert ".card { padding: 24px; }" in f.read_text(encoding="utf-8")
    # Sending the whole batch again: the applied one is recognised, not failed.
    out = await edit({"path": "page.html", "edits": [
        {"old": ".card { padding: 16px; }", "new": ".card { padding: 24px; }"},
        {"old": ".row .meta { color: var(--ink-60); }",
         "new": ".row .meta { color: var(--ink-70); }"}]}, ctx)
    assert "already in the file" in out and "NOT applied" not in out
    assert "--ink-70" in f.read_text(encoding="utf-8")


async def test_overlapping_changes_are_refused(app_client, workspace_root):
    ctx, wd = await _ctx(app_client)
    (wd / "page.html").write_text(PAGE, encoding="utf-8")
    with pytest.raises(ToolError, match="overlap"):
        await edit({"path": "page.html", "edits": [
            {"old": ".a { color: red; }\n  .b", "new": "x"},
            {"old": ".b { color: blue; }", "new": "y"}]}, ctx)


async def test_edits_sent_as_a_json_string_are_taken_as_meant(app_client, workspace_root):
    import json

    from app.agents.graph import execute  # the same path a model's call takes
    from app.tools.resolve import resolve_tools

    ctx, wd = await _ctx(app_client)
    (wd / "page.html").write_text(CSS, encoding="utf-8")
    t = (await resolve_tools(ctx.org, ctx.agent)).tools["edit_file"]
    out, ok = await execute(t, {"path": "page.html", "edits": json.dumps(
        [{"old": ".card { padding: 16px; }", "new": ".card { padding: 8px; }"}])}, ctx)
    assert ok, out
    assert ".card { padding: 8px; }" in (wd / "page.html").read_text(encoding="utf-8")


async def test_reading_an_image_shows_it(app_client, workspace_root):
    from PIL import Image

    ctx, wd = await _ctx(app_client)
    (wd / "shots").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (3200, 900), "white").save(wd / "shots" / "home-1440.png")
    out = await read({"path": "shots/home-1440.png"}, ctx)
    assert "image 3200×900 (shown at 50%)" in out
    assert out.images and out.images[0].startswith("data:image/jpeg;base64,")


async def test_search_files_in_one_file_and_by_a_path_pattern(app_client, workspace_root):
    """Half the searches of a timed run came back "No matches." for text that was there: a
    search in one file searched nothing, and a glob with a folder never matched."""
    search = base.builtin("search_files").handler
    ctx, wd = await _ctx(app_client)
    (wd / "parts").mkdir(parents=True, exist_ok=True)
    (wd / "my-bookings.html").write_text("<a class=brand>Samapti</a>\n", encoding="utf-8")
    (wd / "parts" / "base.css").write_text(".week { display: grid }\n", encoding="utf-8")
    (wd / "DESIGN.md").write_text("## Tokens\n.week rules\n", encoding="utf-8")
    assert "my-bookings.html:1:" in await search({"pattern": "brand", "path": "my-bookings.html"}, ctx)
    assert "parts/base.css:1:" in await search({"pattern": "week", "glob": "parts/*.css"}, ctx)
    found = await search({"pattern": "week", "glob": "*.{css,md}"}, ctx)
    assert "parts/base.css:1:" in found and "DESIGN.md:2:" in found
    assert await search({"pattern": "nowhere", "path": "DESIGN.md"}, ctx) == "No matches in DESIGN.md."
    assert await search({"pattern": "nowhere", "glob": "*.css"}, ctx) == \
        "No matches in 1 files under . matching *.css."
    with pytest.raises(ToolError, match="no such file or folder"):
        await search({"pattern": "x", "path": "missing.html"}, ctx)
