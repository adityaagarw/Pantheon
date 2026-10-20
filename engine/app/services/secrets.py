"""Secrets: credentials agents use by name without ever seeing the value (issue #2).

* Stored per organization, encrypted, write-only (no API returns a value).
* Scoped: each secret lists the agents that may use it, the hosts HTTP tools may send it
  to, and whether ``run_command`` gets it as an environment variable.
* Used by reference: ``{{secret:NAME}}`` in ``fetch_url`` headers/URL or a custom HTTP
  tool, ``$NAME`` in the shell. The value is put in at the last moment.
* Masked everywhere: tool results, errors, recorded tool calls, messages, memories and
  server logs replace any value with ``[secret:NAME]``.
* Revocable: every use reads the current row, so disabling, ungranting or deleting a
  secret takes effect on the next call.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.ids import utcnow
from app.core.security import decrypt_secret, encrypt_secret
from app.events import bus
from app.models import Agent, Secret, SecretUse

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_REF = r"secret:\s*([A-Za-z][A-Za-z0-9_]{0,63})"
PLACEHOLDER = re.compile(r"\{\{\s*" + _REF + r"\s*\}\}")
# Custom HTTP tools fill templates with str.format, which turns "{{" into "{": placeholders
# are swapped for this brace-free form first and resolved after the fill.
PROTECTED = re.compile(r"⟦" + _REF + r"⟧")
MIN_MASK_LEN = 4  # shorter values would mask ordinary text


class SecretError(ValueError):
    """A secret request that can't be honoured. Messages never contain a value."""


# org id -> [(name, value)], longest value first. Filled at startup and kept in step with
# every change in this process, so masking is synchronous and cheap.
_values: dict[str, list[tuple[str, str]]] = {}


@dataclass
class Grant:
    name: str
    description: str
    domains: list[str]
    shell: bool


def _set_cache(org_id: str, rows: list[Secret]) -> None:
    pairs = []
    for r in rows:
        try:
            value = decrypt_secret(r.value_enc)
        except Exception:  # noqa: BLE001 - sealed with another key: unusable, nothing to mask
            continue
        if len(value) >= MIN_MASK_LEN:
            pairs.append((r.name, value))
    _values[org_id] = sorted(pairs, key=lambda p: -len(p[1]))


async def _reload(org_id: str) -> None:
    async with SessionLocal() as session:
        rows = (await session.execute(select(Secret).where(Secret.org_id == org_id))).scalars().all()
    _set_cache(org_id, list(rows))


async def warm() -> None:
    """Load every org's values for masking (called at startup)."""
    async with SessionLocal() as session:
        rows = (await session.execute(select(Secret))).scalars().all()
    by_org: dict[str, list[Secret]] = {}
    for r in rows:
        by_org.setdefault(r.org_id, []).append(r)
    _values.clear()
    for org_id, items in by_org.items():
        _set_cache(org_id, items)


# --- masking ----------------------------------------------------------------------------


def _forms(value: str) -> list[str]:
    forms = [value]
    if (q := quote(value, safe="")) != value:
        forms.append(q)  # as it appears inside a URL
    return forms


def redact(org_id: str | None, text: str) -> str:
    """Replace any secret value with ``[secret:NAME]``. ``org_id=None`` masks every org's."""
    if not text:
        return text
    pairs = _values.get(org_id, []) if org_id else [p for v in _values.values() for p in v]
    for name, value in pairs:
        for form in _forms(value):
            if form in text:
                text = text.replace(form, f"[secret:{name}]")
    return text


