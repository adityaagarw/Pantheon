"""Antigravity CLI (``agy``, Google's successor to Gemini CLI), connected through its
stream-json print mode.

``agy -p --input-format stream-json --output-format stream-json`` takes one message per
line and runs a turn for each; out come ``step_update`` events (its text as it streams,
each tool step as it starts and ends) and a ``result`` per message with its tokens and
the actions headless mode refused.

Each agent gets its own agy home (HOME pointed at a folder Pantheon keeps), holding:
- a ``pantheon`` plugin: Pantheon's tools (the MCP bridge) and the agent's context as an
  always-on rule (agy has no system-prompt flag);
- its settings: the allow rules (Pantheon's tools, and what the user approved) and its
  conversations, so the next turn carries on with ``--conversation``.
Its sign-in stays the computer's (agy keeps it in the OS keychain, not in the home).

Approvals: headless agy refuses anything that needs permission (a shell command, a file
outside its folder) and says exactly what. With autonomy "ask", each refused action goes
to the user's Inbox; what they approve becomes an exact allow rule, and the conversation
carries on so the agent does it. "edits" leaves them refused; "full" skips agy's
permission checks altogether (the user's choice).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from pathlib import Path
from typing import Any

from app.agents.connect.common import ConnectError, Proc, command_argv, is_quota, reset_time
from app.agents.kinds import QuotaWait
from app.core.config import settings

PLUGIN = "pantheon"
BRIDGE_RULE = f"mcp({PLUGIN}_pantheon/*)"  # a plugin's MCP server is named <plugin>_<server>
STEER_EVERY = 1.5
ROUNDS = 3  # approval rounds in one turn: asked, approved, carried on


def home_for(agent_id: str) -> Path:
    return settings.data_path / "agent-homes" / agent_id / "agy"


def _settings_path(home: Path) -> Path:
    return home / ".gemini" / "antigravity-cli" / "settings.json"


def _read_settings(home: Path) -> dict[str, Any]:
    try:
        data = json.loads(_settings_path(home).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def allow(home: Path, rules: list[str]) -> None:
    """Add allow rules to the agent's agy settings (read when agy starts)."""
    data = _read_settings(home)
    perms = data.setdefault("permissions", {})
    have = list(perms.get("allow") or [])
    perms["allow"] = have + [r for r in rules if r not in have]
    path = _settings_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def prepare(turn, home: Path) -> Path:
    """The agent's agy home for this turn: the plugin, its rules and settings."""
    plug = home / ".gemini" / "config" / "plugins" / PLUGIN
    (plug / "rules").mkdir(parents=True, exist_ok=True)
    (plug / "plugin.json").write_text(json.dumps({"name": PLUGIN}), encoding="utf-8")
    b = turn.bridge
    (plug / "mcp_config.json").write_text(json.dumps({"mcpServers": {"pantheon": {
        "command": b.command, "args": b.args, "env": b.env}}}, indent=2), encoding="utf-8")
    (plug / "rules" / "AGENTS.md").write_text(turn.fresh_context(), encoding="utf-8")
    allow(home, [BRIDGE_RULE])
    # Never a trusted workspace: there agy runs what it would refuse, or reports a refused
    # step as done without saying what it was, and the user couldn't be asked.
    data = _read_settings(home)
    if data.pop("trustedWorkspaces", None) is not None:
        _settings_path(home).write_text(json.dumps(data, indent=2), encoding="utf-8")
    return plug


def env_for(home: Path) -> dict[str, str]:
    env = {"HOME": str(home), "USERPROFILE": str(home)}
    gitconfig = Path.home() / ".gitconfig"
    if gitconfig.is_file():  # its shell commands still commit as the user
        env["GIT_CONFIG_GLOBAL"] = str(gitconfig)
    return env


def describe(tool: str, params: dict[str, Any], rule: str) -> str:
    if tool == "run_command" and params.get("CommandLine"):
        return f"run `{str(params['CommandLine'])[:300]}`"
    for key in ("AbsolutePath", "TargetFile", "Url", "SearchPath", "DirectoryPath"):
        if params.get(key):
            return f"use {tool} on {params[key]}"
    return f"use {rule or tool}"


_FOR = re.compile(r"denied permission for (\S+\(.*\))\s*$")


