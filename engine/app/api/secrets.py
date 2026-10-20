"""Secrets (issue #2): managed by the user; values are write-only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from app.core.db import SessionLocal
from app.models import Org
from app.services import secrets

router = APIRouter(prefix="/api/v1", tags=["secrets"])


async def _org(org_id: str) -> None:
    async with SessionLocal() as session:
        if await session.get(Org, org_id) is None:
            raise HTTPException(404, "org not found")


@router.get("/orgs/{org_id}/secrets")
async def list_secrets(org_id: str) -> list[dict[str, Any]]:
    await _org(org_id)
    return await secrets.list_secrets(org_id)


@router.post("/orgs/{org_id}/secrets", status_code=201)
async def create_secret(org_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    await _org(org_id)
    try:
        return await secrets.create(org_id, body)
    except secrets.SecretError as e:
        raise HTTPException(400, str(e)) from None


@router.patch("/secrets/{secret_id}")
async def update_secret(secret_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return await secrets.update(secret_id, body)
    except secrets.SecretError as e:
        raise HTTPException(404 if "not found" in str(e) else 400, str(e)) from None


@router.delete("/secrets/{secret_id}", status_code=204)
async def delete_secret(secret_id: str) -> None:
    try:
        await secrets.delete(secret_id)
    except secrets.SecretError as e:
        raise HTTPException(404, str(e)) from None


@router.get("/orgs/{org_id}/secrets/usage")
async def secret_usage(org_id: str, limit: int = 100) -> list[dict[str, Any]]:
    await _org(org_id)
    return await secrets.usage(org_id, limit=min(limit, 500))
