"""Persistence model.

The runtime is an actor system: every agent owns a durable inbox
(``deliveries``) and a durable LangGraph thread (``lg_checkpoints``). Every
fact an observer might want — each LLM call, tool call, message, task change —
is recorded in its own table so the whole organization is auditable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.ids import new_id

JsonDict = dict[str, Any]
EMBED_DIM = 384  # BAAI/bge-small-en-v1.5


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


# --- configuration ------------------------------------------------------------


class Provider(Base):
    """An LLM endpoint. ``models`` carries per-model metadata (pricing, context)."""

    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("prv"))
    name: Mapped[str] = mapped_column(String)
    # openai | openrouter | openai_compatible | anthropic | ollama | mock
    type: Mapped[str] = mapped_column(String)
    base_url: Mapped[str | None] = mapped_column(String, nullable=True)
    api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{id, context_window?, input_per_mtok?, output_per_mtok?}]
    models: Mapped[list] = mapped_column(JSON, default=list)
    default_model: Mapped[str | None] = mapped_column(String, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    # Extra constructor kwargs (e.g. {"extra_body": {...}}); never secrets.
    options: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _ts()


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class Org(Base):
    """An organization: a set of agents, their structure, channels and work."""

    __tablename__ = "orgs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("org"))
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text, default="")
    # user | system (the Pantheon supervisor's home)
    kind: Mapped[str] = mapped_column(String, default="user")
    # running | paused
    status: Mapped[str] = mapped_column(String, default="running")
    # Absolute host path the org's agents work in (inside a workspace root).
    workspace: Mapped[str | None] = mapped_column(String, nullable=True)
    # {comm_policy, default_model, max_chain_depth, daily_token_budget, ...}
    settings: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    # 3D office layout overrides (desk placement etc.)
    layout: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    task_counter: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("agt"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String, default="")
    team: Mapped[str] = mapped_column(String, default="")
    # The agent's system prompt body: personality, responsibilities, style.
    persona: Mapped[str] = mapped_column(Text, default="")
    # {provider_id, model, temperature, max_tokens, context_window, reasoning_effort}
    model: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    # [{name, approval: auto|ask|deny}] — builtin names and "mcp:<server>:<tool|*>"
    tools: Mapped[list] = mapped_column(JSON, default=list)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    # Working directory relative to the org workspace (or absolute within it).
    worktree: Mapped[str | None] = mapped_column(String, nullable=True)
    # {max_steps_per_turn, max_turns_per_hour}
    limits: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    # {"tasks": {create, assign, edit, require_review}} — overrides org defaults
    permissions: Mapped[JsonDict] = mapped_column(JSON, default=dict, server_default="{}")
    # {body, skin, hair, outfit, accent} for the 3D avatar
    avatar: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    # active | paused | disabled (user-controlled)
    status: Mapped[str] = mapped_column(String, default="active")
    # idle | working | awaiting_approval | error | cooling_down (runtime-owned)
    runtime_status: Mapped[str] = mapped_column(String, default="idle")
    runtime_detail: Mapped[str] = mapped_column(Text, default="")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_supervisor: Mapped[bool] = mapped_column(Boolean, default=False)
    # Built-in meta agents in the system org: "zeus" (supervisor) | "argus" (overseer)
    meta_role: Mapped[str | None] = mapped_column(String, nullable=True)
    # Where the agent chose to be in the org's space, e.g. {"kind": "room", "room": "Lounge"};
    # None = its default spot (its desk, meetings, breaks).
    location: Mapped[JsonDict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Relationship(Base):
    """A directed structural edge: ``manages`` / ``peer`` / ``advises`` / custom."""

    __tablename__ = "relationships"
    __table_args__ = (UniqueConstraint("org_id", "from_id", "to_id", "kind"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("rel"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    from_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    to_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String)
    label: Mapped[str] = mapped_column(String, default="")


class McpServer(Base):
    __tablename__ = "mcp_servers"
    __table_args__ = (UniqueConstraint("org_id", "name"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("mcp"))
    # NULL = available to every org
    org_id: Mapped[str | None] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String)  # slug used in tool names
    description: Mapped[str] = mapped_column(Text, default="")
    transport: Mapped[str] = mapped_column(String)  # stdio | http
    command: Mapped[str | None] = mapped_column(String, nullable=True)
    args: Mapped[list] = mapped_column(JSON, default=list)
    cwd: Mapped[str | None] = mapped_column(String, nullable=True)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    # Encrypted JSON: {"env": {...}, "headers": {...}}
    secrets_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts()


# --- communication ------------------------------------------------------------


class Channel(Base):
    __tablename__ = "channels"
    __table_args__ = (UniqueConstraint("org_id", "key"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("chn"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    # channel | dm | meeting
    kind: Mapped[str] = mapped_column(String, default="channel")
    # Unique handle: "#general", "dm:<a>:<b>", "meeting:<id>"
    key: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    topic: Mapped[str] = mapped_column(Text, default="")
    # participant ids ("user" is the human)
    members: Mapped[list] = mapped_column(JSON, default=list)
    # all | mentions — which members get inbox deliveries for posts
    notify: Mapped[str] = mapped_column(String, default="all")
    created_by: Mapped[str] = mapped_column(String, default="user")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _ts()


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_channel_created", "channel_id", "created_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("msg"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[str | None] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=True
    )
    task_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    reply_to: Mapped[str | None] = mapped_column(String, nullable=True)
    # user | agent | system
    sender_type: Mapped[str] = mapped_column(String)
    sender_id: Mapped[str] = mapped_column(String)
    # chat | request | notification | meeting | feature_request
    kind: Mapped[str] = mapped_column(String, default="chat")
    content: Mapped[str] = mapped_column(Text)
    # Causal chain length from the originating human/system event. Deliveries
    # are refused past the org's max depth so agents cannot ping-pong forever.
    depth: Mapped[int] = mapped_column(Integer, default=0)
    # Turn that produced the message (agent senders).
    turn_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    meta: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _ts()


class Delivery(Base):
    """One message in one agent's inbox. The runtime's unit of work."""

    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint("message_id", "agent_id"),
        Index("ix_deliveries_pending", "agent_id", "status", "available_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    # pending | claimed | done | dropped
    status: Mapped[str] = mapped_column(String, default="pending")
    available_at: Mapped[datetime] = _ts()
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    turn_id: Mapped[str | None] = mapped_column(String, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _ts()


class UserInboxItem(Base):
    """Something that needs the human's attention (messages, approvals, requests)."""

    __tablename__ = "user_inbox"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("uin"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String)  # message | approval | feature_request | error
    ref_id: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(Text)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _ts()


