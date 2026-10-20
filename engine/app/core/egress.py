"""Hosts an organization's agents may not reach (settings.blocked_hosts).

A benchmark's answers are often public: JobBench's rubrics are on Hugging Face, and in
harness2's own validation an agent with web access fetched its task's rubric unasked. So a
benchmark copy of an organization blocks those hosts for fetch_url, web_search results, the
browser and, by name, run_command. An entry is a host ("huggingface.co", which covers its
subdomains) or a host with a path prefix ("github.com/Job-Bench").
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# Agents whose browser session must refuse these hosts (the browser guard has no org).
_by_agent: dict[str, tuple[str, ...]] = {}


def hosts(org: Any) -> tuple[str, ...]:
    raw = (getattr(org, "settings", None) or {}).get("blocked_hosts") or ()
    if isinstance(raw, str):
        raw = re.split(r"[\s,]+", raw)
    return tuple(h.strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
                 for h in raw if isinstance(h, str) and h.strip())


def _match(entry: str, host: str, path: str) -> bool:
    want_host, _, want_path = entry.partition("/")
    if host != want_host and not host.endswith("." + want_host):
        return False
    return not want_path or path.lower().lstrip("/").startswith(want_path.lower())


def blocked_url(url: str, entries: tuple[str, ...]) -> str | None:
    """The entry that blocks this URL, or None."""
    if not entries:
        return None
    p = urlparse(url if "://" in url else "https://" + url)
    host = (p.hostname or "").lower()
    return next((e for e in entries if _match(e, host, p.path)), None)


def check(org: Any, url: str) -> None:
    """Raise the tool error a blocked URL gets."""
    from app.tools.base import ToolError

    if entry := blocked_url(url, hosts(org)):
        raise ToolError(f"{entry} is blocked for this organization (settings.blocked_hosts)")


def blocked_command(org: Any, command: str) -> str | None:
    """The entry a shell command names, or None (curl, wget, git clone, Invoke-WebRequest...)."""
    text = command.lower()
    for entry in hosts(org):
        host, _, path = entry.partition("/")
        for m in re.finditer(re.escape(host), text):
            before = text[m.start() - 1] if m.start() else " "
            if before.isalnum() or before == "-":
                continue  # part of another name: notmyhuggingface.co
            if not path or text[m.end():].lstrip("/").startswith(path.lower()):
                return entry
    return None


def register(agent_id: str, org: Any) -> None:
    """Remember an agent's blocked hosts for its browser session."""
    entries = hosts(org)
    if entries:
        _by_agent[agent_id] = entries
    else:
        _by_agent.pop(agent_id, None)


def blocked_for(agent_id: str, url: str) -> str | None:
    return blocked_url(url, _by_agent.get(agent_id, ()))
