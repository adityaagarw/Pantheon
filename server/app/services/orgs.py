"""Organizations, agents and structure (validated CRUD + import/export)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

from sqlalchemy import delete, func, select

from app.core.db import SessionLocal
from app.events import bus
from app.llm import effort as effort_mod
from app.models import Agent, Board, Channel, McpServer, Org, Provider, Relationship
from app.services import tasks
from app.services.comms import channel_to_dict as comms_channel_dict
from app.services.permissions import validate_task_policy
from app.tools import base as tools_base

REL_KINDS = ("manages", "peer", "advises", "custom")
AGENT_STATUSES = ("active", "paused", "disabled")
DEFAULT_ORG_SETTINGS: dict[str, Any] = {
    "comm_policy": "open",
    "max_chain_depth": 40,
    "daily_token_budget": 0,
    "task_policy": {},
    "meetings": {},
}


class OrgError(ValueError):
    pass


# --- serialization ------------------------------------------------------------------


def org_to_dict(o: Org) -> dict[str, Any]:
    return {"id": o.id, "name": o.name, "description": o.description, "kind": o.kind,
            "status": o.status, "workspace": o.workspace,
            "settings": {**DEFAULT_ORG_SETTINGS, **(o.settings or {})}, "layout": o.layout or {},
            "createdAt": o.created_at.isoformat() if o.created_at else None}


def agent_to_dict(a: Agent) -> dict[str, Any]:
    return {"id": a.id, "orgId": a.org_id, "name": a.name, "role": a.role, "team": a.team,
            "persona": a.persona, "model": a.model or {}, "tools": a.tools or [],
            "skills": a.skills or [], "worktree": a.worktree, "limits": a.limits or {},
            "permissions": a.permissions or {},
            "avatar": a.avatar or {}, "status": a.status, "runtimeStatus": a.runtime_status,
            "runtimeDetail": a.runtime_detail, "consecutiveFailures": a.consecutive_failures,
            "retryAt": a.retry_at.isoformat() if a.retry_at else None,
            "isSupervisor": a.is_supervisor, "metaRole": a.meta_role,
            "location": a.location,
            "createdAt": a.created_at.isoformat() if a.created_at else None}


def rel_to_dict(r: Relationship) -> dict[str, Any]:
    return {"id": r.id, "orgId": r.org_id, "fromId": r.from_id, "toId": r.to_id,
            "kind": r.kind, "label": r.label}


# --- helpers ----------------------------------------------------------------------------

_PALETTE = ["#e4572e", "#29335c", "#f3a712", "#669bbc", "#8f2d56", "#218380", "#73d2de",
            "#ffbc42", "#d81159", "#3a86ff", "#8338ec", "#2a9d8f", "#e76f51", "#6a994e"]
_SKINS = ["#f6d7c3", "#eac1a4", "#d9a47f", "#b97f5a", "#8d5b3e", "#5f3b28"]
_HAIRS = ["#1f1a17", "#3b2a20", "#6b4226", "#a86b3c", "#d9b26f", "#8a8a8a", "#b33a3a"]


def default_avatar(seed: str) -> dict[str, Any]:
    h = int(hashlib.sha1(seed.encode()).hexdigest(), 16)
    return {"outfit": _PALETTE[h % len(_PALETTE)], "skin": _SKINS[(h >> 8) % len(_SKINS)],
            "hair": _HAIRS[(h >> 16) % len(_HAIRS)], "body": ("a", "b")[(h >> 24) % 2],
            "accent": _PALETTE[(h >> 32) % len(_PALETTE)]}


def _clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not name or len(name) > 40:
        raise OrgError("agent name must be 1–40 characters")
    if name.lower() in ("user", "system", "everyone", "all"):
        raise OrgError(f"'{name}' is reserved")
    return name


async def _validate_tools(entries: list[Any], org_id: str | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for e in entries or []:
        if isinstance(e, str):
            e = {"name": e}
        if not isinstance(e, dict) or not e.get("name"):
            raise OrgError(f"invalid tool entry {e!r}")
        name = str(e["name"]).strip()
        approval = e.get("approval") or ""
        if approval and approval not in tools_base.APPROVALS:
            raise OrgError(f"approval for {name} must be one of {tools_base.APPROVALS}")
        if name.startswith("mcp:"):
            parts = name.split(":", 2)
            if len(parts) != 3 or not parts[1] or not parts[2]:
                raise OrgError(f"MCP tool entry must look like mcp:<server>:<tool|*>, got {name}")
        elif tools_base.builtin(name) is None:
            raise OrgError(f"unknown tool '{name}'")
        if name in seen:
            continue
        seen.add(name)
        spec = tools_base.builtin(name)
        out.append({"name": name,
                    "approval": approval or (spec.default_approval if spec else "auto")})
    return out


async def _validate_model(model: dict[str, Any] | None) -> dict[str, Any]:
    model = {k: v for k, v in (model or {}).items() if v not in (None, "")}
    allowed = {"provider_id", "model", "temperature", "max_tokens", "context_window", "vision",
               "reasoning_effort"}
    unknown = set(model) - allowed
    if unknown:
        raise OrgError(f"unknown model settings: {', '.join(sorted(unknown))}")
    if pid := model.get("provider_id"):
        async with SessionLocal() as session:
            if await session.get(Provider, pid) is None:
                raise OrgError(f"provider '{pid}' does not exist")
    if "reasoning_effort" in model and not effort_mod.valid(model["reasoning_effort"]):
        raise OrgError("reasoning_effort must be one of: " + ", ".join(effort_mod.LEVELS))
    if "temperature" in model:
        t = float(model["temperature"])
        if not 0 <= t <= 2:
            raise OrgError("temperature must be between 0 and 2")
        model["temperature"] = t
    for k in ("max_tokens", "context_window"):
        if k in model:
            model[k] = int(model[k])
    return model


def _validate_permissions(perms: dict[str, Any] | None) -> dict[str, Any]:
    perms = dict(perms or {})
    unknown = set(perms) - {"tasks"}
    if unknown:
        raise OrgError(f"unknown permission groups: {', '.join(sorted(unknown))}")
    if "tasks" in perms:
        try:
            perms["tasks"] = validate_task_policy(perms["tasks"])
        except ValueError as e:
            raise OrgError(str(e)) from None
    return perms


MEETING_STYLES = ("discussion", "decision", "brainstorm", "standup", "review")


def _validate_meeting_settings(m: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (m or {}).items():
        if k in ("max_participants", "max_rounds", "default_rounds"):
            v = int(v)
            if not 1 <= v <= (20 if k == "max_participants" else 6):
                raise OrgError(f"meetings.{k} is out of range")
            out[k] = v
        elif k == "create_tasks":
            out[k] = bool(v)
        elif k == "default_style":
            if v not in MEETING_STYLES:
                raise OrgError(f"meetings.default_style must be one of {MEETING_STYLES}")
            out[k] = v
        elif k == "model":
            out[k] = v or {}
        else:
            raise OrgError(f"unknown meeting setting '{k}'")
    return out


async def _general(session, org_id: str) -> Channel:
    ch = (await session.execute(select(Channel).where(
        Channel.org_id == org_id, Channel.key == "#general"))).scalar_one_or_none()
    if ch is None:
        ch = Channel(org_id=org_id, kind="channel", key="#general", name="general",
                     topic="Org-wide announcements and coordination", members=["user"],
                     notify="mentions")
        session.add(ch)
        await session.flush()
    return ch


# --- orgs -------------------------------------------------------------------------------


async def create_org(name: str, description: str = "", *, workspace: str | None = None,
                     settings_: dict[str, Any] | None = None, kind: str = "user",
                     org_id: str | None = None) -> Org:
    name = (name or "").strip()
    if not name:
        raise OrgError("org name is required")
    if workspace:
        try:
            workspace = str(tools_base.check_workspace_path(workspace))
        except tools_base.WorkspaceError as e:
            raise OrgError(str(e)) from None
    async with SessionLocal() as session:
        org = Org(name=name, description=description, workspace=workspace, kind=kind,
                  settings={**DEFAULT_ORG_SETTINGS, **(settings_ or {})})
        if org_id:
            org.id = org_id
        session.add(org)
        await session.flush()
        session.add(Board(org_id=org.id, name="Main", columns=list(tasks.DEFAULT_COLUMNS)))
        await _general(session, org.id)
        await session.commit()
    await bus.publish("org.created", org_to_dict(org), org_id=org.id)
    return org


async def update_org(org_id: str, patch: dict[str, Any]) -> Org:
    async with SessionLocal() as session:
        org = await session.get(Org, org_id)
        if org is None:
            raise OrgError("org not found")
        if "name" in patch and patch["name"]:
            org.name = str(patch["name"]).strip()
        if "description" in patch and patch["description"] is not None:
            org.description = str(patch["description"])
        if "workspace" in patch:
            ws = patch["workspace"]
            try:
                org.workspace = str(tools_base.check_workspace_path(ws)) if ws else None
            except tools_base.WorkspaceError as e:
                raise OrgError(str(e)) from None
        if "status" in patch and patch["status"]:
            if patch["status"] not in ("running", "paused"):
                raise OrgError("status must be running or paused")
            org.status = patch["status"]
        if "settings" in patch and isinstance(patch["settings"], dict):
            merged = {**(org.settings or {}), **patch["settings"]}
            if merged.get("comm_policy") not in ("open", "structured"):
                raise OrgError("comm_policy must be open or structured")
            if "task_policy" in patch["settings"]:
                try:
                    merged["task_policy"] = validate_task_policy(patch["settings"]["task_policy"])
                except ValueError as e:
                    raise OrgError(str(e)) from None
            if "meetings" in patch["settings"]:
                merged["meetings"] = _validate_meeting_settings(patch["settings"]["meetings"])
            if "default_model" in patch["settings"]:
                merged["default_model"] = await _validate_model(patch["settings"]["default_model"])
            org.settings = merged
        if "layout" in patch and isinstance(patch["layout"], dict):
            org.layout = patch["layout"]
        await session.commit()
    await bus.publish("org.updated", org_to_dict(org), org_id=org.id)
    if org.status == "running":
        from app.agents import signals

        async with SessionLocal() as session:
            for aid in (await session.execute(select(Agent.id).where(
                    Agent.org_id == org.id))).scalars():
                signals.wake(aid)
    return org


async def delete_org(org_id: str) -> None:
    from app.agents.runtime import runtime

    async with SessionLocal() as session:
        org = await session.get(Org, org_id)
        if org is None:
            raise OrgError("org not found")
        if org.kind == "system":
            raise OrgError("the system org cannot be deleted")
        # Pause first so no new turn can start while agents are being stopped.
        org.status = "paused"
        await session.commit()
        agent_ids = (await session.execute(select(Agent.id).where(
            Agent.org_id == org_id))).scalars().all()
    from app.services import oversight

    await oversight.remove_watch(org_id)
    from app.services import attachments as files

    await asyncio.to_thread(files.delete_org_files, org_id)
    for aid in agent_ids:
        await runtime.stop_turn(aid)
        await runtime.saver.adelete_thread(f"agent:{aid}")
    async with SessionLocal() as session:
        await session.execute(delete(Org).where(Org.id == org_id))
        await session.commit()
    await bus.publish("org.deleted", {"id": org_id}, org_id=org_id)


# --- agents -------------------------------------------------------------------------------


async def create_agent(org_id: str, data: dict[str, Any]) -> Agent:
    name = _clean_name(data.get("name", ""))
    preset = data.get("preset", "worker")
    raw_tools = data.get("tools")
    if raw_tools is None:
        raw_tools = tools_base.WORKER_TOOLSET if preset == "worker" else tools_base.DEFAULT_TOOLSET
    tool_entries = await _validate_tools(raw_tools, org_id)
    model = await _validate_model(data.get("model"))
    async with SessionLocal() as session:
        org = await session.get(Org, org_id)
        if org is None:
            raise OrgError("org not found")
        clash = await session.scalar(select(func.count()).select_from(Agent).where(
            Agent.org_id == org_id, func.lower(Agent.name) == name.lower()))
        if clash:
            raise OrgError(f"an agent named '{name}' already exists in this org")
        agent = Agent(
            org_id=org_id, name=name, role=str(data.get("role", "")).strip(),
            team=str(data.get("team", "")).strip(), persona=str(data.get("persona", "")),
            model=model, tools=tool_entries, skills=list(data.get("skills") or []),
            worktree=data.get("worktree") or None, limits=dict(data.get("limits") or {}),
            permissions=_validate_permissions(data.get("permissions")),
            avatar={**default_avatar(name + org_id), **(data.get("avatar") or {})},
            status=data.get("status", "active"), is_supervisor=bool(data.get("is_supervisor")),
            meta_role=data.get("meta_role") or None,
        )
        if agent.status not in AGENT_STATUSES:
            raise OrgError(f"status must be one of {AGENT_STATUSES}")
        session.add(agent)
        await session.flush()
        if org.kind != "system":
            general = await _general(session, org_id)
            general.members = sorted({*general.members, agent.id})
        restored = await _restore_channels(session, org, agent)
        await session.commit()
    if agent.worktree:
        try:
            tools_base.agent_workdir(org, agent)
        except tools_base.WorkspaceError as e:
            async with SessionLocal() as session:
                await session.execute(delete(Agent).where(Agent.id == agent.id))
                await session.commit()
            raise OrgError(str(e)) from None
    await bus.publish("agent.created", agent_to_dict(agent), org_id=org_id, agent_id=agent.id)
    for ch in restored:
        await bus.publish("channel.updated", comms_channel_dict(ch), org_id=org_id)
    agent.restored_channels = [ch.key for ch in restored]  # reported by Zeus's create_agent
    return agent


async def _restore_channels(session, org: Org, agent: Agent) -> list[Channel]:
    """A recreated agent (same name as one deleted from this org) gets its channels back."""
    former = dict((org.settings or {}).get("former_channels") or {})
    ids = former.pop(agent.name.lower(), None)
    if not ids:
        return []
    org.settings = {**(org.settings or {}), "former_channels": former}
    restored = []
    for ch in (await session.execute(select(Channel).where(
            Channel.id.in_(ids), Channel.org_id == org.id))).scalars():
        if agent.id not in ch.members:
            ch.members = sorted({*ch.members, agent.id})
            restored.append(ch)
    return restored


async def update_agent(agent_id: str, patch: dict[str, Any]) -> Agent:
    async with SessionLocal() as session:
        agent = await session.get(Agent, agent_id)
        if agent is None:
            raise OrgError("agent not found")
        if "name" in patch and patch["name"] is not None:
            name = _clean_name(patch["name"])
            clash = await session.scalar(select(func.count()).select_from(Agent).where(
                Agent.org_id == agent.org_id, func.lower(Agent.name) == name.lower(),
                Agent.id != agent.id))
            if clash:
                raise OrgError(f"an agent named '{name}' already exists in this org")
            agent.name = name
        for field in ("role", "team", "persona"):
            if field in patch and patch[field] is not None:
                setattr(agent, field, str(patch[field]))
        if "tools" in patch and patch["tools"] is not None:
            agent.tools = await _validate_tools(patch["tools"], agent.org_id)
        if "model" in patch and patch["model"] is not None:
            agent.model = await _validate_model(patch["model"])
        if "skills" in patch and patch["skills"] is not None:
            agent.skills = list(patch["skills"])
        if "worktree" in patch:
            agent.worktree = patch["worktree"] or None
            org = await session.get(Org, agent.org_id)
            try:
                tools_base.agent_workdir(org, agent)  # type: ignore[arg-type]
            except tools_base.WorkspaceError as e:
                raise OrgError(str(e)) from None
        if "permissions" in patch and patch["permissions"] is not None:
            agent.permissions = _validate_permissions(patch["permissions"])
        if "limits" in patch and patch["limits"] is not None:
            agent.limits = {**(agent.limits or {}), **patch["limits"]}
        if "avatar" in patch and patch["avatar"] is not None:
            agent.avatar = {**(agent.avatar or {}), **patch["avatar"]}
        if "status" in patch and patch["status"]:
            if patch["status"] not in AGENT_STATUSES:
                raise OrgError(f"status must be one of {AGENT_STATUSES}")
            agent.status = patch["status"]
        if patch.get("meta_role"):
            agent.meta_role = patch["meta_role"]
        await session.commit()
    await bus.publish("agent.updated", agent_to_dict(agent), org_id=agent.org_id,
                      agent_id=agent.id)
    if agent.status == "active":
        from app.agents import signals

        signals.wake(agent.id)
    return agent


async def delete_agent(agent_id: str) -> None:
    from app.agents.runtime import runtime

    async with SessionLocal() as session:
        agent = await session.get(Agent, agent_id)
        if agent is None:
            raise OrgError("agent not found")
        if agent.is_supervisor:
            raise OrgError(f"{agent.name} is a built-in Pantheon agent and cannot be deleted")
        org_id = agent.org_id
    await runtime.stop_turn(agent_id)
    await runtime.saver.adelete_thread(f"agent:{agent_id}")
    from app.services import attachments as files

    await files.delete_for_agent(agent_id)
    await tasks.reassign_from(org_id, agent_id)
    async with SessionLocal() as session:
        left: list[str] = []
        for ch in (await session.execute(select(Channel).where(
                Channel.org_id == org_id))).scalars():
            if agent_id in ch.members:
                ch.members = [m for m in ch.members if m != agent_id]
                if ch.kind == "channel":
                    left.append(ch.id)
        # Remember its channels by name, so recreating the agent restores them (issue #5).
        org = await session.get(Org, org_id)
        if org is not None and left:
            former = dict((org.settings or {}).get("former_channels") or {})
            former[agent.name.lower()] = left
            org.settings = {**(org.settings or {}), "former_channels": former}
        await session.execute(delete(Agent).where(Agent.id == agent_id))
        await session.commit()
    await bus.publish("agent.deleted", {"id": agent_id}, org_id=org_id, agent_id=agent_id)


# --- relationships -----------------------------------------------------------------------


async def set_relationship(org_id: str, from_ref: str, to_ref: str, kind: str,
                           label: str = "") -> Relationship:
    from app.services.comms import resolve_agent

    if kind not in REL_KINDS:
        raise OrgError(f"kind must be one of {REL_KINDS}")
    async with SessionLocal() as session:
        a = await resolve_agent(session, org_id, from_ref)
        b = await resolve_agent(session, org_id, to_ref)
        if a is None or b is None:
            raise OrgError(f"unknown agent '{from_ref if a is None else to_ref}'")
        if a.id == b.id:
            raise OrgError("an agent cannot relate to itself")
        if kind == "manages":
            # Reject management cycles (a manager chain must be a tree/forest).
            managers: dict[str, list[str]] = {}
            for r in (await session.execute(select(Relationship).where(
                    Relationship.org_id == org_id, Relationship.kind == "manages"))).scalars():
                managers.setdefault(r.to_id, []).append(r.from_id)
            stack, seen = [a.id], set()
            while stack:
                cur = stack.pop()
                if cur == b.id:
                    raise OrgError(f"{a.name} managing {b.name} would create a management cycle")
                if cur in seen:
                    continue
                seen.add(cur)
                stack.extend(managers.get(cur, []))
        existing = (await session.execute(select(Relationship).where(
            Relationship.org_id == org_id, Relationship.from_id == a.id,
            Relationship.to_id == b.id, Relationship.kind == kind))).scalar_one_or_none()
        if existing is not None:
            existing.label = label
            rel = existing
        else:
            rel = Relationship(org_id=org_id, from_id=a.id, to_id=b.id, kind=kind, label=label)
            session.add(rel)
        await session.commit()
    await bus.publish("relationship.changed", rel_to_dict(rel), org_id=org_id)
    return rel


async def remove_relationship(org_id: str, rel_id: str) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(Relationship).where(
            Relationship.org_id == org_id, Relationship.id == rel_id))
        await session.commit()
    await bus.publish("relationship.removed", {"id": rel_id}, org_id=org_id)


# --- snapshot / import / export ---------------------------------------------------------------


async def snapshot(org_id: str) -> dict[str, Any]:
    async with SessionLocal() as session:
        org = await session.get(Org, org_id)
        if org is None:
            raise OrgError("org not found")
        agents = (await session.execute(select(Agent).where(Agent.org_id == org_id)
                                        .order_by(Agent.created_at))).scalars().all()
        rels = (await session.execute(select(Relationship).where(
            Relationship.org_id == org_id))).scalars().all()
        channels = (await session.execute(select(Channel).where(
            Channel.org_id == org_id, Channel.kind == "channel"))).scalars().all()
        boards = (await session.execute(select(Board).where(Board.org_id == org_id))).scalars().all()
        servers = (await session.execute(select(McpServer).where(
            McpServer.org_id == org_id))).scalars().all()
    from app.services.comms import channel_to_dict

    return {
        "org": org_to_dict(org),
        "agents": [agent_to_dict(a) for a in agents],
        "relationships": [rel_to_dict(r) for r in rels],
        "channels": [channel_to_dict(c) for c in channels],
        "boards": [tasks.board_to_dict(b) for b in boards],
        "mcpServers": [{"id": s.id, "name": s.name, "transport": s.transport} for s in servers],
    }


async def export_org(org_id: str) -> dict[str, Any]:
    """Portable definition (no runtime state, no secrets), keyed by agent name."""
    snap = await snapshot(org_id)
    names = {a["id"]: a["name"] for a in snap["agents"]}
    return {
        "version": 1,
        "name": snap["org"]["name"],
        "description": snap["org"]["description"],
        "settings": {k: v for k, v in snap["org"]["settings"].items() if k != "default_model"},
        "agents": [
            {k: a[k] for k in ("name", "role", "team", "persona", "tools", "skills", "worktree",
                               "limits", "avatar", "permissions")}
            | {"model": {k: v for k, v in a["model"].items() if k != "provider_id"}}
            for a in snap["agents"] if not a["isSupervisor"]
        ],
        "relationships": [
            {"from": names[r["fromId"]], "to": names[r["toId"]], "kind": r["kind"],
             "label": r["label"]}
            for r in snap["relationships"] if r["fromId"] in names and r["toId"] in names
        ],
        "channels": [
            {"name": c["name"], "topic": c["topic"], "notify": c["notify"],
             "members": [names.get(m, m) for m in c["members"]]}
            for c in snap["channels"] if c["key"] != "#general"
        ],
    } | await _export_space(org_id, names)


async def _export_space(org_id: str, names: dict[str, str]) -> dict[str, Any]:
    from app.models import WorldObject

    async with SessionLocal() as session:
        org = await session.get(Org, org_id)
        objects = (await session.execute(select(WorldObject).where(
            WorldObject.org_id == org_id))).scalars().all()
    out: dict[str, Any] = {}
    layout = (org.layout or {}) if org else {}
    if isinstance(office := layout.get("office"), dict):
        office = json.loads(json.dumps(office))
        for it in office.get("items") or []:
            if aid := it.pop("agentId", None):
                if aid in names:
                    it["agent"] = names[aid]
        out["office"] = office
    if layout.get("extras"):
        out["extras"] = [{k: v for k, v in e.items() if k != "id"} for e in layout["extras"]]
    if objects:
        out["objects"] = [
            {"name": o.name, "asset": o.asset, "description": o.description, "state": o.state}
            | ({"holder": names[o.holder_id]} if o.holder_id in names
               else {"room": (o.place or {}).get("room", "")}) for o in objects]
    return out


async def _import_space(org_id: str, definition: dict[str, Any]) -> None:
    """A template's physical space: office design, placed extras and movable objects.

    Designs and objects refer to agents by name (``"agent": "Ada"``); ids are
    assigned here.
    """
    from app.models import WorldObject

    async with SessionLocal() as session:
        ids = {a.name.lower(): a.id for a in (await session.execute(select(Agent).where(
            Agent.org_id == org_id))).scalars()}
        org = await session.get(Org, org_id)
        assert org is not None
        layout = dict(org.layout or {})
        if isinstance(office := definition.get("office"), dict):
            office = json.loads(json.dumps(office))
            for i, it in enumerate(office.get("items") or []):
                it.setdefault("id", f"it_{i}")
                if who := it.pop("agent", None):
                    it["agentId"] = ids.get(str(who).lower())
            layout["office"] = office
        if isinstance(extras := definition.get("extras"), list):
            layout["extras"] = [{"id": f"ex_{i}", **e} for i, e in enumerate(extras)]
        org.layout = layout
        for spec in definition.get("objects") or []:
            holder = ids.get(str(spec.get("holder", "")).lower()) if spec.get("holder") else None
            session.add(WorldObject(
                org_id=org_id, name=str(spec["name"])[:80], asset=str(spec.get("asset") or ""),
                description=str(spec.get("description") or ""), holder_id=holder,
                place={} if holder else {"kind": "room", "room": spec.get("room") or ""},
                state=spec.get("state") or {}))
        for a_name, place in (definition.get("positions") or {}).items():
            aid = ids.get(a_name.lower())
            if aid:
                agent = await session.get(Agent, aid)
                if agent is not None:
                    agent.location = {"kind": "room", "room": place}
        await session.commit()


async def import_org(definition: dict[str, Any], *, name: str | None = None,
                     workspace: str | None = None) -> Org:
    from app.services import comms

    org = await create_org(name or definition.get("name") or "New organization",
                           definition.get("description", ""), workspace=workspace,
                           settings_=definition.get("settings") or {})
    try:
        for a in definition.get("agents") or []:
            await create_agent(org.id, a)
        for r in definition.get("relationships") or []:
            await set_relationship(org.id, r["from"], r["to"], r.get("kind", "manages"),
                                   r.get("label", ""))
        for c in definition.get("channels") or []:
            await comms.create_channel(org.id, c["name"], c.get("members") or [],
                                       topic=c.get("topic", ""), notify=c.get("notify", "all"))
        await _import_space(org.id, definition)
    except Exception:
        await delete_org(org.id)
        raise
    return org
