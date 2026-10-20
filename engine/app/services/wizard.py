"""The new-organization wizard's helper: Zeus drafts an organization (or one agent's prompt
and tools) from what the user wants, as a definition the user then edits and creates.

One model call on Zeus's model, no tools and no thread: the wizard is a form, and Zeus
fills it in. Everything it returns is checked against the real tool catalog.
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.db import SessionLocal
from app.models import Agent, Org
from app.tools import base
from app.tools.loadout import CORE, first_sentence

KINDS = {
    "solo": "one capable agent who does the whole job",
    "team": "a small team: one lead who plans, splits the work and takes a share, and a few "
            "specialists who build in parallel",
    "flow": "a flow: a fixed sequence of steps, each one agent doing one thing",
}
# Always granted: what every agent works with (loadout.CORE, without the loader).
BASE_TOOLS = sorted(CORE - {"load_tools"})

SYSTEM = """You are Zeus, the Pantheon supervisor, helping the user set up a new organization
of AI agents in a wizard. Reply with one JSON object and nothing else.

{lessons}
- Every agent already has {base}. Add other tools only by exact name from the catalog
  below. No filler in personas, no headings in capitals.

Tool catalog:
{catalog}

Skills (know-how an agent opens when its trigger fits; give by exact name):
{skills}"""

ORG_TASK = """The user wants: {goal}
Kind of organization: {kind}.
{current}{ask}
Reply with JSON:
{{"name": "short org name", "description": "2-3 sentences every agent sees: what this
organization is for and its standing rules",
 "agents": [{{"name": "a first name", "role": "job title", "lead": true or false,
   "persona": "...", "tools": ["extra tool names"], "skills": ["skill names"]}}],
 "settings": {{"max_concurrent": number of agents that should work at once}}}}"""

AGENT_TASK = """The organization: {org}
The agent to work on: {agent}
{ask}
Improve this agent's persona and tools for the organization's goal (keep its name). Reply
with JSON: {{"name": "...", "role": "...", "persona": "...", "tools": ["extra tool names"],
"skills": ["skill names"]}}"""


class WizardError(ValueError):
    pass


def catalog() -> list[base.ToolSpec]:
    return [t for t in base.builtin_catalog() if not t.supervisor_only and t.name != "load_tools"]


def _catalog_text() -> str:
    return "\n".join(f"- {t.name} ({t.category}): {first_sentence(t.description, 90)}"
                     for t in catalog() if t.name not in BASE_TOOLS)


async def _zeus_model() -> tuple[Any, str]:
    from app.llm.providers import resolve_for_agent
    from app.services.supervisor import SYSTEM_ORG_ID, supervisor_id

    zid = await supervisor_id()
    async with SessionLocal() as session:
        zeus = await session.get(Agent, zid)
        org = await session.get(Org, SYSTEM_ORG_ID)
    return await resolve_for_agent(zeus, org), zid


def _json(text: str) -> dict[str, Any]:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise WizardError("Zeus didn't reply with a draft; try again")
    try:
        out = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        raise WizardError("Zeus's draft wasn't valid JSON; try again") from None
    if not isinstance(out, dict):
        raise WizardError("Zeus's draft wasn't an object; try again")
    return out


def _agent(a: dict[str, Any]) -> dict[str, Any]:
    from app.services import skills

    known = {t.name for t in catalog()}
    extra = [str(n) for n in a.get("tools") or [] if str(n) in known and str(n) not in BASE_TOOLS]
    have = skills.all_skills()
    return {"name": str(a.get("name") or "Agent").strip()[:40],
            "role": str(a.get("role") or "").strip()[:80],
            "lead": bool(a.get("lead")),
            "persona": str(a.get("persona") or "").strip(),
            "tools": [*BASE_TOOLS, *dict.fromkeys(extra)],
            "skills": list(dict.fromkeys(str(n) for n in a.get("skills") or [] if str(n) in have))}


async def _ask(task: str) -> dict[str, Any]:
    from app.agents.llm import LLMFailure, invoke, text_of

    model, zid = await _zeus_model()
    from app.agents.prompt import ORG_DESIGN_LESSONS
    from app.services import experience

    lessons = ORG_DESIGN_LESSONS + ("\n\n" + library if (library := experience.prompt_index())
                                    else "")
    from app.services import skills

    system = SYSTEM.format(lessons=lessons, base=", ".join(BASE_TOOLS),
                           catalog=_catalog_text(),
                           skills="\n".join(f"- {s.name}: {s.description}"
                                            for s in skills.all_skills().values()) or "(none)")
    try:
        ai, _ = await invoke(model, [SystemMessage(content=system), HumanMessage(content=task)],
                             org_id="org_pantheon", agent_id=zid, turn_id=None,
                             purpose="wizard", stream=False)
    except LLMFailure as e:
        raise WizardError(f"Zeus couldn't be reached: {e}") from None
    return _json(text_of(ai))


async def draft_org(goal: str, kind: str, current: dict[str, Any] | None = None,
                    ask: str = "") -> dict[str, Any]:
    """A whole organization: name, description, agents (with personas and tools), settings."""
    if not goal.strip():
        raise WizardError("say what the organization is for")
    kind = kind if kind in KINDS else "team"
    out = await _ask(ORG_TASK.format(
        goal=goal.strip(), kind=KINDS[kind],
        current=(f"The current draft, to revise:\n{json.dumps(current)[:12000]}\n"
                 if current else ""),
        ask=f"The user asks: {ask.strip()}\n" if ask.strip() else ""))
    agents = [_agent(a) for a in out.get("agents") or [] if isinstance(a, dict)][:5]
    if not agents:
        raise WizardError("Zeus's draft had no agents; try again")
    if kind == "solo":
        agents = agents[:1]
    if len(agents) > 1 and not any(a["lead"] for a in agents):
        agents[0]["lead"] = True
    for a in agents if len(agents) == 1 else []:
        a["lead"] = False
    settings = out.get("settings") if isinstance(out.get("settings"), dict) else {}
    conc = settings.get("max_concurrent")
    return {"name": str(out.get("name") or "").strip()[:80],
            "description": str(out.get("description") or "").strip(),
            "agents": agents,
            "settings": {"max_concurrent": max(1, min(int(conc), 8))}
            if isinstance(conc, int) else {}}


async def draft_agent(org: dict[str, Any], agent: dict[str, Any], ask: str = "") -> dict[str, Any]:
    """One agent's persona and tools, improved for the organization's goal."""
    out = await _ask(AGENT_TASK.format(
        org=json.dumps({k: org.get(k) for k in ("goal", "name", "description")})[:4000],
        agent=json.dumps(agent)[:8000],
        ask=f"The user asks: {ask.strip()}" if ask.strip() else ""))
    improved = _agent({**agent, **out, "name": agent.get("name") or out.get("name")})
    improved["lead"] = bool(agent.get("lead"))
    return improved