# --- work -----------------------------------------------------------------------


class Board(Base):
    __tablename__ = "boards"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("brd"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text, default="")
    # Ordered column definitions: [{key, name, wip_limit?}]
    columns: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = _ts()


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("org_id", "number"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("tsk"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    board_id: Mapped[str] = mapped_column(ForeignKey("boards.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)  # human-facing "T-<n>"
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text, default="")
    acceptance: Mapped[str] = mapped_column(Text, default="")
    # backlog | todo | in_progress | blocked | review | done | cancelled
    status: Mapped[str] = mapped_column(String, default="todo")
    priority: Mapped[str] = mapped_column(String, default="normal")  # low|normal|high|urgent
    assignee_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    reporter_id: Mapped[str] = mapped_column(String)  # agent id or "user"
    reviewer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    parent_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    depends_on: Mapped[list] = mapped_column(JSON, default=list)
    watchers: Mapped[list] = mapped_column(JSON, default=list)
    labels: Mapped[list] = mapped_column(JSON, default=list)
    result: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[float] = mapped_column(Float, default=0.0)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskEvent(Base):
    """Audit trail of every task change (who, what, from, to)."""

    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)  # created|status|assignee|comment|edit
    data: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _ts()


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("art"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    path: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String, default="")
    mime: Mapped[str] = mapped_column(String, default="text/plain")
    size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _ts()


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("mem"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)  # NULL = shared
    kind: Mapped[str] = mapped_column(String, default="note")
    content: Mapped[str] = mapped_column(Text)
    # Semantic search (pgvector); NULL until embedded.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM), nullable=True)
    source: Mapped[str] = mapped_column(String, default="agent")  # agent | user
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("mtg"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[str | None] = mapped_column(String, nullable=True)
    facilitator_id: Mapped[str] = mapped_column(String)
    participants: Mapped[list] = mapped_column(JSON, default=list)
    agenda: Mapped[str] = mapped_column(Text)
    # discussion | decision | brainstorm | standup | review
    style: Mapped[str] = mapped_column(String, default="discussion", server_default="discussion")
    # {rounds, detail, create_tasks, room, called_by}
    options: Mapped[JsonDict] = mapped_column(JSON, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String, default="running")  # running|done|failed
    minutes: Mapped[str] = mapped_column(Text, default="")
    task_ids: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    created_at: Mapped[datetime] = _ts()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FeatureRequest(Base):
    """An agent (or the user) asking the Pantheon supervisor for a capability."""

    __tablename__ = "feature_requests"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("fr"))
    org_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    requester_id: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    # open | triaged | accepted | in_progress | done | rejected
    status: Mapped[str] = mapped_column(String, default="open")
    resolution: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# --- observability ----------------------------------------------------------------


class Turn(Base):
    """One activation of an agent: inbox in -> model/tool loop -> quiescent."""

    __tablename__ = "turns"
    __table_args__ = (Index("ix_turns_agent_started", "agent_id", "started_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("trn"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    # running | completed | failed | awaiting_approval | stopped
    status: Mapped[str] = mapped_column(String, default="running")
    trigger_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    summary: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime] = _ts()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LlmCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("llm"))
    org_id: Mapped[str] = mapped_column(String, index=True)
    agent_id: Mapped[str] = mapped_column(String, index=True)
    turn_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    purpose: Mapped[str] = mapped_column(String, default="turn")  # turn|compaction|meeting
    provider: Mapped[str] = mapped_column(String, default="")
    model: Mapped[str] = mapped_column(String, default="")
    request: Mapped[JsonDict] = mapped_column(JSON, default=dict)  # {system, messages, tools}
    response: Mapped[JsonDict] = mapped_column(JSON, default=dict)  # {content, reasoning, tool_calls}
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts()


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # the model's tool_call_id
    org_id: Mapped[str] = mapped_column(String, index=True)
    agent_id: Mapped[str] = mapped_column(String, index=True)
    turn_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String)
    args: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    # running | ok | error | denied
    status: Mapped[str] = mapped_column(String, default="running")
    result: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _ts()


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("apv"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str] = mapped_column(String, index=True)
    turn_id: Mapped[str | None] = mapped_column(String, nullable=True)
    tool_call_id: Mapped[str] = mapped_column(String, unique=True)
    tool: Mapped[str] = mapped_column(String)
    args: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    # pending | approved | denied
    status: Mapped[str] = mapped_column(String, default="pending")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _ts()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorldObject(Base):
    """A movable thing in an org's physical space: can be held, given, placed and used."""

    __tablename__ = "world_objects"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("obj"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String)
    # Catalog key of its 3D look: a furniture key, "asset:<id>" or "plugin:<plugin>/<id>"
    asset: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    holder_id: Mapped[str | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True)
    # Where it lies when nobody holds it: {"kind": "room", "room": ...} | {"kind": "agent", ...}
    place: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    # Free-form state that tools and plugins read and write (e.g. {"open": true}).
    state: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Asset(Base):
    """A 3D asset added at runtime: an imported GLB or a procedural (primitive-built) model."""

    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # slug
    label: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String, default="Props")
    kind: Mapped[str] = mapped_column(String)  # glb | procedural
    file: Mapped[str | None] = mapped_column(String, nullable=True)  # relative to data/assets
    # procedural: {"parts": [{shape, size, pos, rot, color}]}; glb: {"bounds": {min, max}}
    spec: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    # Footprint in meters [w, d, h] and the scale applied to the model's own units.
    size: Mapped[list] = mapped_column(JSON, default=list)
    scale: Mapped[float] = mapped_column(Float, default=1.0)
    carryable: Mapped[bool] = mapped_column(Boolean, default=False)
    blocking: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(String, default="")
    license: Mapped[str] = mapped_column(String, default="")
    created_by: Mapped[str] = mapped_column(String, default="user")
    created_at: Mapped[datetime] = _ts()


