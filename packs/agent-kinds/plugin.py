"""Agents from anywhere: two agent kinds (app/agents/kinds.py).

``cli``: a command-line agent runs each turn in the agent's own folder (its git worktree
when the organization uses them), with Pantheon's tools through the MCP bridge, connected
deeply enough to work like Pantheon's own agents (app/agents/connect): its tool calls,
thinking and cost show live; what it asks permission for waits in the user's Inbox; the
user can steer it mid-turn; its session carries on from turn to turn; a usage limit parks
its work until the limit resets.
- Claude Code: two-way stream-json, the Agent SDK's protocol (connect/claude.py).
- Codex: ``codex exec --json`` events and session resume (connect/codex.py).
- Antigravity (agy): stream-json print mode, its own home with Pantheon's plugin, and
  Inbox approvals for what headless mode refuses (connect/agy.py).
- Hermes Agent, OpenCode or any agent speaking the Agent Client Protocol (connect/acp.py).
- Any other command: the prompt on stdin (or as {prompt}), the reply on stdout.
Claude Code and Codex agents can work with an account of their own (Settings → Accounts,
services/accounts.py) instead of the computer's sign-in.

``remote``: an agent behind an HTTP API, holding its own tools and memory.
- OpenAI-compatible chat (Grok at api.x.ai, or any such endpoint), with the conversation
  kept here.
- A2A (Agent2Agent): JSON-RPC ``message/send``, continuing its context.
- Hermes Agent running as a server (``hermes gateway``, its runs API), elsewhere or in
  Docker, keeping one conversation per agent.
- A webhook: POST the message, get the reply.
API keys are organization secrets (Settings → Secrets) granted to the agent, named in the
agent's settings and only ever sent to the endpoint's host.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Any

import httpx

from app.agents.connect import acp, agy, check, claude, codex
from app.agents.connect.common import ConnectError, Proc, command_argv, is_quota, reset_time
from app.agents.kinds import QuotaWait

HISTORY = 20  # messages of an OpenAI-compatible conversation kept between turns


class KindError(RuntimeError):
    pass


# --- command-line agents --------------------------------------------------------------------

AUTONOMY = ("ask", "edits", "full")
CLI_PRESETS = [
    {"id": "claude-code", "label": "Claude Code",
     "description": "Anthropic's coding agent, signed in on this computer or with an account of "
                    "its own (Settings → Accounts). Its tools, thinking "
                    "and cost show live, it asks you before risky actions, you can steer it "
                    "mid-turn, and it remembers the conversation.",
     "runner": {"preset": "claude-code", "command": "claude", "model": "", "autonomy": "ask"}},
    {"id": "codex", "label": "Codex",
     "description": "OpenAI's coding agent, signed in on this computer or with an account of its "
                    "own (Settings → Accounts). Its commands, edits and "
                    "reasoning show live, and it remembers the conversation.",
     "runner": {"preset": "codex", "command": "codex", "model": "", "autonomy": "edits"}},
    {"id": "hermes", "label": "Hermes Agent",
     "description": "Nous Research's agent with its own memory, skills and 30+ tools, through "
                    "the Agent Client Protocol: live tool calls, your approvals, its sessions "
                    "carried on, and Pantheon's tools.",
     "runner": {"preset": "hermes", "command": "hermes", "model": "", "autonomy": "ask"}},
    {"id": "agy", "label": "Antigravity (agy)",
     "description": "Google's Antigravity CLI (Gemini CLI's successor), signed in on this "
                    "computer: Gemini, Claude and GPT-OSS models. Its steps and text show "
                    "live, what it needs permission for comes to your Inbox, you can steer "
                    "it, and it remembers the conversation.",
     "runner": {"preset": "agy", "command": "agy", "model": "", "autonomy": "ask"}},
    {"id": "opencode", "label": "OpenCode",
     "description": "The open-source coding agent (any model provider), through the Agent "
                    "Client Protocol.",
     "runner": {"preset": "opencode", "command": "opencode", "model": "", "autonomy": "ask"}},
    {"id": "acp", "label": "Any ACP agent",
     "description": "Any agent that speaks the Agent Client Protocol (as Zed and other editors "
                    "use): Kimi, Goose, Claude Code or Codex through their ACP adapters, your "
                    "own.",
     "runner": {"preset": "acp", "command": "", "args": [], "autonomy": "ask"}},
    {"id": "custom", "label": "Any command",
     "description": "Any program that takes a prompt (on stdin, or as {prompt} in its "
                    "arguments) and prints its answer.",
     "runner": {"preset": "custom", "command": "", "args": []}},
]
DEEP = ["claude-code", "codex", "agy", "hermes", "opencode", "acp"]
ACCOUNTS = ["claude-code", "codex"]  # services/accounts.py
CLI_FIELDS = [
    {"key": "command", "label": "Command", "type": "text",
     "hint": "The program, as you'd type it (found on PATH), or its full path"},
    {"key": "account", "label": "Account", "type": "account", "presets": ACCOUNTS,
     "hint": "Empty: what it's signed in to on this computer. Add accounts in Settings → "
             "Accounts"},
    {"key": "fallback_account", "label": "While that account is at its limit, use",
     "type": "account", "presets": ACCOUNTS,
     "hint": "Empty: wait for the limit to reset"},
    {"key": "model", "label": "Model", "type": "model",
     "hint": "Leave empty for its default (Check lists the ones it offers)", "presets": DEEP},
    {"key": "autonomy", "label": "What it may do on its own", "type": "select",
     "options": [["ask", "Edit in its folder; ask me (in my Inbox) before anything riskier"],
                 ["edits", "Edit in its folder; refuse anything riskier"],
                 ["full", "Anything, without asking (shell commands included)"]],
     "presets": DEEP},
    {"key": "effort", "label": "Effort", "type": "select",
     "options": [["", "Its default"], ["low", "Low"], ["medium", "Medium"], ["high", "High"],
                 ["xhigh", "Extra high"], ["max", "Max"]],
     "presets": ["claude-code", "codex"]},
    {"key": "effort", "label": "Effort", "type": "select",
     "options": [["", "Its default"], ["low", "Low"], ["medium", "Medium"], ["high", "High"]],
     "presets": ["agy"]},
    {"key": "max_turns", "label": "Most steps in one turn", "type": "text",
     "hint": "Empty: no limit", "presets": ["claude-code"]},
    {"key": "args", "label": "Arguments", "type": "list",
     "hint": "{prompt} is the message, {workdir} the agent's folder; without {prompt} the "
             "message goes to stdin", "presets": ["custom"]},
    {"key": "args", "label": "Arguments", "type": "list",
     "hint": "What starts it in ACP mode, e.g. acp or --acp", "presets": ["acp"]},
]
DEFAULT_COMMAND = {p["id"]: p["runner"].get("command", "") for p in CLI_PRESETS}


def _validate_cli(r: dict[str, Any]) -> dict[str, Any]:
    preset = r.get("preset") or "custom"
    if preset not in CLI:  # the presets, and older ones agents may still have (gemini)
        raise ValueError(f"no CLI preset '{preset}'")
    if preset not in ACCOUNTS:
        r.pop("account", None)
        r.pop("fallback_account", None)
    if r.get("fallback_account") and r.get("fallback_account") == r.get("account"):
        r["fallback_account"] = ""
    if not str(r.get("command") or "").strip():
        raise ValueError("give the command that runs the agent")
    if not isinstance(r.get("args", []), list):
        raise ValueError("args must be a list")
    if r.get("autonomy") and r["autonomy"] not in AUTONOMY:
        raise ValueError(f"autonomy must be one of {', '.join(AUTONOMY)}")
    if str(r.get("max_turns") or "").strip():
        try:
            r["max_turns"] = max(1, int(r["max_turns"]))
        except (TypeError, ValueError):
            raise ValueError("most steps in one turn must be a whole number") from None
    else:
        r.pop("max_turns", None)
    return r


def _prompt(turn) -> str:
    return f"{turn.fresh_context()}\n\n# Your new messages\n\n{turn.inbox}"


async def _hermes(turn) -> str:
    try:
        return await acp.run(turn, acp.argv_for(turn.runner, "hermes", ["acp"]))
    except ConnectError as e:
        if "invalid choice" not in str(e):
            raise
    return await _hermes_oneshot(turn)  # a Hermes from before it spoke ACP


async def _hermes_oneshot(turn) -> str:
    r = turn.runner
    argv = command_argv(r.get("command") or "hermes") + ["-z", _prompt(turn), "--in",
                                                         str(turn.workdir)]
    if r.get("model"):
        argv += ["-m", r["model"]]
    if r.get("autonomy") == "full":
        argv += ["--yolo"]
    return await _stdout_of(turn, argv, None, None)


async def _custom(turn) -> str:
    r = turn.runner
    args = [str(a) for a in r.get("args") or []]
    uses_prompt = any("{prompt}" in a for a in args)
    fill = {"{prompt}": _prompt(turn), "{workdir}": str(turn.workdir)}
    for k, v in fill.items():
        args = [a.replace(k, v) for a in args]
    argv = [*command_argv(str(r.get("command") or "")), *args]
    return await _stdout_of(turn, argv, None if uses_prompt else _prompt(turn), turn.bridge.env)


async def _stdout_of(turn, argv: list[str], stdin: str | None, env: dict[str, str] | None) -> str:
    """Run it; what it prints is the reply (streamed as it comes)."""
    proc = await Proc.start(argv, turn.workdir, env, stdin=stdin is not None)
    try:
        if stdin is not None:
            await proc.write(stdin)
            proc.close_stdin()
        out = []
        async for line in proc.lines():
            out.append(line)
            await turn.emit(line)
        code = await proc.wait()
    finally:
        await proc.stop()
    if code not in (0, None):
        why = proc.stderr
        if is_quota(why):
            raise QuotaWait(why.strip()[-300:], reset_time(why))
        raise KindError(f"{argv[0]} exited with {code}: {why}")
    return "".join(out).strip()


def _acp(default: str):
    async def run(turn) -> str:
        return await acp.run(turn, acp.argv_for(turn.runner, default, check.ACP_PRESETS[
            turn.runner.get("preset") or "acp"]))
    return run


CLI = {
    "claude-code": claude.run,
    "codex": codex.run,
    "agy": agy.run,
    "hermes": _hermes,
    "gemini": _acp("gemini"),  # Gemini CLI, retired for agy; agents made with it still run
    "opencode": _acp("opencode"),
    "acp": _acp(""),
    "custom": _custom,
}


async def run_cli(turn) -> str:
    return await CLI[turn.runner.get("preset") or "custom"](turn)


async def check_cli(runner: dict[str, Any]) -> dict[str, Any]:
    from app.services import accounts

    env = await accounts.env_for_runner(runner) if runner.get("preset") in ACCOUNTS else {}
    return await check.check(runner, {**DEFAULT_COMMAND, "gemini": "gemini"}, env)


# --- agents behind an HTTP API --------------------------------------------------------------

REMOTE_PRESETS = [
    {"id": "grok", "label": "Grok (xAI)",
     "description": "xAI's Grok through its OpenAI-compatible API. Needs an xAI API key as "
                    "an organization secret.",
     "runner": {"preset": "grok", "protocol": "openai", "url": "https://api.x.ai/v1",
                "model": "grok-4", "secret": "XAI_API_KEY"}},
    {"id": "openai", "label": "OpenAI-compatible agent",
     "description": "Any agent or model server speaking the OpenAI chat API.",
     "runner": {"preset": "openai", "protocol": "openai", "url": "", "model": "", "secret": ""}},
    {"id": "hermes-server", "label": "Hermes on a server",
     "description": "Hermes Agent already running as a server (hermes gateway with its API "
                    "server on), on this computer, another one or in Docker. It keeps one "
                    "conversation per agent. Its API key is an organization secret.",
     "runner": {"preset": "hermes-server", "protocol": "hermes", "url": "http://127.0.0.1:8642",
                "secret": "HERMES_API_KEY"}},
    {"id": "a2a", "label": "A2A agent",
     "description": "An agent speaking the Agent2Agent protocol (JSON-RPC message/send).",
     "runner": {"preset": "a2a", "protocol": "a2a", "url": "", "secret": ""}},
    {"id": "webhook", "label": "Webhook",
     "description": "POSTs {agent, organization, message, system} as JSON; the reply is the "
                    "response's \"reply\" (or its text).",
     "runner": {"preset": "webhook", "protocol": "webhook", "url": "", "secret": ""}},
]
REMOTE_FIELDS = [
    {"key": "url", "label": "Endpoint URL", "type": "text",
     "hint": "For OpenAI-compatible: the base URL (…/v1). For Hermes: its API server."},
    {"key": "model", "label": "Model", "type": "text", "presets": ["grok", "openai"]},
    {"key": "secret", "label": "API key secret", "type": "secret",
     "hint": "The name of an organization secret granted to this agent (empty: no key)"},
]


def _validate_remote(r: dict[str, Any]) -> dict[str, Any]:
    if r.get("protocol") not in ("openai", "a2a", "webhook", "hermes"):
        raise ValueError("protocol must be openai, a2a, hermes or webhook")
    url = str(r.get("url") or "").strip()
    if url and not url.startswith(("http://", "https://")):
        raise ValueError("the endpoint must be an http(s) URL")
    return {**r, "url": url}


async def _headers(turn, url: str) -> dict[str, str]:
    from app.services import secrets

    name = str(turn.runner.get("secret") or "").strip().upper()
    if not name:
        return {}
    value = await secrets.resolve(turn.org.id, turn.agent.id, f"{{{{secret:{name}}}}}",
                                  url=url, tool=f"{turn.agent.name} (remote agent)")
    return {"Authorization": f"Bearer {value}"}


async def _openai(turn, client: httpx.AsyncClient, url: str) -> str:
    r = turn.runner
    history = list(r.get("history") or [])
    messages = [{"role": "system", "content": turn.system}, *history,
                {"role": "user", "content": turn.inbox}]
    endpoint = url.rstrip("/") + "/chat/completions"
    body: dict[str, Any] = {"messages": messages, "stream": True,
                            "stream_options": {"include_usage": True}}
    if r.get("model"):
        body["model"] = r["model"]
    out = []
    async with client.stream("POST", endpoint, json=body, headers=await _headers(turn, endpoint)) as resp:
        if resp.status_code >= 400:
            text = (await resp.aread())[:500].decode("utf-8", "replace")
            if resp.status_code == 429:
                wait = resp.headers.get("retry-after", "")
                raise QuotaWait(f"{endpoint} answered 429: {text}",
                                reset_time(f"in {wait}s" if wait.isdigit() else text))
            raise KindError(f"{endpoint} answered {resp.status_code}: {text}")
        async for line in resp.aiter_lines():
            if not line.startswith("data:") or line.strip() == "data: [DONE]":
                continue
            try:
                chunk = json.loads(line[5:])
            except json.JSONDecodeError:
                continue
            if chunk.get("usage"):
                u = chunk["usage"]
                await turn.usage(int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0))
            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            if delta.get("reasoning_content") or delta.get("reasoning"):
                await turn.think(str(delta.get("reasoning_content") or delta.get("reasoning")))
            if delta.get("content"):
                out.append(delta["content"])
                await turn.emit(delta["content"])
    reply = "".join(out).strip()
    history += [{"role": "user", "content": turn.inbox}, {"role": "assistant", "content": reply}]
    await turn.remember(history=history[-HISTORY:])
    return reply


def _a2a_text(result: dict[str, Any]) -> str:
    """The text of an A2A result: a Message's parts, or a Task's artifacts/status message."""
    parts: list[dict[str, Any]] = list(result.get("parts") or [])
    for art in result.get("artifacts") or []:
        parts += art.get("parts") or []
    if not parts and (msg := (result.get("status") or {}).get("message")):
        parts = msg.get("parts") or []
    return "\n".join(str(p.get("text")) for p in parts if p.get("kind", p.get("type")) == "text"
                     and p.get("text")).strip()


async def _a2a(turn, client: httpx.AsyncClient, url: str) -> str:
    r = turn.runner
    message: dict[str, Any] = {"role": "user", "kind": "message", "messageId": uuid.uuid4().hex,
                               "parts": [{"kind": "text", "text": f"{turn.system}\n\n{turn.inbox}"
                                          if not r.get("context") else turn.inbox}]}
    if r.get("context"):
        message["contextId"] = r["context"]
    rpc = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "message/send",
           "params": {"message": message}}
    resp = await client.post(url, json=rpc, headers=await _headers(turn, url))
    if resp.status_code >= 400:
        raise KindError(f"{url} answered {resp.status_code}: {resp.text[:500]}")
    body = resp.json()
    if body.get("error"):
        raise KindError(f"the A2A agent said: {body['error'].get('message') or body['error']}")
    result = body.get("result") or {}
    if ctx := result.get("contextId"):
        await turn.remember(context=ctx)
    reply = _a2a_text(result)
    await turn.emit(reply)
    return reply


HERMES_DONE = {"completed", "failed", "error", "cancelled", "canceled", "stopped", "interrupted"}


async def _hermes_server(turn, client: httpx.AsyncClient, url: str) -> str:
    """Hermes' runs API: start a run in the agent's own conversation (its session key), follow
    its events, and stop it if the user stops the turn."""
    base = url.rstrip("/")
    first = not turn.runner.get("hermes_session")
    # Its conversation: kept until the agent's context is cleared, then a new one.
    key = turn.runner.get("hermes_session") or f"pantheon-{turn.agent.id}-{uuid.uuid4().hex[:8]}"
    headers = {**await _headers(turn, base), "X-Hermes-Session-Key": key,
               "Idempotency-Key": turn.turn_id}
    body = {"input": _prompt(turn) if first else turn.inbox, "session_id": key,
            "instructions": "You are a member of a Pantheon organization; answer as asked."}
    resp = await client.post(f"{base}/v1/runs", json=body, headers=headers)
    if resp.status_code == 429:
        raise QuotaWait(f"Hermes answered 429: {resp.text[:300]}", reset_time(resp.text))
    if resp.status_code >= 400:
        raise KindError(f"Hermes answered {resp.status_code}: {resp.text[:500]}")
    created = resp.json() if resp.content else {}
    run_id = created.get("run_id") or created.get("runId") or created.get("id")
    if not run_id:
        raise KindError("Hermes didn't say which run it started")
    if first:
        await turn.remember(hermes_session=key)
    out: list[str] = []
    final: dict[str, Any] = {}
    try:
        async with client.stream("GET", f"{base}/v1/runs/{run_id}/events",
                                 headers={**headers, "Accept": "text/event-stream"}) as ev:
            name = None
            async for line in ev.aiter_lines():
                if line.startswith("event:"):
                    name = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                try:
                    data = json.loads(line[5:])
                except json.JSONDecodeError:
                    data = {"text": line[5:].strip()}
                event = str(data.get("event") or data.get("type") or name or "")
                delta = data.get("delta") or data.get("text_delta")
                if event == "message.delta" and delta:
                    out.append(str(delta))
                    await turn.emit(str(delta))
                elif event.startswith("reasoning") and delta:
                    await turn.think(str(delta))
                elif event in ("tool.started", "tool.start", "tool_call.started"):
                    await turn.tool_started(str(data.get("id") or data.get("call_id")),
                                            str(data.get("name") or data.get("tool") or "tool"),
                                            data.get("arguments") or data.get("args") or {})
                elif event in ("tool.completed", "tool.finished", "tool_call.completed"):
                    await turn.tool_finished(str(data.get("id") or data.get("call_id")),
                                             data.get("status") not in ("failed", "error"),
                                             str(data.get("output") or data.get("result") or ""))
                status = str(data.get("status") or (event[4:] if event.startswith("run.") else ""))
                if status.lower() in HERMES_DONE:
                    final = {**data, "status": status.lower()}
                    break
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await asyncio.shield(client.post(f"{base}/v1/runs/{run_id}/stop", headers=headers))
        raise
    if not final:  # the stream ended early: ask how the run went
        final = (await client.get(f"{base}/v1/runs/{run_id}", headers=headers)).json()
    status = str(final.get("status") or "").lower()
    text = "".join(out).strip() or str(final.get("output") or final.get("result") or
                                        final.get("text") or "").strip()
    if status in ("failed", "error"):
        why = str(final.get("error") or final.get("message") or text or "the run failed")
        if is_quota(why):
            raise QuotaWait(why[:300], reset_time(why))
        raise KindError(f"Hermes: {why[:1000]}")
    return text


async def _webhook(turn, client: httpx.AsyncClient, url: str) -> str:
    resp = await client.post(url, headers=await _headers(turn, url), json={
        "agent": {"id": turn.agent.id, "name": turn.agent.name, "role": turn.agent.role},
        "organization": {"id": turn.org.id, "name": turn.org.name},
        "message": turn.inbox, "system": turn.system, "turnId": turn.turn_id})
    if resp.status_code >= 400:
        raise KindError(f"{url} answered {resp.status_code}: {resp.text[:500]}")
    try:
        body = resp.json()
        reply = str(body.get("reply") or body.get("text") or "") if isinstance(body, dict) else str(body)
    except json.JSONDecodeError:
        reply = resp.text
    await turn.emit(reply)
    return reply.strip()


REMOTE = {"openai": _openai, "a2a": _a2a, "webhook": _webhook, "hermes": _hermes_server}


async def run_remote(turn) -> str:
    url = str(turn.runner.get("url") or "").strip()
    if not url:
        raise KindError("this agent has no endpoint URL yet (its Kind settings)")
    async with httpx.AsyncClient(timeout=httpx.Timeout(None if turn.runner.get(
            "protocol") == "hermes" else 600, connect=15)) as client:
        return await REMOTE[turn.runner.get("protocol") or "openai"](turn, client, url)


def register(api) -> None:
    api.agent_kind("cli", "Command-line agent",
                   "Claude Code, Codex, Antigravity, Hermes Agent, OpenCode or any agent that "
                   "speaks ACP, working in its own folder with Pantheon's tools, live and "
                   "steerable.",
                   run_cli, presets=CLI_PRESETS, fields=CLI_FIELDS, validate=_validate_cli,
                   check=check_cli)
    api.agent_kind("remote", "Remote agent",
                   "An agent behind an HTTP API: Grok and other OpenAI-compatible endpoints, "
                   "Hermes on a server, A2A agents, webhooks.",
                   run_remote, presets=REMOTE_PRESETS, fields=REMOTE_FIELDS,
                   validate=_validate_remote)
