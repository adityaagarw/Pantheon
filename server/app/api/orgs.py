"""Organizations, agents, relationships, templates."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from sqlalchemy import func, select

from app.agents.runtime import RuntimeBusy, runtime
from app.core.db import SessionLocal
from app.models import Agent, Org, Task
from app.services import orgs, templates
from app.services.tasks import CLOSED

router = APIRouter(prefix="/api/v1", tags=["orgs"])


async def _get_org(org_id: str) -> Org:
    async with SessionLocal() as session:
        org = await session.get(Org, org_id)
    if org is None:
        raise HTTPException(404, "organization not found")
    return org


async def _get_agent(agent_id: str) -> Agent:
    async with SessionLocal() as session:
        agent = await session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "agent not found")
    return agent


@router.get("/orgs")
async def list_orgs(include_system: bool = False) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        stmt = select(Org).order_by(Org.created_at)
        if not include_system:
            stmt = stmt.where(Org.kind == "user")
        rows = (await session.execute(stmt)).scalars().all()
        counts = dict((await session.execute(select(Agent.org_id, func.count()).group_by(
            Agent.org_id))).all())
        open_tasks = dict((await session.execute(select(Task.org_id, func.count()).where(
            Task.status.not_in(CLOSED)).group_by(Task.org_id))).all())
        working = dict((await session.execute(select(Agent.org_id, func.count()).where(
            Agent.runtime_status == "working").group_by(Agent.org_id))).all())
    return [orgs.org_to_dict(o) | {"agentCount": counts.get(o.id, 0),
                                   "openTasks": open_tasks.get(o.id, 0),
                                   "workingAgents": working.get(o.id, 0)} for o in rows]


@router.post("/orgs", status_code=201)
async def create_org(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    if tpl := body.get("template"):
        definition = templates.get(tpl)
        if definition is None:
            raise HTTPException(404, f"template '{tpl}' not found")
        org = await orgs.import_org(definition, name=body.get("name") or None,
                                    workspace=body.get("workspace") or None)
        if body.get("description"):
            org = await orgs.update_org(org.id, {"description": body["description"]})
    else:
        org = await orgs.create_org(body.get("name", ""), body.get("description", ""),
                                    workspace=body.get("workspace") or None,
                                    settings_=body.get("settings"))
    return orgs.org_to_dict(org)


@router.get("/orgs/{org_id}")
async def get_org(org_id: str) -> dict[str, Any]:
    await _get_org(org_id)
    return await orgs.snapshot(org_id)


@router.patch("/orgs/{org_id}")
async def update_org(org_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    await _get_org(org_id)
    return orgs.org_to_dict(await orgs.update_org(org_id, body))


@router.delete("/orgs/{org_id}", status_code=204)
async def delete_org(org_id: str) -> None:
    await _get_org(org_id)
    await orgs.delete_org(org_id)


@router.get("/orgs/{org_id}/export")
async def export_org(org_id: str) -> dict[str, Any]:
    await _get_org(org_id)
    return await orgs.export_org(org_id)


@router.post("/orgs/import", status_code=201)
async def import_org(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    definition = body.get("definition") or body
    org = await orgs.import_org(definition, name=body.get("name"),
                                workspace=body.get("workspace"))
    return orgs.org_to_dict(org)


@router.get("/templates")
async def list_templates() -> list[dict[str, Any]]:
    return templates.catalog()


# --- agents -----------------------------------------------------------------------------


@router.post("/orgs/{org_id}/agents", status_code=201)
async def create_agent(org_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    await _get_org(org_id)
    body.pop("is_supervisor", None)
    body.pop("meta_role", None)
    agent = await orgs.create_agent(org_id, body)
    if manager := body.get("manager"):
        await orgs.set_relationship(org_id, manager, agent.id, "manages")
    return orgs.agent_to_dict(agent)


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict[str, Any]:
    return orgs.agent_to_dict(await _get_agent(agent_id)) | {"busy": runtime.is_busy(agent_id)}


@router.patch("/agents/{agent_id}")
async def update_agent(agent_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    await _get_agent(agent_id)
    body.pop("is_supervisor", None)
    body.pop("meta_role", None)
    return orgs.agent_to_dict(await orgs.update_agent(agent_id, body))


@router.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: str) -> None:
    await _get_agent(agent_id)
    await orgs.delete_agent(agent_id)


@router.post("/agents/{agent_id}/stop")
async def stop_agent_turn(agent_id: str) -> dict[str, Any]:
    await _get_agent(agent_id)
    return {"stopped": await runtime.stop_turn(agent_id)}


@router.post("/agents/{agent_id}/retry")
async def retry_agent(agent_id: str) -> dict[str, Any]:
    await _get_agent(agent_id)
    await runtime.retry(agent_id)
    return {"ok": True}


@router.post("/agents/{agent_id}/reset-memory")
async def reset_agent_memory(agent_id: str) -> dict[str, Any]:
    await _get_agent(agent_id)
    await runtime.reset_memory(agent_id)
    return {"ok": True}


@router.post("/agents/{agent_id}/compact")
async def compact_agent_context(agent_id: str) -> dict[str, Any]:
    await _get_agent(agent_id)
    try:
        return await runtime.compact_now(agent_id)
    except RuntimeBusy as e:
        raise HTTPException(409, str(e)) from e


@router.get("/agents/{agent_id}/thread")
async def agent_thread(agent_id: str) -> dict[str, Any]:
    await _get_agent(agent_id)
    return await runtime.thread_state(agent_id)


# --- relationships -------------------------------------------------------------------------


@router.post("/orgs/{org_id}/relationships", status_code=201)
async def set_relationship(org_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    await _get_org(org_id)
    rel = await orgs.set_relationship(org_id, body.get("fromId", ""), body.get("toId", ""),
                                      body.get("kind", "manages"), body.get("label", ""))
    return orgs.rel_to_dict(rel)


@router.delete("/orgs/{org_id}/relationships/{rel_id}", status_code=204)
async def remove_relationship(org_id: str, rel_id: str) -> None:
    await orgs.remove_relationship(org_id, rel_id)