def denied_rule(message: str) -> str | None:
    """The allow rule that would have let a refused step run, from agy's error text:
    "...user denied permission for read_file(C:\\x)" or "...to run command:\\n<cmd>"."""
    lines = (message or "").splitlines()
    for i, line in enumerate(lines):
        if m := _FOR.search(line):
            return m.group(1)
        if line.rstrip().endswith("denied permission to run command:") and i + 1 < len(lines):
            return f"command({lines[i + 1].strip()})"
    return None


class _ResumeFailed(ConnectError):
    pass


async def run(turn) -> str:
    try:
        return await _turn(turn, turn.runner.get("session"))
    except _ResumeFailed:
        await turn.remember(session=None)
        return await _turn(turn, None)


async def _turn(turn, conversation: str | None) -> str:
    r = turn.runner
    autonomy = str(r.get("autonomy") or "ask")
    home = home_for(turn.agent.id)
    plug = prepare(turn, home)
    first = turn.inbox
    reply = ""
    try:
        for round_ in range(ROUNDS + 1):
            out = await _converse(turn, home, conversation, first)
            conversation = out["conversation"] or conversation
            if conversation:
                await turn.remember(session=conversation)
            reply = out["reply"] or reply
            if autonomy != "ask" or not out["denied"] or round_ == ROUNDS:
                break
            first = await _ask_user(turn, home, out["denied"])
    finally:
        # The bridge token is only good for this turn; don't leave it on disk.
        with contextlib.suppress(OSError):
            (plug / "mcp_config.json").unlink()
    return reply


async def _ask_user(turn, home: Path, denied: list[dict[str, Any]]) -> str:
    """Each refused action, as an approval in the user's Inbox (all at once); what they
    approve becomes an allow rule. Returns the message that carries the conversation on."""
    asks = [turn.ask(d["tool"], d["params"], describe(d["tool"], d["params"], d["rule"]))
            for d in denied]
    answers = await asyncio.gather(*asks)
    yes = [d for d, (ok, _) in zip(denied, answers, strict=True) if ok]
    no = [(d, note) for d, (ok, note) in zip(denied, answers, strict=True) if not ok]
    allow(home, [d["rule"] for d in yes])
    parts = []
    if yes:
        parts.append("The user approved what you asked for; it's allowed now, so go ahead:\n" +
                     "\n".join(f"- {d['rule']}" for d in yes))
    if no:
        parts.append("The user did not allow:\n" + "\n".join(
            f"- {d['rule']}{f' ({note})' if note else ''}" for d, note in no) +
            "\nCarry on without these, or explain what you need.")
    return "\n\n".join(parts)


async def _converse(turn, home: Path, conversation: str | None, text: str) -> dict[str, Any]:
    r = turn.runner
    argv = command_argv(r.get("command") or "agy") + [
        "--input-format", "stream-json", "--output-format", "stream-json",
        "--print-timeout", "6h"]
    argv += ["--dangerously-skip-permissions"] if r.get("autonomy") == "full" else \
        ["--mode", "accept-edits"]
    if r.get("model"):
        argv += ["--model", str(r["model"])]
    if r.get("effort") in ("low", "medium", "high"):
        argv += ["--effort", str(r["effort"])]
    if conversation:
        argv += ["--conversation", conversation]
    argv.append("-p=")  # the messages come on stdin
    proc = await Proc.start(argv, turn.workdir, {**turn.env, **env_for(home)})
    state: dict[str, Any] = {"sent": 0, "results": [], "conversation": conversation,
                             "denied": [], "text": [], "done": False, "error": ""}
    gate = asyncio.Lock()

    async def send(message: str) -> None:
        state["sent"] += 1
        await proc.send({"event": "user", "message": {"content": message}})

    async def take_steering() -> int:
        texts = await turn.steering()
        for t in texts:
            await send(t)
        return len(texts)

    async def steer() -> None:
        while True:
            await asyncio.sleep(STEER_EVERY)
            async with gate:
                if state["done"]:
                    return
                await take_steering()

    await send(text)
    steering = asyncio.create_task(steer())
    try:
        async for ev in proc.events():
            await _event(turn, ev, state)
            if ev.get("event") == "result":
                async with gate:
                    if len(state["results"]) >= state["sent"] and not await take_steering():
                        state["done"] = True
                        proc.close_stdin()
    finally:
        steering.cancel()
        with contextlib.suppress(BaseException):
            await steering
        await proc.stop()  # its output has ended: it's done (or stopped, if the turn was)
        await proc.wait()
    if not state["results"]:
        why = state["error"] or proc.stderr or f"exited with {proc.returncode}"
        _raise(why, conversation)
    ok = [x for x in state["results"] if x["ok"]]
    if not ok:
        _raise(state["results"][-1]["error"], conversation)
    return {"reply": ok[-1]["text"], "conversation": state["conversation"],
            "denied": state["denied"]}


