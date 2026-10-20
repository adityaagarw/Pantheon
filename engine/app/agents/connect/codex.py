"""Codex (OpenAI), connected through ``codex exec --json``.

Its JSON events become Pantheon's live view: the shell commands it runs, the patches it
applies, its reasoning (a thought cloud), its MCP calls, and its token use. Two event
formats are understood: the older ``{"id", "msg": {"type": ...}}`` and the newer
``{"type": "item.completed", "item": {...}}``.

Its conversation carries on between turns in the same folder: ``codex exec resume <id>``
where Codex has it, or the session's rollout file (``experimental_resume``) on older
versions. A usage limit parks the agent's work until it resets.

Codex can't ask for permission while running headless, so its autonomy is a sandbox:
"edits" and "ask" write only in its folder (``--full-auto``); "full" lifts the sandbox.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.agents.connect.common import (
    ConnectError,
    Proc,
    command_argv,
    is_quota,
    reset_time,
    same_place,
    text_of,
)
from app.agents.kinds import QuotaWait

_caps: dict[str, set[str]] = {}  # command -> what its exec supports ("resume", "json")


async def capabilities(argv: list[str]) -> set[str]:
    key = argv[-1]
    if key not in _caps:
        caps: set[str] = set()
        try:
            proc = await Proc.start([*argv, "exec", "--help"], Path.cwd(), stdin=False)
            text = "".join([line async for line in proc.lines()])
            await proc.wait()
            if "resume" in text:
                caps.add("resume")
            if "--json" in text:
                caps.add("json")
        except OSError:
            pass
        _caps[key] = caps
    return _caps[key]


def rollout_for(session_id: str, env: dict[str, str] | None = None) -> str | None:
    """Where an older Codex keeps a session's history (to resume it)."""
    home = Path((env or {}).get("CODEX_HOME") or os.environ.get("CODEX_HOME")
                or Path.home() / ".codex") / "sessions"
    if not session_id or not home.is_dir():
        return None
    found = sorted(home.rglob(f"rollout-*{session_id}*.jsonl"), key=lambda p: p.stat().st_mtime)
    return str(found[-1]) if found else None


async def run(turn) -> str:
    r = turn.runner
    resume = r.get("session") if same_place(r.get("session_dir"), turn.workdir) else None
    try:
        return await _attempt(turn, resume)
    except _ResumeFailed:
        await turn.remember(session=None, rollout=None)
        return await _attempt(turn, None)


class _ResumeFailed(ConnectError):
    pass


async def _attempt(turn, resume: str | None) -> str:
    r = turn.runner
    b = turn.bridge
    base = command_argv(r.get("command") or "codex")
    caps = await capabilities(base)
    tmp = Path(tempfile.mkdtemp(prefix="pantheon-"))
    last = tmp / "last.txt"
    opts = ["--skip-git-repo-check", "--color", "never", "--output-last-message", str(last),
            "-c", f"mcp_servers.pantheon.command={json.dumps(b.command)}",
            "-c", f"mcp_servers.pantheon.args={json.dumps(b.args)}",
            # Codex reads -c values as TOML: the env is an inline table.
            "-c", "mcp_servers.pantheon.env={" + ", ".join(
                f"{k} = {json.dumps(v)}" for k, v in b.env.items()) + "}"]
    if "json" in caps:
        opts.append("--json")
    opts += ["--dangerously-bypass-approvals-and-sandbox"] if r.get("autonomy") == "full" \
        else ["--full-auto"]
    if r.get("model"):
        opts += ["-m", str(r["model"])]
    if r.get("effort"):
        opts += ["-c", f"model_reasoning_effort={json.dumps(str(r['effort']))}"]
    rollout = r.get("rollout") if resume else None
    if resume and "resume" in caps:
        argv = [*base, "exec", *opts, "resume", resume, "-"]
        prompt = turn.inbox
    elif resume and rollout and Path(rollout).is_file():
        argv = [*base, "exec", *opts, "-c", f"experimental_resume={json.dumps(rollout)}", "-"]
        prompt = turn.inbox
    else:
        resume = None
        argv = [*base, "exec", *opts, "-"]
        prompt = f"{turn.fresh_context()}\n\n# Your new messages\n\n{turn.inbox}"
    try:
        proc = await Proc.start(argv, turn.workdir, turn.env)
        try:
            await proc.write(prompt)
            proc.close_stdin()
            out = await _follow(turn, proc)
            await proc.wait()
        finally:
            await proc.stop()
        final = last.read_text(encoding="utf-8", errors="replace").strip() if last.exists() else ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    final = final or out["message"]
    errors = " | ".join(out["errors"])
    if not final:
        why = errors or f"exited with {proc.returncode}: {proc.stderr}"
        if resume and ("resume" in why.lower() or "rollout" in why.lower()
                       or "not found" in why.lower()):
            raise _ResumeFailed(why)
        if is_quota(why):
            raise QuotaWait(why[:300], reset_time(why))
        hint = " (sign in again: run codex login)" if "401" in why or "token" in why.lower() else ""
        raise ConnectError(f"Codex gave no answer: {why[-1500:]}{hint}")
    if out["session"]:
        await turn.remember(session=out["session"], session_dir=str(turn.workdir),
                            rollout=rollout_for(out["session"], turn.env)
                            if "resume" not in caps else None)
    return final


