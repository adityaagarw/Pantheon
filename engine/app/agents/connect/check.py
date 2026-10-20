"""The Check button on an agent's Kind tab: is its program installed, signed in, and
answering? Which models can it use? Each check says what's wrong and what to do."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.agents.connect import acp, agy
from app.agents.connect.common import ConnectError, Proc, command_argv

HELLO = "Reply with just the word: ready"


async def _output(argv: list[str], cwd: Path, stdin: str | None = None,
                  timeout: float = 30, env: dict[str, str] | None = None) -> tuple[int | None, str]:
    proc = await Proc.start(argv, cwd, env, stdin=stdin is not None)
    try:
        if stdin is not None:
            await proc.write(stdin)
            proc.close_stdin()
        async with asyncio.timeout(timeout):
            text = "".join([line async for line in proc.lines()])
            code = await proc.wait()
        return code, (text + proc.stderr).strip()
    except TimeoutError:
        return None, f"no answer in {int(timeout)}s"
    finally:
        await proc.stop()


def _item(label: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"label": label, "ok": ok, "detail": detail[:600]}


async def check(runner: dict[str, Any], defaults: dict[str, str],
                env: dict[str, str] | None = None) -> dict[str, Any]:
    """``env``: the agent's account (services/accounts.py), checked as the agent would use it."""
    env = env or {}
    preset = str(runner.get("preset") or "custom")
    command = str(runner.get("command") or defaults.get(preset, "")).strip()
    items: list[dict[str, Any]] = []
    out: dict[str, Any] = {"ok": False, "checks": items}
    try:
        argv = command_argv(command)
    except ConnectError as e:
        items.append(_item("Installed", False, f"{e}. {INSTALL.get(preset, '')}".strip()))
        return out
    items.append(_item("Installed", True, argv[-1]))
    work = Path(tempfile.mkdtemp(prefix="pantheon-check-"))
    try:
        if preset != "custom":
            code, text = await _output([*argv, "--version"], work, env=env)
            items.append(_item("Version", code == 0, text.splitlines()[0] if text else "?"))
        if preset == "claude-code":
            await _claude(argv, runner, work, items, env)
        elif preset == "codex":
            code, text = await _output([*argv, "login", "status"], work, env=env)
            signed = code == 0 and "not logged" not in text.lower()
            if not signed and env.get("OPENAI_API_KEY"):
                signed, text = True, "an API key account"
            items.append(_item("Signed in", signed, text or "run: codex login"))
        elif preset == "agy":
            await _agy(argv, runner, work, items, out)
        elif preset in ACP_PRESETS:
            try:
                args = [str(a) for a in runner.get("args") or ACP_PRESETS[preset]]
                info = await asyncio.wait_for(acp.probe([*argv, *args], work), 120)
                items.append(_item("Speaks ACP", True, info["agent"]))
                out["models"] = info["models"]
                if info.get("model"):
                    items.append(_item("Model", True, f"currently {info['model']}"))
            except (ConnectError, TimeoutError, OSError) as e:
                items.append(_item("Speaks ACP", False, f"{e}"[:600] or "no answer"))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    out["ok"] = all(i["ok"] for i in items)
    return out


async def _claude(argv: list[str], runner: dict[str, Any], work: Path,
                  items: list[dict[str, Any]], env: dict[str, str]) -> None:
    """A real (tiny) request: proves it's signed in and the model answers."""
    probe = [*argv, "-p", "--output-format", "json", "--strict-mcp-config",
             "--permission-mode", "plan"]
    if runner.get("model"):
        probe += ["--model", str(runner["model"])]
    code, text = await _output(probe, work, stdin=HELLO, timeout=90, env=env)
    try:
        res = json.loads(text[text.index("{"):]) if "{" in text else {}
    except json.JSONDecodeError:
        res = {}
    answered = code == 0 and not res.get("is_error") and bool(res.get("result"))
    detail = (f"answered “{str(res.get('result'))[:60]}” "
              f"(${float(res.get('total_cost_usd') or 0):.4f})") if answered else \
        (str(res.get("result") or text)[-400:] or "no answer: run `claude` once to sign in")
    items.append(_item("Signed in and answering", answered, detail))


async def _agy(argv: list[str], runner: dict[str, Any], work: Path,
               items: list[dict[str, Any]], out: dict[str, Any]) -> None:
    """Its models, and a tiny request from a home like an agent's (its sign-in is the
    computer's, kept in the OS keychain: this shows it reaches an agent too)."""
    env = agy.env_for(agy.scratch_home(work))
    try:
        out["models"] = await agy.models(argv, work, env)
    except (OSError, TimeoutError):
        out["models"] = []
    items.append(_item("Models", bool(out["models"]),
                       f"{len(out['models'])} offered" if out["models"] else "none listed"))
    probe = [*argv, "--output-format", "json", "--mode", "plan", "--print-timeout", "80s"]
    if runner.get("model"):
        probe += ["--model", str(runner["model"])]
    code, text = await _output([*probe, f"-p={HELLO}"], work, timeout=100, env=env)
    try:
        res = json.loads(text[text.index("{"):]) if "{" in text else {}
    except json.JSONDecodeError:
        res = {}
    answered = code == 0 and res.get("status") == "SUCCESS" and bool(res.get("response"))
    detail = f"answered “{str(res.get('response')).strip()[:60]}”" if answered else \
        (str(res.get("error") or text)[-400:] or "no answer: run `agy` once to sign in")
    items.append(_item("Signed in and answering", answered, detail))


ACP_PRESETS: dict[str, list[str]] = {
    "hermes": ["acp"],
    "gemini": ["--experimental-acp"],
    "opencode": ["acp"],
    "acp": [],
}
INSTALL = {
    "claude-code": "Install it: npm install -g @anthropic-ai/claude-code, then run `claude` once "
                   "to sign in.",
    "codex": "Install it: npm install -g @openai/codex, then run `codex login`.",
    "hermes": "Install Hermes Agent (pip install hermes-agent), then run `hermes setup`.",
    "agy": "Install the Antigravity CLI (antigravity.google), then run `agy` once to sign in.",
    "gemini": "Gemini CLI is retired: use the Antigravity (agy) preset instead.",
    "opencode": "Install it: npm install -g opencode-ai, then run `opencode auth login`.",
}
