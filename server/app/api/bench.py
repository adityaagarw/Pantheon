"""Benchmarks: run SwarmBench tasks on copies of an organization, and compare the results."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.models import BenchRun
from app.services import bench

router = APIRouter(prefix="/api/v1", tags=["bench"])


@router.get("/bench/tasks")
async def bench_tasks() -> dict[str, Any]:
    return {"tasks": bench.task_catalog(), "workflows": bench.workflow_catalog()}


@router.get("/bench/runs")
async def bench_runs(org_id: str | None = None) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        stmt = select(BenchRun).order_by(BenchRun.created_at.desc())
        if org_id:
            stmt = stmt.where(BenchRun.org_id == org_id)
        return [bench.run_to_dict(r) for r in (await session.execute(stmt.limit(200))).scalars()]


@router.post("/bench/runs", status_code=201)
async def start_bench_run(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return await bench.start_run(
            str(body.get("orgId") or body.get("org") or ""), tasks=list(body.get("tasks") or []),
            workflow=str(body.get("workflow") or "task"), repeat=int(body.get("repeat") or 1),
            timeout_minutes=float(body["timeoutMinutes"]) if body.get("timeoutMinutes") else None,
            jobs=int(body.get("jobs") or 1), lead=body.get("lead") or None,
            idle_seconds=float(body.get("idleSeconds") or 45))
    except bench.BenchError as e:
        raise HTTPException(400, str(e)) from None


async def _run(run_id: str) -> BenchRun:
    async with SessionLocal() as session:
        r = await session.get(BenchRun, run_id)
    if r is None:
        raise HTTPException(404, "benchmark run not found")
    return r


@router.get("/bench/runs/{run_id}")
async def get_bench_run(run_id: str) -> dict[str, Any]:
    return bench.run_to_dict(await _run(run_id))


@router.post("/bench/runs/{run_id}/cancel")
async def cancel_bench_run(run_id: str) -> dict[str, Any]:
    r = await _run(run_id)
    if r.status != "running":
        return bench.run_to_dict(r)
    return await bench.cancel_run(run_id)


@router.delete("/bench/runs/{run_id}", status_code=204)
async def delete_bench_run(run_id: str) -> None:
    r = await _run(run_id)
    if r.status == "running":
        raise HTTPException(409, "cancel the run first")
    async with SessionLocal() as session:
        await session.execute(delete(BenchRun).where(BenchRun.id == run_id))
        await session.commit()


# --- attempts, for the SwarmBench CLI's pantheon harness ------------------------------------


@router.post("/bench/attempts", status_code=201)
async def start_attempt(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        org = await bench.find_org(str(body.get("org") or ""))
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            raise bench.BenchError("prompt is required")
        att = bench.start_attempt(
            org, prompt, str(body.get("workspace") or ""), label=str(body.get("label") or "task")[:60],
            lead=body.get("lead") or None, timeout=float(body.get("timeoutSeconds") or 3600),
            idle_seconds=float(body.get("idleSeconds") or 45), keep_org=bool(body.get("keepOrg")))
    except bench.BenchError as e:
        raise HTTPException(400, str(e)) from None
    return att.to_dict()


@router.get("/bench/attempts/{attempt_id}")
async def get_attempt(attempt_id: str) -> dict[str, Any]:
    att = bench.ATTEMPTS.get(attempt_id)
    if att is None:
        raise HTTPException(404, "attempt not found (Pantheon may have restarted)")
    return att.to_dict()


@router.post("/bench/attempts/{attempt_id}/cancel")
async def cancel_attempt(attempt_id: str) -> dict[str, Any]:
    att = bench.ATTEMPTS.get(attempt_id)
    if att is None:
        raise HTTPException(404, "attempt not found")
    att.cancel = True
    return att.to_dict()
