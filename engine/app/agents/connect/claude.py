"""Claude Code, connected both ways (the protocol Anthropic's Agent SDK speaks to it).

``claude -p --input-format stream-json --output-format stream-json`` keeps a two-way line
protocol open for the whole turn:
- in: the user's message, then anything the user steers with while it works, and our
  answers to its permission prompts (``--permission-prompt-tool stdio``);
- out: its text and thinking as they stream, each tool call and result, its rate-limit
  window, and a result per message with the turn's tokens and cost.

What it asks permission for (a shell command, a web fetch) follows the agent's autonomy:
"ask" puts it in the user's Inbox as an approval, "edits" refuses it, "full" allows all.
Pantheon's own tools (the bridge) decide their approvals themselves.

Its session carries on from turn to turn (``--resume``) in the same folder; when that
fails, it starts again with the system context and what the agent did lately.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
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

MODES = {"full": "bypassPermissions", "ask": "acceptEdits", "edits": "acceptEdits",
         "plan": "plan"}
OURS = "mcp__pantheon__"
STEER_EVERY = 1.5  # seconds between looks for the user's steering


class ResumeFailed(ConnectError):
    pass


def describe(name: str, args: dict[str, Any]) -> str:
    """What it wants to do, in words, for the approval in the user's Inbox."""
    if name in ("Bash", "PowerShell") and args.get("command"):
        return f"run `{str(args['command'])[:300]}`"
    if name in ("Write", "Edit", "MultiEdit", "NotebookEdit") and args.get("file_path"):
        return f"edit {args['file_path']}"
    if name in ("WebFetch",) and args.get("url"):
        return f"fetch {args['url']}"
    if name == "WebSearch" and args.get("query"):
        return f"search the web for “{args['query']}”"
    return f"use {name}"


async def run(turn) -> str:
    r = turn.runner
    resume = r.get("session") if same_place(r.get("session_dir"), turn.workdir) else None
    try:
        return await _attempt(turn, resume)
    except ResumeFailed:
        # Its old session is gone (cleared, or another computer): start a new one.
        await turn.remember(session=None)
        return await _attempt(turn, None)


