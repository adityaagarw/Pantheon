"""Tool framework.

A tool is a JSON-schema-described async function. Agents only ever see (and
can only ever execute) the tools their configuration grants — permissions are
re-resolved from the database at execution time, not trusted from the prompt.

Agent tool entries: ``{"name": "<tool>", "approval": "auto"|"ask"|"deny"}``.
MCP tools use ``mcp:<server>:<tool>`` or ``mcp:<server>:*``.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaError

from app.core.config import settings
from app.models import Agent, Org

Approval = str  # auto | ask | deny
APPROVALS = ("auto", "ask", "deny")


class ToolError(Exception):
    """An expected tool failure; its message is returned to the model."""


class ToolOutput(str):
    """Tool text that also carries images (data: URLs), e.g. a screenshot.

    Vision models see the images right after the tool result; others get the
    text only. Images are never stored in the database.
    """

    images: list[str]

    def __new__(cls, text: str, images: list[str] | None = None) -> ToolOutput:
        obj = str.__new__(cls, text)
        obj.images = list(images or [])
        return obj


@dataclass
class ToolContext:
    org: Org
    agent: Agent
    turn_id: str | None
    tool_call_id: str
    depth: int = 0  # causal depth of the inbox messages that started this turn

    @property
    def sender(self):
        from app.services.comms import Sender

        return Sender("agent", self.agent.id, turn_id=self.turn_id, depth=self.depth)

    @property
    def workspace(self) -> Path:
        return org_workspace(self.org)

    @property
    def workdir(self) -> Path:
        return agent_workdir(self.org, self.agent)


Handler = Callable[[dict[str, Any], ToolContext], Awaitable[str]]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler
    category: str = "general"
    default_approval: Approval = "auto"
    timeout: float = 120.0
    supervisor_only: bool = False
    # Shown in the agent builder; tools with side effects on the outside world.
    side_effects: bool = False
    _validator: Draft202012Validator | None = field(default=None, repr=False)

    def validate(self, args: dict[str, Any]) -> None:
        if self._validator is None:
            self._validator = Draft202012Validator(self.parameters)
        try:
            self._validator.validate(args)
        except SchemaError as e:
            path = ".".join(str(p) for p in e.absolute_path)
            raise ToolError(f"invalid arguments{(' at ' + path) if path else ''}: {e.message}") \
                from None

    def openai_schema(self) -> dict[str, Any]:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description,
                             "parameters": self.parameters}}

    def catalog_entry(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "category": self.category,
                "defaultApproval": self.default_approval, "supervisorOnly": self.supervisor_only,
                "sideEffects": self.side_effects, "parameters": self.parameters}


_REGISTRY: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", spec.name):
        raise ValueError(f"invalid tool name {spec.name!r}")
    _REGISTRY[spec.name] = spec
    return spec


def unregister(name: str) -> None:
    _REGISTRY.pop(name, None)


def tool(
    name: str,
    description: str,
    parameters: dict[str, Any] | None = None,
    *,
    category: str = "general",
    approval: Approval = "auto",
    timeout: float = 120.0,
    supervisor_only: bool = False,
    side_effects: bool = False,
) -> Callable[[Handler], Handler]:
    params = parameters or {"type": "object", "properties": {}}
    params.setdefault("type", "object")
    params.setdefault("additionalProperties", False)

    def deco(fn: Handler) -> Handler:
        register(ToolSpec(name, description.strip(), params, fn, category, approval, timeout,
                          supervisor_only, side_effects))
        return fn

    return deco


def builtin(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)


def builtin_catalog() -> list[ToolSpec]:
    return sorted(_REGISTRY.values(), key=lambda s: (s.category, s.name))


def obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [],
            "additionalProperties": False}


S = {"type": "string"}
I = {"type": "integer"}  # noqa: E741
B = {"type": "boolean"}


def arr(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


# Tools every new agent gets by default (all removable).
DEFAULT_TOOLSET: list[dict[str, str]] = [
    {"name": n, "approval": "auto"} for n in (
        "send_message", "post_message", "read_channel", "list_channels", "create_channel",
        "list_colleagues", "create_task", "update_task", "list_tasks", "get_task",
        "remember", "recall", "request_feature", "hold_meeting",
    )
]
WORKER_TOOLSET: list[dict[str, str]] = DEFAULT_TOOLSET + [
    {"name": "read_file", "approval": "auto"},
    {"name": "write_file", "approval": "auto"},
    {"name": "edit_file", "approval": "auto"},
    {"name": "list_dir", "approval": "auto"},
    {"name": "search_files", "approval": "auto"},
    {"name": "run_command", "approval": "ask"},
    {"name": "web_search", "approval": "auto"},
    {"name": "fetch_url", "approval": "auto"},
    *({"name": n, "approval": "auto"} for n in (
        "browser_open", "browser_read", "browser_click", "browser_type", "browser_press",
        "browser_scroll", "browser_back", "stage_show", "stage_docs", "stage_check",
        "stage_list", "whiteboard_list", "whiteboard_read", "whiteboard_draw",
        "whiteboard_erase", "whiteboard_new")),
]


# --- workspace resolution ------------------------------------------------------


class WorkspaceError(ToolError):
    pass


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def check_workspace_path(path: str | Path) -> Path:
    """Resolve and verify a workspace path lies inside a configured root."""
    p = Path(path).expanduser()
    roots = settings.workspace_root_paths
    if not p.is_absolute():
        p = roots[0] / p
    p = p.resolve()
    if not any(_inside(p, r) for r in roots):
        raise WorkspaceError(
            f"{p} is outside the allowed workspace roots "
            f"({'; '.join(str(r) for r in roots)}); set PANTHEON_WORKSPACE_ROOTS to allow it"
        )
    return p


def org_workspace(org: Org) -> Path:
    if org.workspace:
        p = check_workspace_path(org.workspace)
    else:
        p = settings.workspace_root_paths[0] / org.id
    p.mkdir(parents=True, exist_ok=True)
    return p


def agent_workdir(org: Org, agent: Agent) -> Path:
    root = org_workspace(org)
    if not agent.worktree:
        return root
    p = Path(agent.worktree).expanduser()
    p = (p if p.is_absolute() else root / p).resolve()
    if not _inside(p, root):
        raise WorkspaceError(f"agent worktree {p} is outside the org workspace {root}")
    p.mkdir(parents=True, exist_ok=True)
    return p


def resolve_in_workspace(ctx: ToolContext, path: str) -> Path:
    """A tool-supplied path, relative to the agent's workdir, confined to the org workspace."""
    if not path:
        raise ToolError("path is required")
    root = ctx.workspace
    p = Path(path).expanduser()
    p = (p if p.is_absolute() else ctx.workdir / p).resolve()
    if not _inside(p, root):
        raise ToolError(f"'{path}' is outside your workspace ({root})")
    return p


def truncate(text: str, cap: int | None = None) -> str:
    cap = cap or settings.tool_output_cap_chars
    if len(text) <= cap:
        return text
    head = text[: cap * 3 // 4]
    tail = text[-cap // 4 :]
    return f"{head}\n\n…[{len(text) - cap} characters omitted]…\n\n{tail}"
