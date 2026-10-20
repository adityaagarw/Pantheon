"""Agents that speak the Agent Client Protocol (ACP): Hermes Agent (``hermes acp``), Gemini
CLI, OpenCode, Kimi, and Claude Code or Codex through their ACP adapters.

Pantheon is the client (the role an editor like Zed plays). JSON-RPC over the agent's
stdin/stdout:
- ``initialize`` says what both sides can do; ``session/new`` (or ``session/resume`` /
  ``session/load`` to carry on the agent's earlier session) gives it its folder and
  Pantheon's tools as an MCP server;
- ``session/prompt`` sends the turn's messages; the agent streams ``session/update``s:
  its text, thinking, tool calls and their progress, its plan, usage;
- ``session/request_permission`` (the agent asking us) follows the agent's autonomy: the
  user approves from their Inbox ("ask"), only edits go ("edits"), or all ("full");
- ``session/cancel`` when the user stops the turn; what the user steers with while it works
  goes in as the next prompt of the same turn.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
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

PROTOCOL = 1
SAFE = {"read", "search", "think", "fetch"}  # tool kinds that change nothing
EDITS = {"edit", "delete", "move"}


class AcpError(ConnectError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class AcpClient:
    """One ACP agent process: requests out, its requests and notifications in."""

    def __init__(self, proc: Proc,
                 on_update: Callable[[dict[str, Any]], Awaitable[None]],
                 on_request: Callable[[str, dict[str, Any]], Awaitable[Any]]):
        self.proc = proc
        self.on_update = on_update
        self.on_request = on_request
        self._next = 0
        self._waiting: dict[int, asyncio.Future] = {}
        self._tasks: set[asyncio.Task] = set()
        self._reader = asyncio.create_task(self._read())

    async def request(self, method: str, params: dict[str, Any], timeout: float | None = None
                      ) -> dict[str, Any]:
        self._next += 1
        rid = self._next
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiting[rid] = fut
        await self.proc.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            async with asyncio.timeout(timeout):
                return await fut
        finally:
            self._waiting.pop(rid, None)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self.proc.send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _read(self) -> None:
        try:
            async for msg in self.proc.events():
                if "method" in msg and "id" in msg:  # it asks us
                    task = asyncio.create_task(self._answer(msg))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
                elif "method" in msg:
                    if msg["method"] == "session/update":
                        await self.on_update((msg.get("params") or {}).get("update") or {})
                elif "id" in msg:
                    fut = self._waiting.get(msg["id"])
                    if fut is not None and not fut.done():
                        if "error" in msg:
                            err = msg["error"] or {}
                            fut.set_exception(AcpError(
                                str(err.get("message") or err) + (
                                    f": {err['data']}" if err.get("data") else ""),
                                err.get("code")))
                        else:
                            fut.set_result(msg.get("result") or {})
        finally:
            gone = AcpError(f"the agent stopped: {self.proc.stderr[-1500:]}")
            for fut in self._waiting.values():
                if not fut.done():
                    fut.set_exception(gone)

    async def _answer(self, msg: dict[str, Any]) -> None:
        try:
            result = await self.on_request(msg["method"], msg.get("params") or {})
            await self.proc.send({"jsonrpc": "2.0", "id": msg["id"], "result": result})
        except AcpError as e:
            await self.proc.send({"jsonrpc": "2.0", "id": msg["id"],
                                  "error": {"code": e.code or -32601, "message": str(e)}})

    async def close(self) -> None:
        self._reader.cancel()
        for t in list(self._tasks):
            t.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(self._reader, *self._tasks, return_exceptions=True)
        await self.proc.stop()


class Turn:
    """One Pantheon turn through an ACP agent."""

    def __init__(self, turn, autonomy: str):
        self.turn = turn
        self.autonomy = autonomy
        self.reply: list[str] = []
        self.quiet = False  # while an old session replays its history
        self.usage_seen = False

    async def update(self, u: dict[str, Any]) -> None:
        if self.quiet:
            return
        kind = u.get("sessionUpdate")
        t = self.turn
        if kind == "agent_message_chunk":
            text = text_of(u.get("content"))
            self.reply.append(text)
            await t.emit(text)
        elif kind == "agent_thought_chunk":
            await t.think(text_of(u.get("content")))
        elif kind == "tool_call":
            if _ours(u):
                return
            await t.tool_started(str(u.get("toolCallId")), _title(u), _args(u))
            if u.get("status") in ("completed", "failed"):
                await t.tool_finished(str(u.get("toolCallId")), u.get("status") == "completed",
                                      _output(u))
        elif kind == "tool_call_update":
            if u.get("status") in ("completed", "failed") and not _ours(u):
                await t.tool_finished(str(u.get("toolCallId")), u.get("status") == "completed",
                                      _output(u))
        elif kind == "plan":
            entries = u.get("entries") or []
            done = sum(1 for e in entries if e.get("status") == "completed")
            now = next((e.get("content") for e in entries if e.get("status") == "in_progress"), "")
            await t.status(f"plan {done}/{len(entries)}" + (f": {now}" if now else ""))
        elif kind == "usage_update":
            cost = u.get("cost") or {}
            if isinstance(cost, dict) and cost.get("amount"):
                self.usage_seen = True
                await t.usage(cost_usd=float(cost["amount"]))

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        if method == "session/request_permission":
            return await self.permission(params)
        raise AcpError(f"Pantheon doesn't offer {method}", -32601)

    async def permission(self, p: dict[str, Any]) -> dict[str, Any]:
        call = p.get("toolCall") or {}
        options = p.get("options") or []
        kind = str(call.get("kind") or "other")
        if _ours(call) or self.autonomy == "full" or kind in SAFE:
            ok, note = True, ""
        elif self.autonomy == "edits":
            ok, note = kind in EDITS, "this agent may only edit files in its own folder"
        else:
            ok, note = await self.turn.ask(_title(call), _args(call), _what(call))
        want = ("allow_once", "allow_always") if ok else ("reject_once", "reject_always")
        pick = next((o for w in want for o in options if o.get("kind") == w), None)
        if pick is None:
            return {"outcome": {"outcome": "cancelled"}}
        if not ok and note:
            await self.turn.status(f"denied: {note}")
        return {"outcome": {"outcome": "selected", "optionId": pick.get("optionId")}}


def _ours(u: dict[str, Any]) -> bool:
    """A call to Pantheon's own tools: the bridge shows (and approves) those itself."""
    title = str(u.get("title") or "").lower()
    raw = u.get("rawInput") if isinstance(u.get("rawInput"), dict) else {}
    return "pantheon" in title or str(raw.get("server") or "").lower() == "pantheon"