async def _attempt(turn, resume: str | None) -> str:
    r = turn.runner
    autonomy = str(r.get("autonomy") or "ask")
    tmp = Path(tempfile.mkdtemp(prefix="pantheon-"))
    cfg = tmp / "mcp.json"
    cfg.write_text(json.dumps(turn.bridge.mcp_config()), encoding="utf-8")
    argv = command_argv(r.get("command") or "claude") + [
        "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
        "--include-partial-messages", "--replay-user-messages", "--mcp-config", str(cfg),
        "--strict-mcp-config",
        "--permission-prompt-tool", "stdio", "--permission-mode", MODES.get(autonomy, "acceptEdits")]
    if r.get("model"):
        argv += ["--model", str(r["model"])]
    if r.get("effort"):
        argv += ["--effort", str(r["effort"])]
    if r.get("max_turns"):
        argv += ["--max-turns", str(int(r["max_turns"]))]
    if resume:
        argv += ["--resume", resume]
    else:
        system = tmp / "system.md"
        system.write_text(turn.fresh_context(), encoding="utf-8")
        argv += ["--append-system-prompt-file", str(system)]
    try:
        proc = await Proc.start(argv, turn.workdir, turn.env)
        try:
            return await _converse(turn, proc, autonomy, resume)
        finally:
            await proc.stop()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def _converse(turn, proc: Proc, autonomy: str, resume: str | None) -> str:
    await proc.send({"type": "control_request", "request_id": "pantheon-init",
                     "request": {"subtype": "initialize", "hooks": None}})
    await proc.send(_user(turn.inbox))
    # A message sent while it works joins the running turn; one sent just as it finishes
    # starts another. Replays (--replay-user-messages) say when each was taken in: the turn
    # is over at a result once every steer has been.
    state: dict[str, Any] = {"answered": False, "unread": set(), "done": False,
                             "session": resume, "limit": None, "results": []}
    side: set[asyncio.Task] = set()
    # Taking the user's steering and deciding the turn is over never overlap, so nothing
    # taken is left unsent.
    gate = asyncio.Lock()

    async def take_steering() -> int:
        texts = await turn.steering()
        for text in texts:
            msg = _user(text)
            state["unread"].add(msg["uuid"])
            await proc.send(msg)
        return len(texts)

    async def steer() -> None:
        while True:
            await asyncio.sleep(STEER_EVERY)
            async with gate:
                if state["done"]:
                    return
                await take_steering()

    async def permission(rid: str, req: dict[str, Any]) -> None:
        name, args = str(req.get("tool_name") or ""), req.get("input") or {}
        if name.startswith(OURS) or autonomy == "full":
            ok, note = True, ""
        elif autonomy == "ask":
            ok, note = await turn.ask(name, args, describe(name, args))
        else:
            ok, note = False, "this agent may only edit files in its own folder"
        answer = {"behavior": "allow", "updatedInput": args} if ok else \
            {"behavior": "deny", "message": f"The user didn't allow this: {note or 'no reason'}. "
                                            "Carry on without it, or explain what you need."}
        await proc.send({"type": "control_response", "response": {
            "subtype": "success", "request_id": rid, "response": answer}})

    steering = asyncio.create_task(steer())
    try:
        async with asyncio.timeout(60 * 60 * 6):
            async for ev in proc.events():
                t = ev.get("type")
                if t in ("system", "result") and ev.get("session_id"):
                    state["session"] = ev["session_id"]
                if t == "stream_event":
                    await _stream(turn, ev.get("event") or {})
                elif t == "assistant":
                    for block in (ev.get("message") or {}).get("content") or []:
                        if block.get("type") == "tool_use" and not str(
                                block.get("name", "")).startswith(OURS):
                            await turn.tool_started(block.get("id", ""), block.get("name", ""),
                                                    block.get("input") or {})
                elif t == "user":
                    if ev.get("isReplay"):
                        state["unread"].discard(ev.get("uuid"))
                        continue
                    content = (ev.get("message") or {}).get("content")
                    for block in content if isinstance(content, list) else []:
                        if block.get("type") == "tool_result":
                            await turn.tool_finished(block.get("tool_use_id", ""),
                                                     not block.get("is_error"),
                                                     text_of(block.get("content")))
                elif t == "rate_limit_event":
                    info = ev.get("rate_limit_info") or {}
                    if info.get("status") == "rejected" and info.get("resetsAt"):
                        state["limit"] = datetime.fromtimestamp(int(info["resetsAt"]), UTC)
                elif t == "control_request":
                    req = ev.get("request") or {}
                    rid = str(ev.get("request_id") or "")
                    if req.get("subtype") == "can_use_tool":
                        task = asyncio.create_task(permission(rid, req))  # it may wait on the user
                        side.add(task)
                        task.add_done_callback(side.discard)
                    else:
                        await proc.send({"type": "control_response", "response": {
                            "subtype": "success", "request_id": rid, "response": {}}})
                elif t == "result":
                    await _result(turn, ev, state, resume)
                    async with gate:
                        if not state["unread"] and not await take_steering():
                            state["done"] = True
                            proc.close_stdin()
    finally:
        steering.cancel()
        for task in list(side):
            task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(steering, *side, return_exceptions=True)
    await proc.wait()
    results = [x for x in state["results"] if x]
    if not results:
        err = proc.stderr
        if resume and "no conversation found" in err.lower():
            raise ResumeFailed(err)
        if state["limit"] or is_quota(err):
            raise QuotaWait(err.strip()[-300:] or "usage limit", state["limit"] or reset_time(err))
        raise ConnectError(f"Claude Code gave no answer (exit {proc.returncode}): {err[-1500:]}")
    if state["session"]:
        await turn.remember(session=state["session"], session_dir=str(turn.workdir))
    return results[-1]


def _user(text: str) -> dict[str, Any]:
    # The fields the Agent SDK sends: without them a message sent mid-turn is dropped.
    return {"type": "user", "message": {"role": "user", "content": text},
            "parent_tool_use_id": None, "session_id": "default", "uuid": str(uuid.uuid4())}


async def _stream(turn, event: dict[str, Any]) -> None:
    if event.get("type") != "content_block_delta":
        return
    delta = event.get("delta") or {}
    if delta.get("type") == "text_delta":
        await turn.emit(delta.get("text") or "")
    elif delta.get("type") == "thinking_delta":
        await turn.think(delta.get("thinking") or "")


async def _result(turn, ev: dict[str, Any], state: dict[str, Any], resume: str | None) -> None:
    usage = ev.get("usage") or {}
    await turn.usage(
        int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0)
        + int(usage.get("cache_read_input_tokens") or 0),
        int(usage.get("output_tokens") or 0), float(ev.get("total_cost_usd") or 0))
    text = str(ev.get("result") or "")
    if ev.get("is_error") or str(ev.get("subtype", "")).startswith("error"):
        why = text or "; ".join(map(str, ev.get("errors") or [])) or str(ev.get("subtype"))
        if resume and "no conversation found" in why.lower():
            raise ResumeFailed(why)
        if state["limit"] or is_quota(why):
            raise QuotaWait(why[:300], state["limit"] or reset_time(why))
        if ev.get("subtype") == "error_max_turns":
            state["results"].append(text or "(stopped at the turn's step limit; ask me to go on)")
            return
        raise ConnectError(f"Claude Code: {why[:1500]}")
    if state["limit"] and is_quota(text):
        raise QuotaWait(text[:300], state["limit"])
    state["results"].append(text)
