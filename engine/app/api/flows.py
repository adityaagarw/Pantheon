"""Flows (services/flows.py): make, edit and run them, and follow a run."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from app.core.db import SessionLocal
from app.models import FlowRun
from app.services import flows

router = APIRouter(prefix="/api/v1", tags=["flows"])


def _err(e: flows.FlowError) -> HTTPException:
    return HTTPException(404 if "not found" in str(e) else 400, str(e))


@router.get("/orgs/{org_id}/flows")
async def list_flows(org_id: str) -> list[dict[str, Any]]:
    return [flows.flow_to_dict(f) for f in await flows.for_org(org_id)]


@router.post("/orgs/{org_id}/flows", status_code=201)
async def create_flow(org_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        f = await flows.create(org_id, str(body.get("name") or ""),
                               body.get("graph") or {"nodes": [], "edges": []},
                               str(body.get("description") or ""))
    except flows.FlowError as e:
        raise _err(e) from None
    return flows.flow_to_dict(f)


@router.patch("/flows/{flow_id}")
async def update_flow(flow_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return flows.flow_to_dict(await flows.update(flow_id, body))
    except flows.FlowError as e:
        raise _err(e) from None


@router.delete("/flows/{flow_id}", status_code=204)
async def delete_flow(flow_id: str) -> None:
    try:
        await flows.delete(flow_id)
    except flows.FlowError as e:
        raise _err(e) from None


@router.get("/flows/{flow_id}/runs")
async def list_runs(flow_id: str) -> list[dict[str, Any]]:
    return [flows.run_to_dict(r) for r in await flows.runs(flow_id)]


@router.post("/flows/{flow_id}/runs", status_code=201)
async def start_run(flow_id: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        run = await flows.start(flow_id, str(body.get("input") or ""))
    except flows.FlowError as e:
        raise _err(e) from None
    return flows.run_to_dict(await _run(run.id))


async def _run(run_id: str) -> FlowRun:
    async with SessionLocal() as session:
        run = await session.get(FlowRun, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return run


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    return flows.run_to_dict(await _run(run_id))


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict[str, Any]:
    try:
        return flows.run_to_dict(await flows.cancel(run_id))
    except flows.FlowError as e:
        raise _err(e) from None
