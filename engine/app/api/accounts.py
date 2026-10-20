"""Accounts for command-line agents (services/accounts.py): Settings → Accounts."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from app.services import accounts

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])


def _fail(e: accounts.AccountError) -> HTTPException:
    return HTTPException(404 if "not found" in str(e) else 400, str(e))


@router.get("")
async def list_accounts() -> dict[str, Any]:
    return {"accounts": await accounts.list_accounts(), "tools": accounts.tools()}


@router.post("", status_code=201)
async def create(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return await accounts.create(body)
    except accounts.AccountError as e:
        raise _fail(e) from None


@router.patch("/{account_id}")
async def patch(account_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return await accounts.patch(account_id, body)
    except accounts.AccountError as e:
        raise _fail(e) from None


@router.delete("/{account_id}", status_code=204)
async def delete(account_id: str) -> None:
    try:
        await accounts.delete(account_id)
    except accounts.AccountError as e:
        raise _fail(e) from None


@router.post("/{account_id}/check")
async def check(account_id: str) -> dict[str, Any]:
    try:
        return await accounts.check(account_id)
    except accounts.AccountError as e:
        raise _fail(e) from None


@router.post("/{account_id}/sign-in")
async def sign_in(account_id: str) -> dict[str, Any]:
    """Start the tool's own login; follow it with GET /sign-ins/{id}."""
    try:
        return await accounts.sign_in(account_id)
    except accounts.AccountError as e:
        raise _fail(e) from None


@router.get("/sign-ins/{signin_id}")
async def sign_in_view(signin_id: str) -> dict[str, Any]:
    try:
        return accounts.sign_in_view(signin_id)
    except accounts.AccountError as e:
        raise HTTPException(404, str(e)) from None


@router.post("/sign-ins/{signin_id}/input")
async def sign_in_input(signin_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return await accounts.sign_in_input(signin_id, str(body.get("text") or ""))
    except accounts.AccountError as e:
        raise HTTPException(400, str(e)) from None


@router.delete("/sign-ins/{signin_id}", status_code=204)
async def sign_in_cancel(signin_id: str) -> None:
    await accounts.sign_in_cancel(signin_id)