def _raise(why: str, conversation: str | None) -> None:
    low = why.lower()
    if conversation and "conversation" in low and ("not found" in low or "no such" in low
                                                    or "unknown" in low):
        raise _ResumeFailed(why)
    if is_quota(why):
        raise QuotaWait(why.strip()[-300:], reset_time(why))
    hint = " (sign in: run agy once)" if "auth" in low else ""
    raise ConnectError(f"agy gave no answer: {why[-1500:]}{hint}")


async def _event(turn, ev: dict[str, Any], state: dict[str, Any]) -> None:
    kind = ev.get("event")
    if kind == "init":
        state["conversation"] = ev.get("conversation_id") or state["conversation"]
    elif kind == "step_update":
        s = ev.get("step_update") or {}
        state["conversation"] = s.get("conversation_id") or state["conversation"]
        if s.get("step_type") == "agent_response":
            if delta := s.get("text_delta"):
                state["text"].append(delta)
                await turn.emit(delta)
        elif s.get("step_type") == "tool":
            await _tool(turn, s, state)
    elif kind == "result":
        res = ev.get("result") or {}
        state["conversation"] = res.get("conversation_id") or state["conversation"]
        u = res.get("usage") or {}
        await turn.usage(int(u.get("input_tokens") or 0) + int(u.get("cache_read_tokens") or 0),
                         int(u.get("output_tokens") or 0))
        ok = str(res.get("status") or "").upper() == "SUCCESS"
        text = str(res.get("response") or "").strip() or "".join(state["text"]).strip()
        refused = [str(d.get("display_name") or d.get("action")) for d in
                   res.get("denied_actions") or []]
        if ok and not text and (state["denied"] or refused):
            text = "(I needed permission for: " + ", ".join(
                [d["rule"] for d in state["denied"]] or refused) + ")"
        state["results"].append({"ok": ok, "text": text,
                                 "error": str(res.get("error") or text or "failed")})
        state["text"] = []


async def _tool(turn, s: dict[str, Any], state: dict[str, Any]) -> None:
    info = s.get("tool_info") or {}
    name = str(s.get("tool_name") or info.get("name") or "tool")
    params = info.get("parameters") or {}
    if name == "call_mcp_tool" and str(params.get("ServerName", "")).startswith(PLUGIN):
        return  # Pantheon's own tools: the bridge shows those
    call_id = f"{s.get('conversation_id')}:{s.get('step_index')}"
    st = str(s.get("state") or "").upper()
    if st == "ACTIVE":
        await turn.tool_started(call_id, name, params)
    elif st in ("DONE", "ERROR"):
        await turn.tool_started(call_id, name, params)  # a step can arrive already over
        err = info.get("error") or {}
        message = str(err.get("message") or "") if isinstance(err, dict) else str(err)
        await turn.tool_finished(call_id, st == "DONE", message or str(info.get("output") or ""))
        if st == "ERROR" and "permission" in message:
            rule = denied_rule(message)
            if rule and all(d["rule"] != rule for d in state["denied"]):
                state["denied"].append({"tool": name, "params": params, "rule": rule})


async def models(argv: list[str], cwd: Path, env: dict[str, str]) -> list[dict[str, str]]:
    """``agy models``: "id<TAB>Name" lines."""
    proc = await Proc.start([*argv, "models"], cwd, env, stdin=False)
    try:
        async with asyncio.timeout(60):
            lines = [line async for line in proc.lines()]
            await proc.wait()
    finally:
        await proc.stop()
    out = []
    for line in lines:
        if "\t" in line:
            mid, name = line.rstrip("\r\n").split("\t", 1)
            out.append({"id": mid.strip(), "name": name.strip()})
    return out


def scratch_home(base: Path) -> Path:
    """A throwaway agy home (for the Check), like an agent's."""
    home = base / "agy-home"
    home.mkdir(parents=True, exist_ok=True)
    return home
