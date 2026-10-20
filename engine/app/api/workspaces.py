"""An organization's workspace: its worktrees, what each changed, merging, and turning git
on for an organization that has a workspace already (services/workspaces.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from app.core.db import SessionLocal
from app.models import Org
from app.services import orgs, workspaces
from app.tools.base import WorkspaceError, org_workspace

router = APIRouter(prefix="/api/v1", tags=["workspaces"])


async def _org(org_id: str) -> Org:
    async with SessionLocal() as session:
        org = await session.get(Org, org_id)
    if org is None:
        raise HTTPException(404, "organization not found")
    return org


def _tree(org: Org, rel: str) -> Path:
    """A worktree of the org by its path relative to the workspace ("." is the main one)."""
    try:
        root = org_workspace(org)
    except WorkspaceError as e:
        raise HTTPException(400, str(e)) from None
    path = (root / rel).resolve()
    if not path.is_relative_to(root) or not path.is_dir():
        raise HTTPException(404, "no such worktree")
    return path


@router.get("/orgs/{org_id}/worktrees")
async def list_worktrees(org_id: str) -> dict[str, Any]:
    org = await _org(org_id)
    try:
        trees = await workspaces.worktrees(org)
    except workspaces.GitError as e:
        raise HTTPException(400, str(e)) from None
    return {"root": str(org_workspace(org)), "git": workspaces.git_settings(org), "worktrees": trees}


@router.get("/orgs/{org_id}/worktrees/diff")
async def worktree_diff(org_id: str, path: str = ".") -> dict[str, Any]:
    org = await _org(org_id)
    g = workspaces.git_settings(org)
    if not g["enabled"]:
        raise HTTPException(400, "this organization doesn't use git")
    return await workspaces.diff(org_workspace(org), g["base"], _tree(org, path))


@router.post("/orgs/{org_id}/worktrees/merge")
async def merge_worktree(org_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Merge a worktree's branch into the base branch (in the main checkout)."""
    org = await _org(org_id)
    g = workspaces.git_settings(org)
    if not g["enabled"]:
        raise HTTPException(400, "this organization doesn't use git")
    root = org_workspace(org)
    branch = await workspaces.branch_of(_tree(org, str(body.get("path") or "")))
    if branch == g["base"]:
        raise HTTPException(400, "that's the base branch itself")
    if await workspaces.changes(root):
        raise HTTPException(409, "the main checkout has uncommitted changes; commit them first")
    merged, conflicts, out = await workspaces.merge(root, branch)
    return {"merged": merged, "branch": branch, "conflicts": conflicts, "output": out[-4000:]}


@router.post("/orgs/{org_id}/workspace")
async def set_up_workspace(org_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Give an existing organization a new workspace, or turn on git and worktrees in the
    one it has ({"source": "current"})."""
    await _org(org_id)
    try:
        org = await workspaces.setup(org_id, body)
    except (workspaces.WorkspaceSetupError, workspaces.GitError, orgs.OrgError) as e:
        raise HTTPException(400, str(e)) from None
    return orgs.org_to_dict(org)