def redact_value(org_id: str | None, value: Any) -> Any:
    """Mask strings anywhere inside JSON-like data (tool arguments)."""
    if isinstance(value, str):
        return redact(org_id, value)
    if isinstance(value, dict):
        return {k: redact_value(org_id, v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(org_id, v) for v in value]
    return value


class RedactingFilter(logging.Filter):
    """Masks secret values in server log lines, including tracebacks."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not _values:
            return True
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        masked = redact(None, message)
        if masked != message:
            record.msg, record.args = masked, None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(None, record.exc_text)
        return True


def install_log_filter() -> None:
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())


# --- management (the user; Zeus can only grant and revoke) --------------------------------


def to_dict(s: Secret, names: dict[str, str] | None = None) -> dict[str, Any]:
    names = names or {}
    return {"id": s.id, "orgId": s.org_id, "name": s.name, "description": s.description,
            "agentIds": list(s.agent_ids or []),
            "agents": [names.get(a, a) for a in s.agent_ids or []],
            "domains": list(s.domains or []), "allowShell": s.allow_shell, "enabled": s.enabled,
            "hasValue": bool(s.value_enc),
            "createdAt": s.created_at.isoformat() if s.created_at else None,
            "updatedAt": s.updated_at.isoformat() if s.updated_at else None,
            "lastUsedAt": s.last_used_at.isoformat() if s.last_used_at else None}


def _clean_domains(domains: Any) -> list[str]:
    out = []
    for d in domains or []:
        d = str(d).strip().lower()
        if not d:
            continue
        host = urlparse(d).hostname if "://" in d else d.split("/")[0]
        if not host or not re.match(r"^(\*\.)?[a-z0-9.-]+(:\d+)?$", host):
            raise SecretError(f"'{d}' is not a host name (e.g. api.github.com or *.example.com)")
        out.append(host)
    return sorted(set(out))


async def _agent_ids(session, org_id: str, refs: Any) -> list[str]:
    from app.services.comms import resolve_agent

    ids = []
    for ref in refs or []:
        agent = await resolve_agent(session, org_id, str(ref))
        if agent is None:
            raise SecretError(f"unknown agent '{ref}'")
        ids.append(agent.id)
    return sorted(set(ids))


async def _names(session, org_id: str) -> dict[str, str]:
    return dict((await session.execute(select(Agent.id, Agent.name).where(
        Agent.org_id == org_id))).all())


async def list_secrets(org_id: str) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (await session.execute(select(Secret).where(Secret.org_id == org_id)
                                      .order_by(Secret.name))).scalars().all()
        names = await _names(session, org_id)
    return [to_dict(s, names) for s in rows]


async def create(org_id: str, data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("name") or "").strip().upper()
    if not NAME_RE.match(name):
        raise SecretError("name must be UPPER_SNAKE_CASE, 2-64 characters, e.g. GITHUB_TOKEN")
    value = str(data.get("value") or "")
    if not value.strip():
        raise SecretError("value is required")
    async with SessionLocal() as session:
        if await session.scalar(select(Secret.id).where(Secret.org_id == org_id, Secret.name == name)):
            raise SecretError(f"a secret named {name} already exists in this organization")
        row = Secret(org_id=org_id, name=name, description=str(data.get("description") or ""),
                     value_enc=encrypt_secret(value),
                     agent_ids=await _agent_ids(session, org_id, data.get("agents")),
                     domains=_clean_domains(data.get("domains")),
                     allow_shell=bool(data.get("allowShell")), enabled=True)
        session.add(row)
        await session.commit()
        names = await _names(session, org_id)
    await _reload(org_id)
    out = to_dict(row, names)
    await bus.publish("secret.changed", out, org_id=org_id)
    return out


async def update(secret_id: str, data: dict[str, Any]) -> dict[str, Any]:
    async with SessionLocal() as session:
        row = await session.get(Secret, secret_id)
        if row is None:
            raise SecretError("secret not found")
        if "value" in data:
            if not str(data["value"] or "").strip():
                raise SecretError("value can't be empty; disable or delete the secret instead")
            row.value_enc = encrypt_secret(str(data["value"]))
        if "description" in data:
            row.description = str(data["description"] or "")
        if "agents" in data:
            row.agent_ids = await _agent_ids(session, row.org_id, data["agents"])
        if "domains" in data:
            row.domains = _clean_domains(data["domains"])
        if "allowShell" in data:
            row.allow_shell = bool(data["allowShell"])
        if "enabled" in data:
            row.enabled = bool(data["enabled"])
        row.updated_at = utcnow()
        await session.commit()
        names = await _names(session, row.org_id)
    await _reload(row.org_id)
    out = to_dict(row, names)
    await bus.publish("secret.changed", out, org_id=row.org_id)
    return out


async def delete(secret_id: str) -> None:
    async with SessionLocal() as session:
        row = await session.get(Secret, secret_id)
        if row is None:
            raise SecretError("secret not found")
        org_id = row.org_id
        await session.delete(row)
        await session.commit()
    # Keep masking the deleted value for this process's lifetime: copies may still surface.
    previous = _values.get(org_id, [])
    await _reload(org_id)
    _values[org_id] = sorted({*previous, *_values.get(org_id, [])}, key=lambda p: -len(p[1]))
    await bus.publish("secret.deleted", {"id": secret_id}, org_id=org_id)


async def set_grant(org_id: str, secret_name: str, agent_ref: str, granted: bool) -> str:
    """Give or take away one agent's use of a secret (what Zeus may do)."""
    async with SessionLocal() as session:
        row = (await session.execute(select(Secret).where(
            Secret.org_id == org_id, Secret.name == secret_name.strip().upper()))).scalar_one_or_none()
        if row is None:
            raise SecretError(f"no secret named {secret_name.upper()} in this organization "
                              "(the user adds secrets in the org's Settings)")
        (agent_id,) = await _agent_ids(session, org_id, [agent_ref])
        ids = set(row.agent_ids or [])
        ids.add(agent_id) if granted else ids.discard(agent_id)
        row.agent_ids = sorted(ids)
        row.updated_at = utcnow()
        await session.commit()
        names = await _names(session, org_id)
    await bus.publish("secret.changed", to_dict(row, names), org_id=org_id)
    return f"{names.get(agent_id, agent_id)} {'can now use' if granted else 'can no longer use'} {row.name}."


# --- use ----------------------------------------------------------------------------------


async def grants_for(org_id: str, agent_id: str) -> list[Grant]:
    async with SessionLocal() as session:
        rows = (await session.execute(select(Secret).where(
            Secret.org_id == org_id, Secret.enabled.is_(True)))).scalars().all()
    return [Grant(r.name, r.description, list(r.domains or []), r.allow_shell)
            for r in rows if agent_id in (r.agent_ids or [])]


def _host_allowed(host: str, domains: list[str]) -> bool:
    host = (host or "").lower()
    return any(host == d or fnmatch.fnmatch(host, d) for d in domains)


async def _record(rows: list[tuple[Secret, str]], agent_id: str, tool: str) -> None:
    if not rows:
        return
    async with SessionLocal() as session:
        now = utcnow()
        for row, target in rows:
            session.add(SecretUse(org_id=row.org_id, secret_id=row.id, secret_name=row.name,
                                  agent_id=agent_id, tool=tool, target=target))
            live = await session.get(Secret, row.id)
            if live is not None:
                live.last_used_at = now
        await session.commit()


async def resolve(org_id: str, agent_id: str, text: str, *, url: str, tool: str,
                  url_encode: bool = False) -> str:
    """Put granted secrets into ``text`` for a request to ``url``. Raises SecretError (with
    no value in it) when a secret is unknown, not granted, disabled or not for that host."""
    names = {m.group(1).upper() for m in PLACEHOLDER.finditer(text)} | \
        {m.group(1).upper() for m in PROTECTED.finditer(text)}
    if not names:
        return text
    host = urlparse(url).hostname or ""
    async with SessionLocal() as session:
        rows = {r.name: r for r in (await session.execute(select(Secret).where(
            Secret.org_id == org_id, Secret.name.in_(names)))).scalars()}
    used: list[tuple[Secret, str]] = []
    values: dict[str, str] = {}
    for name in sorted(names):
        row = rows.get(name)
        if row is None:
            raise SecretError(f"there is no secret named {name}; list_secrets shows yours")
        if not row.enabled:
            raise SecretError(f"{name} is disabled")
        if agent_id not in (row.agent_ids or []):
            raise SecretError(f"you aren't allowed to use {name}; ask the user (or Zeus) for it")
        if not _host_allowed(host, row.domains or []):
            allowed = ", ".join(row.domains or []) or "no hosts yet"
            raise SecretError(f"{name} may only be sent to {allowed}, not {host or 'this URL'}")
        value = decrypt_secret(row.value_enc)
        values[name] = quote(value, safe="") if url_encode else value
        used.append((row, host))

    def fill(m: re.Match) -> str:
        return values[m.group(1).upper()]

    out = PROTECTED.sub(fill, PLACEHOLDER.sub(fill, text))
    await _record(used, agent_id, tool)
    return out


def protect(template: str) -> str:
    """Make placeholders survive a str.format fill (custom HTTP tools)."""
    return PLACEHOLDER.sub(lambda m: f"⟦secret:{m.group(1)}⟧", template)


async def shell_env(org_id: str, agent_id: str, command: str) -> dict[str, str]:
    """Environment variables for run_command: the agent's shell-enabled secrets."""
    async with SessionLocal() as session:
        rows = (await session.execute(select(Secret).where(
            Secret.org_id == org_id, Secret.enabled.is_(True),
            Secret.allow_shell.is_(True)))).scalars().all()
    env, used = {}, []
    for r in rows:
        if agent_id not in (r.agent_ids or []):
            continue
        try:
            env[r.name] = decrypt_secret(r.value_enc)
        except Exception:  # noqa: BLE001
            continue
        if r.name in command:
            used.append((r, "shell"))
    await _record(used, agent_id, "run_command")
    return env


async def usage(org_id: str, limit: int = 100) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (await session.execute(select(SecretUse).where(SecretUse.org_id == org_id)
                                      .order_by(SecretUse.created_at.desc()).limit(limit))).scalars().all()
        names = await _names(session, org_id)
    return [{"secret": u.secret_name, "agent": names.get(u.agent_id, u.agent_id), "tool": u.tool,
             "target": u.target, "at": u.created_at.isoformat() if u.created_at else None}
            for u in rows]
