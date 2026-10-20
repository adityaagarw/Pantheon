"""Agent kinds: what runs an agent.

- ``llm``: Pantheon's own loop around a model (app/agents/graph.py). The default.
- ``human``: a person. Messages to them wait in the user's Inbox, and whoever answers
  does so as that agent (app/services/humans.py). They never take turns.
- Every other kind comes from a plugin pack (``PackApi.agent_kind``): the shipped
  agent-kinds pack adds ``cli`` (Claude Code, Codex, Hermes or any command-line agent) and
  ``remote`` (an agent behind an HTTP API: OpenAI-compatible, A2A or a plain webhook).

A plugin's kind runs a turn with ``run(turn) -> str``: it gets the inbox as text, the
agent's context, a folder to work in and a bridge to Pantheon's tools, and returns the
reply. Everything around the turn (claiming messages, the Turn record, delivering the
reply, flow steps, failures and retries) is the runtime's, as for any agent.

While it runs, the turn's live methods (``turn.think``, ``tool_started``/``tool_finished``,
``usage``, ``ask``, ``steering``) make it as visible and steerable as Pantheon's own
agents: its tools show in the office and Sessions, its cost counts, the user approves what
it asks to do from their Inbox, and steering reaches it mid-turn. Raising QuotaWait parks
its work until the provider's limit resets.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models import Agent, Org

BUILTIN = ("llm", "human")


@dataclass
class Bridge:
    """How an external agent reaches Pantheon's tools: a stdio MCP server
    (python -m app.mcpbridge) that relays to the engine."""

    command: str
    args: list[str]
    env: dict[str, str]
    tools: list[str]

    def mcp_config(self) -> dict[str, Any]:
        """{"mcpServers": {"pantheon": ...}}, the format Claude Code reads."""
        return {"mcpServers": {"pantheon": {"command": self.command, "args": self.args,
                                            "env": self.env}}}

    def acp_server(self) -> dict[str, Any]:
        """The same server as the Agent Client Protocol's session/new takes it."""
        return {"name": "pantheon", "command": self.command, "args": self.args,
                "env": [{"name": k, "value": v} for k, v in self.env.items()]}


class QuotaWait(RuntimeError):
    """The agent's provider says its usage limit is reached: its messages wait (they aren't
    failures) until ``until``, or an hour when it doesn't say."""

    def __init__(self, why: str, until: datetime | None = None):
        super().__init__(why)
        self.until = until


class Live:
    """What a running external turn tells Pantheon and asks of it (app/agents/external.py
    implements it). The base does nothing, for kinds run outside a turn."""

    async def think(self, text: str) -> None: ...
    async def tool_started(self, call_id: str, name: str, args: dict[str, Any]) -> None: ...
    async def tool_finished(self, call_id: str, ok: bool, output: str) -> None: ...
    async def usage(self, input_tokens: int = 0, output_tokens: int = 0,
                    cost_usd: float = 0.0) -> None: ...
    async def status(self, detail: str) -> None: ...

    async def ask(self, action: str, args: dict[str, Any], why: str = "") -> tuple[bool, str]:
        return False, "nobody is there to approve it"

    async def steering(self) -> list[str]:
        return []


@dataclass
class ExternalTurn:
    agent: Agent
    org: Org
    turn_id: str
    inbox: str  # the new messages, as an LLM agent would see them
    system: str  # who the agent is, its organization and how to work in it
    workdir: Path
    bridge: Bridge
    emit: Callable[[str], Awaitable[None]]  # stream text to the interface as it comes
    runner: dict[str, Any] = field(default_factory=dict)  # the agent's kind settings
    live: Live = field(default_factory=Live)
    # What the agent did lately, for a session that starts fresh (its own memory is gone).
    recent: str = ""
    # The environment of the account it signs in with (Settings → Accounts): its own
    # settings folder, or an API key. Empty: this computer's sign-in.
    env: dict[str, str] = field(default_factory=dict)

    async def think(self, text: str) -> None:
        await self.live.think(text)

    async def tool_started(self, call_id: str, name: str, args: dict[str, Any]) -> None:
        await self.live.tool_started(call_id, name, args)

    async def tool_finished(self, call_id: str, ok: bool, output: str) -> None:
        await self.live.tool_finished(call_id, ok, output)

    async def usage(self, input_tokens: int = 0, output_tokens: int = 0,
                    cost_usd: float = 0.0) -> None:
        await self.live.usage(input_tokens, output_tokens, cost_usd)

    async def status(self, detail: str) -> None:
        await self.live.status(detail)

    async def ask(self, action: str, args: dict[str, Any], why: str = "") -> tuple[bool, str]:
        """Ask the user to approve something (it waits in their Inbox): (approved, note)."""
        return await self.live.ask(action, args, why)

    async def steering(self) -> list[str]:
        """Messages that arrived for the agent while it works (the user's steer), as text."""
        return await self.live.steering()

    def fresh_context(self) -> str:
        """The system context plus what the agent did lately: for a new session."""
        return self.system + (f"\n\n# What you did lately\n{self.recent}" if self.recent else "")

    async def remember(self, **values: Any) -> None:
        """Keep something for the next turn (a CLI's session id), in the agent's runner."""
        from app.core.db import SessionLocal

        async with SessionLocal() as session:
            a = await session.get(Agent, self.agent.id)
            if a is not None:
                a.runner = {**(a.runner or {}), **values}
                await session.commit()
        self.runner = {**self.runner, **values}


Run = Callable[[ExternalTurn], Awaitable[str]]
# Is it installed, signed in and answering? {"ok", "checks": [{"label", "ok", "detail"}],
# "models"?: [{"id", "name"}]}
Check = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class Kind:
    id: str
    label: str
    description: str
    run: Run | None
    # Starting points in the interface: [{"id", "label", "description", "runner": {...}}].
    presets: list[dict[str, Any]] = field(default_factory=list)
    # The settings the interface asks for: [{"key", "label", "hint", "type": text|secret|
    # select|list, "options"?}].
    fields: list[dict[str, Any]] = field(default_factory=list)
    validate: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    pack: str = ""
    check: Check | None = None

    def info(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "description": self.description,
                "presets": self.presets, "fields": self.fields, "pack": self.pack,
                "checkable": self.check is not None}


_KINDS: dict[str, Kind] = {
    "llm": Kind("llm", "Pantheon agent", "Pantheon's own agent, around the model you choose.",
                None),
    "human": Kind("human", "A person", "A person in the organization: messages to them wait "
                  "in your Inbox, and you (or whoever you hand it to) answer as them. Human "
                  "in the loop.", None),
}


def register(kind: Kind) -> None:
    if kind.id in BUILTIN:
        raise ValueError(f"'{kind.id}' is a built-in agent kind")
    _KINDS[kind.id] = kind


def get(kind_id: str) -> Kind | None:
    from app import packs

    k = _KINDS.get(kind_id)
    if k is not None and k.pack and not packs.is_usable(k.pack):
        return None  # its pack is switched off
    return k


def all_kinds() -> list[Kind]:
    return [k for k in _KINDS.values() if get(k.id) is not None]


def validate(kind_id: str, runner: dict[str, Any] | None) -> dict[str, Any]:
    k = get(kind_id)
    if k is None:
        raise ValueError(f"no agent kind '{kind_id}' (is its pack switched off?)")
    runner = dict(runner or {})
    return k.validate(runner) if k.validate else runner
