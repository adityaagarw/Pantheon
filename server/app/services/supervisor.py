"""Zeus, the Pantheon supervisor: a built-in meta-agent living in the system org.

It helps the user design organizations and triages feature requests that
agents file (``request_feature``) — the self-improvement loop.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.db import SessionLocal
from app.events import bus
from app.models import Agent, FeatureRequest, Org, UserInboxItem
from app.services import comms, orgs
from app.tools import base as tools_base

SYSTEM_ORG_ID = "org_pantheon"
SUPERVISOR_NAME = "Zeus"
LEGACY_NAMES = {"Pantheon"}  # renamed automatically unless the user chose a name

META_TOOLS = [
    "list_orgs", "get_org", "create_org", "update_org", "create_agent", "update_agent",
    "delete_agent", "set_relationship", "remove_relationship", "create_org_channel",
    "tool_catalog", "list_models", "add_mcp_server", "update_mcp_server", "test_mcp_server",
    "list_feature_requests", "update_feature_request", "message_agent", "org_activity",
    "assign_goal",
]
SUPERVISOR_PERSONA = """\
You are Zeus, the supervisor of this Pantheon platform and the user's partner in designing
and running organizations of AI agents. You are thoughtful, precise and practical.
You explain trade-offs briefly and then act. When the user describes what they want,
propose a concrete organization (roles, reporting lines, channels, tools, models),
confirm the important choices, then build it with your admin tools.
"""


def fr_to_dict(fr: FeatureRequest) -> dict[str, Any]:
    return {"id": fr.id, "orgId": fr.org_id, "requesterId": fr.requester_id, "title": fr.title,
            "description": fr.description, "rationale": fr.rationale, "status": fr.status,
            "resolution": fr.resolution,
            "createdAt": fr.created_at.isoformat() if fr.created_at else None,
            "updatedAt": fr.updated_at.isoformat() if fr.updated_at else None}


WORLD_TOOLS = ["list_assets", "create_asset", "place_item", "spawn_object", "remove_object",
               "set_world", "move_agent"]
BUILD_TOOLS = ["create_tool", "list_custom_tools", "delete_custom_tool", "grant_tool",
               "create_activity", "list_activities", "update_activity", "delete_activity",
               "run_activity"]
ARGUS_TOOLS = ["list_orgs", "get_org", "org_activity", "org_health", "list_org_tasks",
               "read_org_conversations", "message_agent", "raise_alert", "watch_org",
               "unwatch_org", "list_watches"]
ARGUS_PERSONA = """You are Argus, the hundred-eyed overseer of this Pantheon platform. You monitor the
user's organizations on demand: you check whether work is proceeding as planned and
intended, spot agents that are stuck, looping, failing or drifting from their goals,
and tell the user clearly what needs their attention. You are calm, observant and
evidence-driven: every claim you make points at a task, a message or an error.
"""
COMMON_TOOLS = ("remember", "recall")


def _tools(names: list[str], extra: tuple[str, ...] = ()) -> list[dict[str, str]]:
    return [{"name": n, "approval": "auto"} for n in [*names, *extra]]


META_AGENTS: dict[str, dict[str, Any]] = {
    "zeus": {"name": SUPERVISOR_NAME, "role": "Platform supervisor", "persona": SUPERVISOR_PERSONA,
             "tools": _tools(META_TOOLS + WORLD_TOOLS + BUILD_TOOLS, COMMON_TOOLS + (
                 "web_search", "fetch_url", "read_file", "list_dir", "search_files"))
             + [{"name": "import_asset", "approval": "ask"}],
             "avatar": {"outfit": "#c9a227", "accent": "#fff4d6"}},
    "argus": {"name": "Argus", "role": "Overseer", "persona": ARGUS_PERSONA,
              "tools": _tools(ARGUS_TOOLS, COMMON_TOOLS),
              "avatar": {"outfit": "#2f6f9f", "accent": "#d6ecff"}},
}


async def _ensure_system_org() -> None:
    async with SessionLocal() as session:
        org = await session.get(Org, SYSTEM_ORG_ID)
    if org is None:
        await orgs.create_org("Pantheon", "The Pantheon platform's own control room.",
                              kind="system", org_id=SYSTEM_ORG_ID)


async def ensure_meta_agent(role: str) -> Agent:
    """Create the meta agent if missing; top up tools added by upgrades (user edits persist)."""
    spec = META_AGENTS[role]
    await _ensure_system_org()
    async with SessionLocal() as session:
        agent = (await session.execute(select(Agent).where(
            Agent.org_id == SYSTEM_ORG_ID, Agent.meta_role == role))).scalars().first()
        if agent is None and role == "zeus":  # pre-Argus databases
            agent = (await session.execute(select(Agent).where(
                Agent.org_id == SYSTEM_ORG_ID, Agent.is_supervisor.is_(True),
                Agent.meta_role.is_(None)))).scalars().first()
    if agent is None:
        return await orgs.create_agent(SYSTEM_ORG_ID, {
            "name": spec["name"], "role": spec["role"], "persona": spec["persona"],
            "tools": spec["tools"], "is_supervisor": True, "meta_role": role, "preset": "none",
            "avatar": spec["avatar"]})
    names = {t["name"] for t in agent.tools or []}
    missing = [t for t in spec["tools"] if t["name"] not in names]
    patch: dict[str, Any] = {}
    if missing:
        patch["tools"] = [*agent.tools, *missing]
    if role == "zeus" and agent.name in LEGACY_NAMES:
        patch["name"] = SUPERVISOR_NAME
        patch["persona"] = (agent.persona or "").replace(
            "You are Pantheon, the supervisor of this platform",
            "You are Zeus, the supervisor of this Pantheon platform")
    if agent.meta_role != role:
        patch["meta_role"] = role
    if patch:
        agent = await orgs.update_agent(agent.id, patch)
    return agent


async def ensure_supervisor() -> Agent:
    zeus = await ensure_meta_agent("zeus")
    await ensure_meta_agent("argus")
    return zeus


async def meta_agent_id(role: str) -> str:
    async with SessionLocal() as session:
        aid = (await session.execute(select(Agent.id).where(
            Agent.org_id == SYSTEM_ORG_ID, Agent.meta_role == role))).scalars().first()
    if aid is None:
        aid = (await ensure_meta_agent(role)).id
    return aid


async def supervisor_id() -> str:
    return await meta_agent_id("zeus")


async def file_feature_request(org_id: str | None, requester_id: str, title: str,
                               description: str, rationale: str = "", depth: int = 0
                               ) -> FeatureRequest:
    async with SessionLocal() as session:
        fr = FeatureRequest(org_id=org_id, requester_id=requester_id, title=title[:300],
                            description=description, rationale=rationale)
        session.add(fr)
        requester = await session.get(Agent, requester_id)
        org = await session.get(Org, org_id) if org_id else None
        if org_id:
            session.add(UserInboxItem(org_id=org_id, kind="feature_request", ref_id=fr.id,
                                      title=f"Feature request: {title[:200]}"))
        await session.commit()
    await bus.publish("feature_request.created", fr_to_dict(fr), org_id=org_id,
                      agent_id=requester_id)
    who = f"{requester.name} ({requester.role}) in {org.name if org else '?'}" if requester \
        else requester_id
    await comms.notify(
        SYSTEM_ORG_ID, [await supervisor_id()],
        f"Feature request {fr.id} from {who} [org {org_id}]:\n{title}\n\n{description}"
        + (f"\n\nWhy: {rationale}" if rationale else "")
        + "\n\nTriage it: resolve what configuration can solve, reply to the requester with "
          "message_agent, and record the outcome with update_feature_request.",
        depth=depth + 1, kind="feature_request", meta={"featureRequestId": fr.id},
    )
    return fr


def catalog() -> list[dict[str, Any]]:
    return [s.catalog_entry() for s in tools_base.builtin_catalog()]
