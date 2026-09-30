"""Providers, MCP servers, tool catalog, approvals, feature requests, supervisor."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from sqlalchemy import select, update

from app.agents.runtime import approval_to_dict, runtime
from app.core.db import SessionLocal
from app.core.security import decrypt_json, encrypt_json, encrypt_secret
from app.events import bus
from app.llm.providers import PROVIDER_TYPES, list_remote_models, resolve_binding
from app.models import Approval, FeatureRequest, McpServer, Provider
from app.services import supervisor
from app.services.comms import Sender, channel_to_dict, dm_key
from app.services.supervisor import SYSTEM_ORG_ID, fr_to_dict
from app.tools.mcp import manager as mcp_manager

router = APIRouter(prefix="/api/v1", tags=["admin"])


# --- providers ----------------------------------------------------------------------------


def provider_to_dict(p: Provider) -> dict[str, Any]:
    return {"id": p.id, "name": p.name, "type": p.type, "baseUrl": p.base_url,
            "hasApiKey": bool(p.api_key_enc), "models": p.models or [],
            "defaultModel": p.default_model, "isDefault": p.is_default, "options": p.options or {}}


def _normalize_models(models: Any) -> list[dict[str, Any]]:
    out = []
    for m in models or []:
        if isinstance(m, str):
            m = {"id": m}
        if isinstance(m, dict) and m.get("id"):
            out.append({k: v for k, v in m.items()
                        if k in ("id", "context_window", "input_per_mtok", "output_per_mtok", "vision")})
    return out


@router.get("/providers")
async def list_providers() -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (await session.execute(select(Provider).order_by(Provider.created_at))).scalars()
        return [provider_to_dict(p) for p in rows]


@router.post("/providers", status_code=201)
async def create_provider(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    ptype = body.get("type")
    if ptype not in PROVIDER_TYPES:
        raise HTTPException(400, f"type must be one of {PROVIDER_TYPES}")
    if ptype == "openai_compatible" and not body.get("baseUrl"):
        raise HTTPException(400, "baseUrl is required for openai_compatible providers")
    async with SessionLocal() as session:
        p = Provider(name=body.get("name") or ptype, type=ptype, base_url=body.get("baseUrl"),
                     api_key_enc=encrypt_secret(body["apiKey"]) if body.get("apiKey") else None,
                     models=_normalize_models(body.get("models")),
                     default_model=body.get("defaultModel"),
                     options=body.get("options") or {})
        has_default = (await session.execute(select(Provider).where(
            Provider.is_default.is_(True)))).first()
        p.is_default = bool(body.get("isDefault")) or not has_default
        if p.is_default:
            await session.execute(update(Provider).values(is_default=False))
        session.add(p)
        await session.commit()
    return provider_to_dict(p)


@router.patch("/providers/{provider_id}")
async def update_provider(provider_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    async with SessionLocal() as session:
        p = await session.get(Provider, provider_id)
        if p is None:
            raise HTTPException(404, "provider not found")
        if body.get("name"):
            p.name = body["name"]
        if "baseUrl" in body:
            p.base_url = body["baseUrl"] or None
        if body.get("apiKey"):
            p.api_key_enc = encrypt_secret(body["apiKey"])
        if body.get("clearApiKey"):
            p.api_key_enc = None
        if "models" in body:
            p.models = _normalize_models(body["models"])
        if "defaultModel" in body:
            p.default_model = body["defaultModel"] or None
        if "options" in body:
            p.options = body["options"] or {}
        if body.get("isDefault"):
            await session.execute(update(Provider).values(is_default=False))
            p.is_default = True
        await session.commit()
    return provider_to_dict(p)


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(provider_id: str) -> None:
    from app.models import Agent

    async with SessionLocal() as session:
        p = await session.get(Provider, provider_id)
        if p is None:
            raise HTTPException(404, "provider not found")
        users = [a.name for a in (await session.execute(select(Agent))).scalars()
                 if (a.model or {}).get("provider_id") == provider_id]
        if users:
            raise HTTPException(409, f"provider is used by: {', '.join(users[:10])}")
        await session.delete(p)
        await session.commit()


@router.post("/providers/{provider_id}/test")
async def test_provider(provider_id: str, body: dict[str, Any] = Body(default={})) -> dict:
    async with SessionLocal() as session:
        p = await session.get(Provider, provider_id)
    if p is None:
        raise HTTPException(404, "provider not found")
    result: dict[str, Any] = {"ok": False}
    try:
        result["models"] = await list_remote_models(p)
    except Exception as e:  # noqa: BLE001
        result["modelsError"] = f"{type(e).__name__}: {e}"
    model = body.get("model") or p.default_model or next(
        (m["id"] for m in p.models or []), None) or (result.get("models") or [None])[0]
    if model:
        try:
            resolved = await resolve_binding({"provider_id": p.id, "model": model})
            reply = await resolved.chat.ainvoke("Reply with exactly: pong")
            result.update(ok=True, model=model, reply=str(reply.content)[:200])
        except Exception as e:  # noqa: BLE001
            result["error"] = f"{type(e).__name__}: {e}"[:1000]
    else:
        result["error"] = "no model to test"
    return result


# --- MCP servers ---------------------------------------------------------------------------


def mcp_to_dict(s: McpServer) -> dict[str, Any]:
    secrets = decrypt_json(s.secrets_enc)
    st = mcp_manager.status().get(s.id, {})
    return {"id": s.id, "orgId": s.org_id, "name": s.name, "description": s.description,
            "transport": s.transport, "command": s.command, "args": s.args, "cwd": s.cwd,
            "url": s.url, "enabled": s.enabled,
            "envKeys": sorted((secrets.get("env") or {}).keys()),
            "headerKeys": sorted((secrets.get("headers") or {}).keys()),
            "status": st.get("status", "stopped"), "error": st.get("error"),
            "toolCount": st.get("tools", 0)}


@router.get("/mcp-servers")
async def list_mcp(org_id: str | None = None) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        stmt = select(McpServer)
        if org_id:
            stmt = stmt.where((McpServer.org_id == org_id) | McpServer.org_id.is_(None))
        return [mcp_to_dict(s) for s in (await session.execute(stmt)).scalars()]


@router.post("/mcp-servers", status_code=201)
async def create_mcp(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    import re

    name = str(body.get("name", ""))
    if not re.fullmatch(r"[a-z0-9_-]{1,40}", name):
        raise HTTPException(400, "name must be 1-40 chars of a-z, 0-9, _ or -")
    if body.get("transport") not in ("stdio", "http"):
        raise HTTPException(400, "transport must be stdio or http")
    async with SessionLocal() as session:
        s = McpServer(org_id=body.get("orgId"), name=name,
                      description=body.get("description", ""), transport=body["transport"],
                      command=body.get("command"), args=body.get("args") or [],
                      cwd=body.get("cwd"), url=body.get("url"),
                      secrets_enc=encrypt_json({"env": body.get("env") or {},
                                                "headers": body.get("headers") or {}}))
        session.add(s)
        try:
            await session.commit()
        except Exception:  # noqa: BLE001
            raise HTTPException(409, f"an MCP server named '{name}' already exists") from None
    return mcp_to_dict(s)


@router.patch("/mcp-servers/{server_id}")
async def update_mcp(server_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    async with SessionLocal() as session:
        s = await session.get(McpServer, server_id)
        if s is None:
            raise HTTPException(404, "server not found")
        for k in ("description", "command", "args", "cwd", "url", "enabled"):
            if k in body:
                setattr(s, k, body[k])
        if "env" in body or "headers" in body:
            secrets = decrypt_json(s.secrets_enc)
            for key in ("env", "headers"):
                if key in body and body[key] is not None:
                    merged = {**(secrets.get(key) or {}), **body[key]}
                    secrets[key] = {k: v for k, v in merged.items() if v not in (None, "")}
            s.secrets_enc = encrypt_json(secrets)
        await session.commit()
    await mcp_manager.restart(server_id)
    return mcp_to_dict(s)


@router.delete("/mcp-servers/{server_id}", status_code=204)
async def delete_mcp(server_id: str) -> None:
    await mcp_manager.restart(server_id)
    async with SessionLocal() as session:
        s = await session.get(McpServer, server_id)
        if s is not None:
            await session.delete(s)
            await session.commit()


@router.post("/mcp-servers/{server_id}/test")
async def test_mcp(server_id: str) -> dict[str, Any]:
    await mcp_manager.restart(server_id)
    try:
        tools = await mcp_manager.tools(server_id)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    return {"ok": True, "tools": [{"name": t.name, "description": t.description,
                                   "parameters": t.parameters} for t in tools]}


@router.get("/tools/catalog")
async def tool_catalog() -> list[dict[str, Any]]:
    return [t for t in supervisor.catalog() if not t["supervisorOnly"]]


# --- approvals -------------------------------------------------------------------------------


@router.get("/orgs/{org_id}/approvals")
async def list_approvals(org_id: str, status: str | None = "pending") -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        stmt = select(Approval).where(Approval.org_id == org_id)
        if status:
            stmt = stmt.where(Approval.status == status)
        rows = (await session.execute(stmt.order_by(Approval.created_at.desc()).limit(200))
                ).scalars()
        return [approval_to_dict(a) for a in rows]


@router.post("/approvals/{approval_id}")
async def decide_approval(approval_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        row = await runtime.decide(approval_id, bool(body.get("approved")), body.get("note", ""))
    except KeyError:
        raise HTTPException(404, "approval not found") from None
    if body.get("approved") and body.get("always"):
        await _always_allow(row.agent_id, row.tool)
    return approval_to_dict(row)


async def _always_allow(agent_id: str, tool: str) -> None:
    """Switch a tool to auto-approval for one agent (after an 'always allow')."""
    from app.models import Agent
    from app.services import orgs

    async with SessionLocal() as session:
        agent = await session.get(Agent, agent_id)
    if agent is None:
        return
    entries = [dict(e) if isinstance(e, dict) else {"name": e} for e in agent.tools or []]
    if tool.startswith("mcp__"):
        _, server, name = tool.split("__", 2)
        entries.insert(0, {"name": f"mcp:{server}:{name}", "approval": "auto"})
    else:
        for e in entries:
            if e["name"] == tool:
                e["approval"] = "auto"
    await orgs.update_agent(agent_id, {"tools": entries})
    # Approve the agent's other pending requests for the same tool too.
    async with SessionLocal() as session:
        pending = (await session.execute(select(Approval).where(
            Approval.agent_id == agent_id, Approval.tool == tool,
            Approval.status == "pending"))).scalars().all()
    for p in pending:
        await runtime.decide(p.id, True, "always allowed")


# --- feature requests --------------------------------------------------------------------------


@router.get("/feature-requests")
async def list_feature_requests(org_id: str | None = None) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        stmt = select(FeatureRequest).order_by(FeatureRequest.created_at.desc()).limit(500)
        if org_id:
            stmt = stmt.where(FeatureRequest.org_id == org_id)
        return [fr_to_dict(f) for f in (await session.execute(stmt)).scalars()]


@router.post("/feature-requests", status_code=201)
async def create_feature_request(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    if not body.get("title"):
        raise HTTPException(400, "title is required")
    fr = await supervisor.file_feature_request(body.get("orgId"), "user", body["title"],
                                               body.get("description", ""),
                                               body.get("rationale", ""))
    return fr_to_dict(fr)


@router.patch("/feature-requests/{fr_id}")
async def update_feature_request(fr_id: str, body: dict[str, Any] = Body(...)) -> dict:
    async with SessionLocal() as session:
        fr = await session.get(FeatureRequest, fr_id)
        if fr is None:
            raise HTTPException(404, "feature request not found")
        if body.get("status"):
            fr.status = body["status"]
        if "resolution" in body:
            fr.resolution = body["resolution"] or ""
        await session.commit()
    await bus.publish("feature_request.updated", fr_to_dict(fr), org_id=fr.org_id)
    return fr_to_dict(fr)


# --- supervisor ---------------------------------------------------------------------------------


async def _meta(role: str) -> dict[str, Any]:
    from app.services.comms import ensure_dm
    from app.services.orgs import agent_to_dict

    if role not in supervisor.META_AGENTS:
        raise HTTPException(404, f"no meta agent '{role}'")
    aid = await supervisor.meta_agent_id(role)
    async with SessionLocal() as session:
        from app.models import Agent

        agent = await session.get(Agent, aid)
        ch = await ensure_dm(session, SYSTEM_ORG_ID, aid, "user")
        await session.commit()
    return {"orgId": SYSTEM_ORG_ID, "agent": agent_to_dict(agent), "channel": channel_to_dict(ch),
            "dmKey": dm_key(aid, "user")}


@router.get("/supervisor")
async def get_supervisor() -> dict[str, Any]:
    return await _meta("zeus")


@router.get("/meta/{role}")
async def get_meta_agent(role: str) -> dict[str, Any]:
    """The built-in meta agents: zeus (supervisor) and argus (overseer)."""
    return await _meta(role)


@router.get("/argus/watches")
async def argus_watches() -> list[dict[str, Any]]:
    from app.services import oversight

    return list((await oversight.watches()).values())


@router.put("/argus/watches/{org_id}")
async def set_argus_watch(org_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from app.models import Org
    from app.services import oversight

    async with SessionLocal() as session:
        org = await session.get(Org, org_id)
    if org is None:
        raise HTTPException(404, "organization not found")
    return await oversight.set_watch(org.id, org.name, int(body.get("everyMinutes") or 30),
                                     str(body.get("focus") or ""))


@router.delete("/argus/watches/{org_id}", status_code=204)
async def remove_argus_watch(org_id: str) -> None:
    from app.services import oversight

    await oversight.remove_watch(org_id)


_ = Sender