class CustomTool(Base):
    """A tool Zeus (or the user) defined without code: an action, an HTTP call or a prompt."""

    __tablename__ = "custom_tools"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    description: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String)  # action | http | prompt
    # JSON schema of the arguments the agent passes.
    parameters: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    # kind-specific: action {narration, witness, requires_object}; http {method, url, headers,
    # body}; prompt {instructions}
    config: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    approval: Mapped[str] = mapped_column(String, default="auto")  # default approval when granted
    created_by: Mapped[str] = mapped_column(String, default="user")
    created_at: Mapped[datetime] = _ts()


class Activity(Base):
    """A scheduled happening in an org: standups, lunch, market opening, patrols…"""

    __tablename__ = "activities"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("act"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String)
    instructions: Mapped[str] = mapped_column(Text, default="")
    participants: Mapped[list] = mapped_column(JSON, default=list)  # agent ids; [] = everyone
    room: Mapped[str | None] = mapped_column(String, nullable=True)  # gather here first
    # {"every_minutes": 60} | {"daily_at": "09:00"} (UTC) | {} = only when run by hand
    schedule: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String, default="user")
    created_at: Mapped[datetime] = _ts()


class Schedule(Base):
    """An agent's own timer: a cron expression or a one-off time that wakes it with a note."""

    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("sch"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    message: Mapped[str] = mapped_column(Text)
    cron: Mapped[str | None] = mapped_column(String, nullable=True)  # None = one-off
    timezone: Mapped[str] = mapped_column(String, default="UTC")
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True,
                                                         index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    runs: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = _ts()


class StagePage(Base):
    """A page agents show the user on the Stage: a lesson, a 3D scene, a visual explanation."""

    __tablename__ = "stage_pages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("stg"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, default="scene")  # scene (JS module) | html
    source: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Whiteboard(Base):
    """A shared Excalidraw whiteboard the user and agents draw on."""

    __tablename__ = "whiteboards"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("wbd"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String, default="Whiteboard")
    # Excalidraw scene elements (merged per element by version).
    elements: Mapped[list] = mapped_column(JSON, default=list)
    app_state: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    files: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    # Shapes agents asked for, in Excalidraw's skeleton format; an open editor
    # converts them into real elements.
    pending: Mapped[list] = mapped_column(JSON, default=list)
    thumbnail: Mapped[str | None] = mapped_column(Text, nullable=True)  # PNG data: URL
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Attachment(Base):
    """A file uploaded to an agent's library (or shared with the org), or attached to a message."""

    __tablename__ = "attachments"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("att"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)  # NULL = org
    message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String)
    mime: Mapped[str] = mapped_column(String, default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String, default="")
    kind: Mapped[str] = mapped_column(String, default="other")  # document | image | other
    status: Mapped[str] = mapped_column(String, default="processing")  # processing | ready | failed
    error: Mapped[str] = mapped_column(Text, default="")
    # {pages, chars, width, height, chunks, note}
    info: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    uploaded_by: Mapped[str] = mapped_column(String, default="user")
    created_at: Mapped[datetime] = _ts()


