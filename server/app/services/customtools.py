"""Tools defined at runtime, without code (Zeus creates them; the user can too).

Three kinds:

* ``action``: something a person does in the physical space ("hand out a flyer",
  "buy a coffee"). Its ``narration`` template (``{actor}``, ``{target}``,
  ``{room}`` and any argument) is what happens; the target and, optionally,
  everyone in the room are told. ``requires_object`` (a name glob) makes the
  actor hold something first.
* ``http``: call a web API. ``url``/``body`` templates take the arguments;
  granted with approval "ask" by default.
* ``prompt``: a reusable skill run by the calling agent's own model with fixed
  ``instructions`` (e.g. "rewrite as a press release").

Custom tools are registered into the same registry as built-ins, so they are
granted per agent and approved exactly like any other tool.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import re
import string
from typing import Any
from urllib.parse import quote

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError as JsonSchemaError
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select

from app.core.db import SessionLocal
from app.events import bus
from app.models import CustomTool
from app.tools import base
from app.tools.base import ToolContext, ToolError, ToolSpec

log = logging.getLogger(__name__)
KINDS = ("action", "http", "prompt")
NAME = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
DEFAULT_PARAMS = {"type": "object", "properties": {
    "target": {"type": "string", "description": "who it's done to or with (optional)"},
    "details": {"type": "string", "description": "anything else worth saying (optional)"}},
    "additionalProperties": False}
_OURS: set[str] = set()


class CustomToolError(ValueError):
    pass


class _Blank(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _fill(template: str, values: dict[str, Any], *, url: bool = False) -> str:
    safe = {k: (quote(str(v), safe="") if url else str(v)) for k, v in values.items()}
    return string.Formatter().vformat(template, (), _Blank(safe))


def validate(name: str, description: str, kind: str, parameters: dict[str, Any] | None,
             config: dict[str, Any] | None, approval: str) -> tuple[dict, dict]:
    if not NAME.match(name or ""):
        raise CustomToolError("name must be snake_case: lowercase letters, digits, underscores")
    if name not in _OURS and base.builtin(name) is not None:
        raise CustomToolError(f"'{name}' is a built-in tool; pick another name")
    if not (description or "").strip():
        raise CustomToolError("describe what the tool does (agents read this)")
    if kind not in KINDS:
        raise CustomToolError(f"kind must be one of {', '.join(KINDS)}")
    if approval not in base.APPROVALS:
        raise CustomToolError(f"approval must be one of {', '.join(base.APPROVALS)}")
    params = dict(parameters or DEFAULT_PARAMS)
    params.setdefault("type", "object")
    try:
        Draft202012Validator.check_schema(params)
    except JsonSchemaError as e:
        raise CustomToolError(f"invalid parameters schema: {e.message}") from None
    cfg = dict(config or {})
    if kind == "action" and not str(cfg.get("narration") or "").strip():
        raise CustomToolError("action tools need a narration, e.g. \"{actor} waves at {target}\"")
    if kind == "http":
        if not re.match(r"^https?://", str(cfg.get("url") or "")):
            raise CustomToolError("http tools need an http(s) url")
        cfg["method"] = str(cfg.get("method") or "GET").upper()
        if cfg["method"] not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            raise CustomToolError("method must be GET, POST, PUT, PATCH or DELETE")
    if kind == "prompt" and not str(cfg.get("instructions") or "").strip():
        raise CustomToolError("prompt tools need instructions")
    return params, cfg


# --- execution ---------------------------------------------------------------------------


async def _run_action(row: CustomTool, args: dict[str, Any], ctx: ToolContext) -> str:
    from app.services import comms, spatial
    from app.services.comms import Sender

    cfg = row.config or {}
    w = await spatial.load_world(ctx.org.id)
    me = w.by_id.get(ctx.agent.id, ctx.agent)
    room = w.room_of(me)
    if pattern := str(cfg.get("requires_object") or "").strip().lower():
        if not any(o.holder_id == me.id and fnmatch.fnmatchcase(o.name.lower(), pattern)
                   for o in w.objects):
            raise ToolError(f"you need to be holding {pattern.strip('*')} to do that")
    target = None
    if args.get("target"):
        async with SessionLocal() as session:
            target = await comms.resolve_agent(session, ctx.org.id, str(args["target"]))
        if target is None:
            raise ToolError(f"no one called '{args['target']}'")
        target = w.by_id.get(target.id, target)
        if w.room_of(target) != room:
            raise ToolError(f"{target.name} is {w.where(target)}, not here")
    text = _fill(str(cfg["narration"]), {**args, "actor": me.name,
                                         "target": target.name if target else "",
                                         "room": room}).strip()
    await bus.publish("world.action", {"action": row.name, "text": text,
                                       "targetId": target.id if target else None},
                      org_id=ctx.org.id, agent_id=me.id)
    told: set[str] = {me.id}
    if target is not None:
        told.add(target.id)
        await comms.post(ctx.org.id, Sender.system(ctx.depth), text, to_agents=[target.id],
                         kind="observation", meta={"room": room})
    if cfg.get("witness", True):
        others = [a.id for a in w.people_in(room) if a.id not in told]
        if others:
            await comms.post(ctx.org.id, Sender.system(ctx.depth), text, to_agents=others,
                             kind="observation", meta={"room": room})
    return text


async def _run_http(row: CustomTool, args: dict[str, Any], ctx: ToolContext) -> str:
    cfg = row.config or {}
    url = _fill(str(cfg["url"]), args, url=True)
    headers = {str(k): _fill(str(v), args) for k, v in (cfg.get("headers") or {}).items()}
    body = cfg.get("body")
    kwargs: dict[str, Any] = {"headers": headers}
    if isinstance(body, str) and body:
        kwargs["content"] = _fill(body, args)
    elif isinstance(body, dict):
        kwargs["json"] = json.loads(_fill(json.dumps(body), args))
    elif cfg["method"] != "GET":
        kwargs["json"] = args
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            res = await client.request(cfg["method"], url, **kwargs)
    except httpx.HTTPError as e:
        raise ToolError(f"request failed: {e}") from None
    return base.truncate(f"HTTP {res.status_code}\n{res.text}")


async def _run_prompt(row: CustomTool, args: dict[str, Any], ctx: ToolContext) -> str:
    from app.agents import llm
    from app.llm.providers import resolve_for_agent

    model = await resolve_for_agent(ctx.agent, ctx.org)
    ai, _ = await llm.invoke(
        model, [SystemMessage(content=str(row.config["instructions"])),
                HumanMessage(content=json.dumps(args, ensure_ascii=False, indent=1))],
        org_id=ctx.org.id, agent_id=ctx.agent.id, turn_id=ctx.turn_id, purpose="tool",
        stream=False)
    return llm.text_of(ai).strip() or "(no output)"


RUNNERS = {"action": _run_action, "http": _run_http, "prompt": _run_prompt}


def _register(row: CustomTool) -> None:
    snapshot = CustomTool(name=row.name, description=row.description, kind=row.kind,
                          parameters=row.parameters, config=row.config, approval=row.approval,
                          created_by=row.created_by)

    async def handler(args: dict, ctx: ToolContext) -> str:
        return await RUNNERS[snapshot.kind](snapshot, args, ctx)

    params = dict(row.parameters or DEFAULT_PARAMS)
    params.setdefault("additionalProperties", False)
    base.register(ToolSpec(row.name, row.description, params, handler, category="custom",
                           default_approval=row.approval, timeout=90.0,
                           side_effects=row.kind == "http"))
    _OURS.add(row.name)


def to_dict(row: CustomTool) -> dict[str, Any]:
    return {"name": row.name, "description": row.description, "kind": row.kind,
            "parameters": row.parameters, "config": row.config, "approval": row.approval,
            "createdBy": row.created_by,
            "createdAt": row.created_at.isoformat() if row.created_at else None}


async def load_all() -> int:
    async with SessionLocal() as session:
        rows = (await session.execute(select(CustomTool))).scalars().all()
    for row in rows:
        try:
            _register(row)
        except Exception:  # noqa: BLE001 - one bad definition must not stop startup
            log.exception("custom tool %s failed to register", row.name)
    return len(rows)


async def save(name: str, description: str, kind: str, parameters: dict | None,
               config: dict | None, approval: str = "", created_by: str = "user") -> CustomTool:
    approval = approval or ("ask" if kind == "http" else "auto")
    params, cfg = validate(name, description, kind, parameters, config, approval)
    async with SessionLocal() as session:
        row = await session.get(CustomTool, name)
        if row is None:
            row = CustomTool(name=name)
            session.add(row)
        row.description, row.kind, row.parameters, row.config = description.strip(), kind, \
            params, cfg
        row.approval, row.created_by = approval, created_by
        await session.commit()
    _register(row)
    await bus.publish("tool.custom.saved", to_dict(row))
    return row


async def delete(name: str) -> None:
    async with SessionLocal() as session:
        row = await session.get(CustomTool, name)
        if row is None:
            raise CustomToolError(f"no custom tool '{name}'")
        await session.delete(row)
        await session.commit()
    base.unregister(name)
    _OURS.discard(name)
    await bus.publish("tool.custom.deleted", {"name": name})


async def all_tools() -> list[CustomTool]:
    async with SessionLocal() as session:
        return list((await session.execute(select(CustomTool).order_by(CustomTool.name)))
                    .scalars())