def _title(u: dict[str, Any]) -> str:
    return str(u.get("title") or u.get("kind") or "tool")[:200]


def _args(u: dict[str, Any]) -> dict[str, Any]:
    raw = u.get("rawInput")
    if isinstance(raw, dict):
        return raw
    out: dict[str, Any] = {"input": raw} if raw is not None else {}
    if u.get("locations"):
        out["files"] = [loc.get("path") for loc in u["locations"]]
    return out


def _what(u: dict[str, Any]) -> str:
    title = _title(u)
    files = [loc.get("path") for loc in u.get("locations") or [] if loc.get("path")]
    return f"{title[0].lower()}{title[1:]}" + (f" ({', '.join(files[:3])})" if files else "")


def _output(u: dict[str, Any]) -> str:
    parts = []
    for c in u.get("content") or []:
        if c.get("type") == "content":
            parts.append(text_of(c.get("content")))
        elif c.get("type") == "diff":
            parts.append(f"edited {c.get('path')}")
    if not parts and u.get("rawOutput") is not None:
        parts.append(text_of(u["rawOutput"]))
    return "\n".join(p for p in parts if p)[:20000]


async def run(turn, argv: list[str], env: dict[str, str] | None = None) -> str:
    """One turn: start the agent, open (or carry on) its session, prompt, stream, close."""
    r = turn.runner
    autonomy = str(r.get("autonomy") or "ask")
    me = Turn(turn, autonomy)
    proc = await Proc.start(argv, turn.workdir, {**turn.env, **(env or {})})
    client = AcpClient(proc, me.update, me.request)
    try:
        return await _session(turn, client, me, r, autonomy)
    except AcpError as e:
        if is_quota(str(e)):
            raise QuotaWait(str(e)[:300], reset_time(str(e))) from None
        raise
    finally:
        await client.close()