class AttachmentChunk(Base):
    """A searchable passage of an uploaded document."""

    __tablename__ = "attachment_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    attachment_id: Mapped[str] = mapped_column(
        ForeignKey("attachments.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[str] = mapped_column(String, index=True)
    agent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    idx: Mapped[int] = mapped_column(Integer)
    start: Mapped[int] = mapped_column(Integer, default=0)  # char offset in the extracted text
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM), nullable=True)


class EventLog(Base):
    __tablename__ = "events"

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    type: Mapped[str] = mapped_column(String)
    agent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime] = _ts()


# --- LangGraph checkpoint storage ---------------------------------------------------


class LgCheckpoint(Base):
    __tablename__ = "lg_checkpoints"

    thread_id: Mapped[str] = mapped_column(String, primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String, primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(String, primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String, nullable=True)
    type: Mapped[str] = mapped_column(String)
    checkpoint: Mapped[bytes] = mapped_column(LargeBinary)
    metadata_type: Mapped[str] = mapped_column(String)
    metadata_blob: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = _ts()


class LgBlob(Base):
    __tablename__ = "lg_blobs"

    thread_id: Mapped[str] = mapped_column(String, primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String, primary_key=True, default="")
    channel: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[str] = mapped_column(String, primary_key=True)
    type: Mapped[str] = mapped_column(String)
    blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class LgWrite(Base):
    __tablename__ = "lg_writes"

    thread_id: Mapped[str] = mapped_column(String, primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String, primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)
    blob: Mapped[bytes] = mapped_column(LargeBinary)
    task_path: Mapped[str] = mapped_column(String, default="")


# --- secrets ----------------------------------------------------------------------------


class Secret(Base):
    """A credential agents use by name (``{{secret:NAME}}`` / ``$NAME``) without seeing it.

    The value is encrypted, write-only through the API, injected only at the moment a tool
    runs, and masked out of everything stored or shown to a model."""

    __tablename__ = "secrets"
    __table_args__ = (UniqueConstraint("org_id", "name"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("sec"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String)  # UPPER_SNAKE, e.g. GITHUB_TOKEN
    description: Mapped[str] = mapped_column(Text, default="")
    value_enc: Mapped[str] = mapped_column(Text)
    # agents allowed to use it
    agent_ids: Mapped[list] = mapped_column(JSON, default=list)
    # hosts HTTP tools may send it to ("api.github.com", "*.example.com")
    domains: Mapped[list] = mapped_column(JSON, default=list)
    # also exposed to run_command as an environment variable
    allow_shell: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SecretUse(Base):
    """Audit trail: which agent used which secret, where. Never the value."""

    __tablename__ = "secret_uses"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("scu"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    secret_id: Mapped[str] = mapped_column(String, index=True)  # kept after the secret is deleted
    secret_name: Mapped[str] = mapped_column(String)
    agent_id: Mapped[str] = mapped_column(String)
    tool: Mapped[str] = mapped_column(String)
    target: Mapped[str] = mapped_column(String, default="")  # host, or "shell"
    created_at: Mapped[datetime] = _ts()
