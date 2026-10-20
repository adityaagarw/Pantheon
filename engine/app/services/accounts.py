"""Accounts: the sign-ins and API keys command-line agents work with.

By default a Claude Code or Codex agent uses whatever that program is signed in to on
this computer. An account gives agents another one:
- a sign-in has its own settings folder for the tool (CLAUDE_CONFIG_DIR, CODEX_HOME)
  under Pantheon's data, signed in with the tool's own login: Pantheon runs it, shows the
  link or code it prints, and passes on what the user types back. Pantheon never sees a
  password; the token stays in that folder, as the tool keeps it.
- an API key is encrypted with the secrets key (in the OS keychain) and handed to the
  tool's process only, in its environment.

Several agents can share an account. When one hits the account's usage limit, the
account cools down and every agent on it waits until it resets, or uses its fallback
account (an agent's ``runner.fallback_account``), say an API key, meanwhile.

agy and Hermes aren't here: agy keeps its sign-in in the OS keychain, not in a folder
Pantheon could give it, and Hermes keeps its own provider keys (``hermes auth``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update

from app.agents.connect.common import ConnectError, Proc, command_argv
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.ids import new_id, utcnow
from app.core.security import decrypt_secret, encrypt_secret
from app.events import bus
from app.models import Account, Agent

log = logging.getLogger(__name__)

TOOLS: dict[str, dict[str, Any]] = {
    "claude-code": {
        "label": "Claude Code", "command": "claude", "home": "CLAUDE_CONFIG_DIR",
        "key": "ANTHROPIC_API_KEY",
        "methods": {"subscription": ("Claude subscription (Pro, Max, Team)",
                                     ["auth", "login", "--claudeai"]),
                    "console": ("Anthropic Console (API billing)", ["auth", "login", "--console"]),
                    "apikey": ("API key", None)},
        "status": ["auth", "status", "--json"]},
    "codex": {
        "label": "Codex", "command": "codex", "home": "CODEX_HOME", "key": "OPENAI_API_KEY",
        "methods": {"chatgpt": ("ChatGPT plan", ["login"]), "apikey": ("API key", None)},
        "status": ["login", "status"]},
}
URL = re.compile(r"https?://[^\s\"'<>]+")
LINES = 400  # of a sign-in's output kept to show


class AccountError(ValueError):
    pass


def home(account_id: str) -> Path:
    return settings.data_path / "accounts" / account_id


def to_dict(a: Account) -> dict[str, Any]:
    tool = TOOLS.get(a.tool, {})
    cooling = a.cooling_until if a.cooling_until and a.cooling_until > utcnow() else None
    return {"id": a.id, "tool": a.tool, "toolLabel": tool.get("label", a.tool), "name": a.name,
            "method": a.method,
            "methodLabel": tool.get("methods", {}).get(a.method, (a.method,))[0],
            "hasKey": bool(a.key_enc), "status": dict(a.status or {}),
            "coolingUntil": cooling.isoformat() if cooling else None,
            "coolingReason": a.cooling_reason if cooling else "",
            "costUsd": round(float(a.cost_usd or 0), 6), "inputTokens": a.input_tokens or 0,
            "outputTokens": a.output_tokens or 0,
            "createdAt": a.created_at.isoformat() if a.created_at else None}


def tools() -> list[dict[str, Any]]:
    return [{"id": k, "label": v["label"],
             "methods": [{"id": m, "label": label, "signIn": argv is not None}
                         for m, (label, argv) in v["methods"].items()]}
            for k, v in TOOLS.items()]


async def list_accounts() -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (await session.execute(select(Account).order_by(Account.created_at))).scalars()
        accounts = [to_dict(a) for a in rows]
        users: dict[str, list[str]] = {}
        for ag in (await session.execute(select(Agent).where(Agent.kind == "cli"))).scalars():
            r = ag.runner or {}
            for key in ("account", "fallback_account"):
                if r.get(key):
                    users.setdefault(r[key], []).append(ag.name)
    for a in accounts:
        a["agents"] = sorted(set(users.get(a["id"], [])))
    return accounts


async def get(account_id: str) -> Account | None:
    async with SessionLocal() as session:
        return await session.get(Account, account_id)


async def create(data: dict[str, Any]) -> dict[str, Any]:
    tool = str(data.get("tool") or "")
    if tool not in TOOLS:
        raise AccountError(f"accounts are for {', '.join(TOOLS)}")
    method = str(data.get("method") or "")
    if method not in TOOLS[tool]["methods"]:
        raise AccountError(f"{TOOLS[tool]['label']} signs in with "
                           f"{', '.join(TOOLS[tool]['methods'])}")
    name = str(data.get("name") or "").strip() or f"{TOOLS[tool]['label']} account"
    key = str(data.get("key") or "").strip()
    if method == "apikey" and not key:
        raise AccountError("give the API key")
    row = Account(id=new_id("acc"), tool=tool, name=name[:120], method=method,
                  key_enc=encrypt_secret(key) if key else None, status={})
    home(row.id).mkdir(parents=True, exist_ok=True)
    async with SessionLocal() as session:
        session.add(row)
        await session.commit()
    if key:
        await _store_key(row, key)
    out = to_dict(row)
    await bus.publish("account.changed", out)
    return out


async def patch(account_id: str, data: dict[str, Any]) -> dict[str, Any]:
    async with SessionLocal() as session:
        row = await session.get(Account, account_id)
        if row is None:
            raise AccountError("account not found")
        if "name" in data and str(data["name"]).strip():
            row.name = str(data["name"]).strip()[:120]
        if str(data.get("key") or "").strip():
            if row.method != "apikey":
                raise AccountError("this account signs in; it has no API key")
            row.key_enc = encrypt_secret(str(data["key"]).strip())
        if data.get("cooling") is False:  # the user says the limit is over
            row.cooling_until, row.cooling_reason = None, ""
        await session.commit()
        out = to_dict(row)
    if str(data.get("key") or "").strip():
        await _store_key(row, str(data["key"]).strip())
    await bus.publish("account.changed", out)
    return out


async def _store_key(a: Account, key: str) -> None:
    """Codex also wants an API key logged in (in the account's folder); Claude Code reads
    it from its environment. What the login prints is never kept: it could echo the key."""
    if a.tool != "codex":
        return
    try:
        argv = command_argv(TOOLS["codex"]["command"])
    except ConnectError:
        return  # not installed yet: the key still goes to it in its environment
    code, _ = await _output([*argv, "login", "--api-key", key],
                            {"CODEX_HOME": str(home(a.id))}, timeout=60)
    if code != 0:
        log.warning("codex didn't take account %s's API key (exit %s)", a.id, code)


async def delete(account_id: str) -> None:
    async with SessionLocal() as session:
        row = await session.get(Account, account_id)
        if row is None:
            raise AccountError("account not found")
        for ag in (await session.execute(select(Agent).where(Agent.kind == "cli"))).scalars():
            r = dict(ag.runner or {})
            if account_id in (r.get("account"), r.get("fallback_account")):
                if r.get("account") == account_id:
                    r["account"] = ""
                    r.pop("session", None)  # its sessions lived in that folder
                if r.get("fallback_account") == account_id:
                    r["fallback_account"] = ""
                ag.runner = r
        await session.delete(row)
        await session.commit()
    shutil.rmtree(home(account_id), ignore_errors=True)
    await bus.publish("account.deleted", {"id": account_id})


# --- using one ------------------------------------------------------------------------------


def env_of(a: Account) -> dict[str, str]:
    """The tool's environment for this account: its own folder, and its key."""
    spec = TOOLS[a.tool]
    path = home(a.id)
    path.mkdir(parents=True, exist_ok=True)
    env = {spec["home"]: str(path)}
    if a.method == "apikey" and a.key_enc:
        env[spec["key"]] = decrypt_secret(a.key_enc)
    return env


@dataclass
class Choice:
    account: Account | None = None
    env: dict[str, str] = field(default_factory=dict)


def _cooling(a: Account | None) -> bool:
    return bool(a and a.cooling_until and a.cooling_until > utcnow())


async def choose(runner: dict[str, Any]) -> Choice:
    """The account an agent's turn uses: its own, or its fallback while that one cools
    down. Raises QuotaWait when every account it may use is cooling down."""
    from app.agents.kinds import QuotaWait

    main_id, spare_id = runner.get("account") or "", runner.get("fallback_account") or ""
    if not main_id:
        return Choice()
    main = await get(main_id)
    if main is None or main.tool not in TOOLS:
        raise ConnectError("this agent's account was removed; pick another on its Kind tab")
    if not _cooling(main):
        return Choice(main, env_of(main))
    spare = await get(spare_id) if spare_id else None
    if spare is not None and spare.tool == main.tool and not _cooling(spare):
        return Choice(spare, env_of(spare))
    until = min(x.cooling_until for x in (main, spare) if x is not None and _cooling(x))
    raise QuotaWait(f"{main.name} is at its usage limit ({main.cooling_reason})", until)


async def cool(account_id: str, until: datetime, why: str) -> None:
    """The account hit its usage limit: every agent on it waits until ``until``."""
    async with SessionLocal() as session:
        await session.execute(update(Account).where(Account.id == account_id).values(
            cooling_until=until, cooling_reason=why[:500]))
        await session.commit()
        row = await session.get(Account, account_id)
    if row is not None:
        await bus.publish("account.changed", to_dict(row))


async def add_usage(account_id: str, input_tokens: int, output_tokens: int, cost: float) -> None:
    async with SessionLocal() as session:
        await session.execute(update(Account).where(Account.id == account_id).values(
            input_tokens=Account.input_tokens + int(input_tokens),
            output_tokens=Account.output_tokens + int(output_tokens),
            cost_usd=Account.cost_usd + float(cost)))
        await session.commit()


async def env_for_runner(runner: dict[str, Any]) -> dict[str, str]:
    """For the Check button: the account's environment, cooling down or not."""
    a = await get(runner.get("account") or "") if runner.get("account") else None
    return env_of(a) if a is not None and a.tool in TOOLS else {}


# --- signed in? -----------------------------------------------------------------------------


async def _output(argv: list[str], env: dict[str, str], timeout: float = 60) -> tuple[int | None, str]:
    proc = await Proc.start(argv, settings.data_path, env, stdin=False)
    try:
        async with asyncio.timeout(timeout):
            text = "".join([line async for line in proc.lines()])
            code = await proc.wait()
        return code, (text + proc.stderr).strip()
    except TimeoutError:
        return None, f"no answer in {int(timeout)}s"
    finally:
        await proc.stop()


def _status_of(tool: str, code: int | None, text: str) -> dict[str, Any]:
    if tool == "claude-code":
        try:
            data = json.loads(text[text.index("{"):text.rindex("}") + 1])
        except ValueError:
            return {"signedIn": False, "detail": text[-300:] or "no answer"}
        detail = " · ".join(str(x) for x in (data.get("email"), data.get("subscriptionType"),
                                             data.get("authMethod")) if x and x != "none")
        return {"signedIn": bool(data.get("loggedIn")), "detail": detail or "not signed in"}
    low = text.lower()
    signed = code == 0 and "not logged in" not in low
    return {"signedIn": signed, "detail": text.splitlines()[0][:200] if text else ""}


async def check(account_id: str) -> dict[str, Any]:
    a = await get(account_id)
    if a is None:
        raise AccountError("account not found")
    spec = TOOLS[a.tool]
    try:
        argv = command_argv(spec["command"])
    except ConnectError as e:
        status = {"signedIn": False, "detail": str(e)}
    else:
        code, text = await _output([*argv, *spec["status"]], env_of(a))
        status = _status_of(a.tool, code, text)
        if a.method == "apikey" and a.key_enc and not status["signedIn"]:
            # The key goes to it in its environment when it works; nothing is "logged in".
            status = {"signedIn": True, "detail": "API key (used when the agent works)"}
    status["checkedAt"] = utcnow().isoformat()
    async with SessionLocal() as session:
        row = await session.get(Account, account_id)
        if row is None:
            raise AccountError("account not found")
        row.status = status
        await session.commit()
        out = to_dict(row)
    await bus.publish("account.changed", out)
    return out


# --- signing in -----------------------------------------------------------------------------


@dataclass
class SignIn:
    """The tool's own login, running: what it prints (links found in it), and its end."""

    id: str
    account_id: str
    proc: Proc
    lines: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    done: bool = False
    code: int | None = None
    task: asyncio.Task | None = None

    def view(self) -> dict[str, Any]:
        return {"id": self.id, "accountId": self.account_id, "output": "".join(self.lines),
                "urls": self.urls, "done": self.done, "ok": self.done and self.code == 0}


_signins: dict[str, SignIn] = {}


async def sign_in(account_id: str) -> dict[str, Any]:
    a = await get(account_id)
    if a is None:
        raise AccountError("account not found")
    login = TOOLS[a.tool]["methods"][a.method][1]
    if login is None:
        raise AccountError("an API-key account doesn't sign in")
    for s in list(_signins.values()):
        if s.account_id == account_id and not s.done:
            return s.view()
    try:
        argv = [*command_argv(TOOLS[a.tool]["command"]), *login]
    except ConnectError as e:
        raise AccountError(str(e)) from None
    proc = await Proc.start(argv, home(a.id), {**env_of(a), "FORCE_HYPERLINK": "0"},
                            merge_stderr=True)
    s = SignIn(new_id("signin"), account_id, proc)
    _signins[s.id] = s
    s.task = asyncio.create_task(_follow(s))
    return s.view()


async def _follow(s: SignIn) -> None:
    try:
        async with asyncio.timeout(15 * 60):
            async for line in s.proc.lines():
                _add(s, line)
            s.code = await s.proc.wait()
    except TimeoutError:
        _add(s, "\n(sign-in timed out)\n")
    finally:
        await s.proc.stop()
        s.done = True
    with contextlib.suppress(Exception):
        await check(s.account_id)
    await bus.publish("account.signin", s.view())


TERMINAL = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[A-Za-z]")


def _add(s: SignIn, text: str) -> None:
    text = TERMINAL.sub("", text)  # colors, and hyperlinks (OSC 8: their text stays)
    s.lines.append(text)
    del s.lines[:-LINES]
    for url in URL.findall(text):
        url = url.rstrip(".,)")
        if url not in s.urls:
            s.urls.append(url)


def sign_in_view(signin_id: str) -> dict[str, Any]:
    s = _signins.get(signin_id)
    if s is None:
        raise AccountError("no such sign-in (it may have finished long ago)")
    return s.view()


async def sign_in_input(signin_id: str, text: str) -> dict[str, Any]:
    """What the user types back (a code the login asks to paste)."""
    s = _signins.get(signin_id)
    if s is None or s.done:
        raise AccountError("this sign-in is over")
    await s.proc.write(text.strip() + "\n")
    _add(s, "› (sent)\n")
    return s.view()


async def sign_in_cancel(signin_id: str) -> None:
    s = _signins.pop(signin_id, None)
    if s is not None and not s.done:
        await s.proc.stop()