async def _follow(turn, proc: Proc) -> dict[str, Any]:
    """Read its events into the live view; what it said last, its session and its errors."""
    out: dict[str, Any] = {"message": "", "session": None, "errors": []}
    async for ev in proc.events():
        if "msg" in ev and isinstance(ev["msg"], dict):
            await _old_event(turn, ev["msg"], out)
        elif isinstance(ev.get("type"), str):
            await _new_event(turn, ev, out)
    return out


async def _old_event(turn, m: dict[str, Any], out: dict[str, Any]) -> None:
    t = m.get("type")
    if t == "session_configured":
        out["session"] = m.get("session_id") or out["session"]
    elif t == "agent_message_delta":
        await turn.emit(m.get("delta") or "")
    elif t == "agent_message":
        out["message"] = str(m.get("message") or "")
    elif t in ("agent_reasoning_delta", "agent_reasoning_raw_content_delta"):
        await turn.think(m.get("delta") or "")
    elif t == "agent_reasoning" and m.get("text"):
        await turn.think(str(m["text"]) + "\n")
    elif t == "exec_command_begin":
        cmd = m.get("command")
        await turn.tool_started(str(m.get("call_id")), "shell", {
            "command": " ".join(cmd) if isinstance(cmd, list) else str(cmd), "cwd": m.get("cwd")})
    elif t == "exec_command_end":
        text = (m.get("stdout") or "") + (m.get("stderr") or "") or m.get("formatted_output") or ""
        await turn.tool_finished(str(m.get("call_id")), m.get("exit_code") == 0, str(text))
    elif t == "patch_apply_begin":
        await turn.tool_started(str(m.get("call_id")), "apply_patch",
                                {"files": list((m.get("changes") or {}).keys())})
    elif t == "patch_apply_end":
        await turn.tool_finished(str(m.get("call_id")), bool(m.get("success")),
                                 (m.get("stdout") or "") + (m.get("stderr") or ""))
    elif t == "mcp_tool_call_begin":
        inv = m.get("invocation") or {}
        if inv.get("server") != "pantheon":  # the bridge shows its own calls
            await turn.tool_started(str(m.get("call_id")), f"{inv.get('server')}.{inv.get('tool')}",
                                    inv.get("arguments") or {})
    elif t == "mcp_tool_call_end":
        result = m.get("result") or {}
        ok = "Ok" in result if isinstance(result, dict) else True
        await turn.tool_finished(str(m.get("call_id")), ok, text_of(result))
    elif t == "web_search_begin":
        await turn.tool_started(str(m.get("call_id")), "web_search", {"query": m.get("query")})
    elif t == "web_search_end":
        await turn.tool_finished(str(m.get("call_id")), True, str(m.get("query") or ""))
    elif t == "token_count":
        await _usage(turn, m.get("info", {}).get("last_token_usage") if m.get("info") else m)
    elif t == "task_complete":
        out["message"] = str(m.get("last_agent_message") or out["message"])
    elif t in ("error", "stream_error"):
        out["errors"].append(str(m.get("message") or m))


async def _new_event(turn, ev: dict[str, Any], out: dict[str, Any]) -> None:
    t = ev["type"]
    item = ev.get("item") or {}
    kind = item.get("type") or item.get("item_type")
    if t == "thread.started":
        out["session"] = ev.get("thread_id") or out["session"]
    elif t == "turn.completed":
        await _usage(turn, ev.get("usage"))
    elif t in ("turn.failed", "error"):
        err = ev.get("error") or {}
        out["errors"].append(str(err.get("message") if isinstance(err, dict) else err)
                             or str(ev.get("message") or ev))
    elif t == "item.started":
        if kind == "command_execution":
            await turn.tool_started(item.get("id", ""), "shell", {"command": item.get("command")})
        elif kind == "mcp_tool_call" and item.get("server") != "pantheon":
            await turn.tool_started(item.get("id", ""), f"{item.get('server')}.{item.get('tool')}",
                                    item.get("arguments") or {})
        elif kind == "web_search":
            await turn.tool_started(item.get("id", ""), "web_search", {"query": item.get("query")})
    elif t == "item.completed":
        if kind == "agent_message":
            out["message"] = str(item.get("text") or "")
            await turn.emit(out["message"])
        elif kind == "reasoning":
            await turn.think(str(item.get("text") or "") + "\n")
        elif kind == "command_execution":
            await turn.tool_finished(item.get("id", ""), item.get("exit_code") == 0,
                                     str(item.get("aggregated_output") or ""))
        elif kind == "file_change":
            files = [c.get("path") for c in item.get("changes") or []]
            await turn.tool_started(item.get("id", ""), "apply_patch", {"files": files})
            await turn.tool_finished(item.get("id", ""), item.get("status") != "failed",
                                     ", ".join(map(str, files)))
        elif kind == "mcp_tool_call" and item.get("server") != "pantheon":
            await turn.tool_finished(item.get("id", ""), item.get("status") != "failed",
                                     text_of(item.get("result") or item.get("error")))
        elif kind == "web_search":
            await turn.tool_finished(item.get("id", ""), True, str(item.get("query") or ""))
        elif kind == "error":
            out["errors"].append(str(item.get("message") or item))


async def _usage(turn, u: dict[str, Any] | None) -> None:
    if not isinstance(u, dict):
        return
    await turn.usage(int(u.get("input_tokens") or 0),
                     int(u.get("output_tokens") or 0) + int(u.get("reasoning_output_tokens") or 0))