async def _session(turn, client: AcpClient, me: Turn, r: dict[str, Any], autonomy: str) -> str:
    init = await client.request("initialize", {
        "protocolVersion": PROTOCOL,
        "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                               "terminal": False},
        "clientInfo": {"name": "pantheon", "title": "Pantheon", "version": "2.0"}}, timeout=120)
    caps = init.get("agentCapabilities") or {}
    cwd = str(turn.workdir.resolve())
    servers = [turn.bridge.acp_server()]
    sid, carried = None, False
    old = r.get("session") if same_place(r.get("session_dir"), turn.workdir) else None
    if old:
        sid, carried = await _carry_on(client, me, caps, old, cwd, servers)
    if sid is None:
        res = await client.request("session/new", {"cwd": cwd, "mcpServers": servers}, timeout=120)
        sid = str(res.get("sessionId"))
        await _configure(client, sid, res, r, autonomy)
    await turn.remember(session=sid, session_dir=str(turn.workdir))
    text = turn.inbox if carried else f"{turn.fresh_context()}\n\n# Your new messages\n\n{turn.inbox}"
    answers: list[str] = []
    while text:
        me.reply = []
        prompt = asyncio.create_task(client.request("session/prompt", {
            "sessionId": sid, "prompt": [{"type": "text", "text": text}]}))
        steered: list[str] = []
        try:
            while not prompt.done():
                await asyncio.wait({prompt}, timeout=1.5)
                if prompt.done():
                    break
                if new := await turn.steering():
                    # ACP has no way into a running prompt: stop it, and the steering is the
                    # next prompt in the same session, so the agent keeps everything it did.
                    steered += new
                    with contextlib.suppress(Exception):
                        await client.notify("session/cancel", {"sessionId": sid})
            res = prompt.result()
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await client.notify("session/cancel", {"sessionId": sid})
            prompt.cancel()
            raise
        usage = res.get("usage") or {}
        if usage and not me.usage_seen:
            await turn.usage(int(usage.get("inputTokens") or usage.get("input_tokens") or 0),
                             int(usage.get("outputTokens") or usage.get("output_tokens") or 0))
        said = "".join(me.reply).strip()
        if res.get("stopReason") == "refusal" and not said:
            said = "(the agent declined this)"
        if res.get("stopReason") in ("max_tokens", "max_turn_requests") and said:
            said += "\n\n(stopped at its limit; ask me to go on)"
        if said:
            answers.append(said)
        text = "\n\n".join(steered)
        if text and res.get("stopReason") == "cancelled":
            text += "\n\n(You were stopped mid-step to read this. Carry on from where you were.)"
    if not answers:
        raise ConnectError("the agent ended its turn without a reply")
    return answers[-1]


async def _carry_on(client: AcpClient, me: Turn, caps: dict[str, Any], old: str, cwd: str,
                    servers: list[dict[str, Any]]) -> tuple[str | None, bool]:
    """Re-open the agent's earlier session: resume (no replay) where it can, else load
    (which replays the history, kept off the live view). None if it's gone."""
    params = {"sessionId": old, "cwd": cwd, "mcpServers": servers}
    try:
        if "resume" in (caps.get("sessionCapabilities") or {}):
            await client.request("session/resume", params, timeout=120)
            return old, True
        if caps.get("loadSession"):
            me.quiet = True
            try:
                await client.request("session/load", params, timeout=300)
            finally:
                me.quiet = False
            return old, True
    except (AcpError, TimeoutError):
        pass
    return None, False


async def _configure(client: AcpClient, sid: str, res: dict[str, Any], r: dict[str, Any],
                     autonomy: str) -> None:
    """The model and mode the agent's settings ask for, where the agent offers them."""
    model = str(r.get("model") or "").strip()
    models = res.get("models") or {}
    if model and models:
        ids = [m.get("modelId") for m in models.get("availableModels") or []]
        pick = model if model in ids else next((i for i in ids if i and i.endswith(model)), None)
        if pick:
            with contextlib.suppress(AcpError, TimeoutError):
                await client.request("session/set_model", {"sessionId": sid, "modelId": pick},
                                     timeout=60)
    modes = [m.get("id") for m in (res.get("modes") or {}).get("availableModes") or []]
    want = {"full": ("bypassPermissions", "dont_ask", "yolo", "full-access", "accept_edits"),
            "edits": ("accept_edits", "acceptEdits", "auto-edit"),
            "ask": ("accept_edits", "acceptEdits", "auto-edit")}.get(autonomy, ())
    pick = next((m for m in want if m in modes), None)
    if pick:
        with contextlib.suppress(AcpError, TimeoutError):
            await client.request("session/set_mode", {"sessionId": sid, "modeId": pick}, timeout=60)


async def probe(argv: list[str], cwd) -> dict[str, Any]:
    """For the Check button: does it answer ACP, and which models does it offer?"""
    proc = await Proc.start(argv, cwd)

    async def nothing(*_: Any) -> Any:
        raise AcpError("not now", -32601)

    client = AcpClient(proc, nothing, nothing)
    try:
        init = await client.request("initialize", {
            "protocolVersion": PROTOCOL,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                   "terminal": False}}, timeout=60)
        res = await client.request("session/new", {"cwd": str(cwd), "mcpServers": []}, timeout=90)
        info = init.get("agentInfo") or {}
        models = (res.get("models") or {})
        return {"agent": f"{info.get('name', '?')} {info.get('version', '')}".strip(),
                "models": [{"id": m.get("modelId"), "name": m.get("name") or m.get("modelId")}
                           for m in models.get("availableModels") or []],
                "model": models.get("currentModelId"),
                "modes": [m.get("id") for m in (res.get("modes") or {}).get("availableModes") or []]}
    finally:
        await client.close()


def argv_for(r: dict[str, Any], default: str, args: list[str]) -> list[str]:
    return command_argv(str(r.get("command") or default)) + [str(a) for a in (r.get("args") or args)]
