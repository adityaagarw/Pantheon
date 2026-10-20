"""Skills (services/skills.py): agents open theirs; Zeus makes, lists and removes them."""

from __future__ import annotations

import asyncio
import json

from app.services import skills
from app.tools.base import S, ToolContext, ToolError, obj, tool
from app.tools.meta import meta


@tool("use_skill", "Open one of your skills (listed under Skills in your instructions) and "
      "get its full instructions and helper files. Open it before the part of the work its "
      "trigger names.", obj({"name": S}, ["name"]), category="general")
async def use_skill(args: dict, ctx: ToolContext) -> str:
    name = str(args["name"]).strip()
    mine = [str(n) for n in ctx.agent.skills or []]
    if name not in mine:
        raise ToolError(f"you have no skill named '{name}'; yours: {', '.join(mine) or 'none'}")
    skill = skills.get(name)
    if skill is None:
        raise ToolError(f"the skill '{name}' no longer exists")
    return await asyncio.to_thread(skills.open_for, skill, ctx.workdir)


@meta("list_skills", "List the skills agents can be given (built in and the user's own): "
      "name, trigger and helper files. Pass `name` for one skill's full instructions.",
      obj({"name": S}))
async def list_skills(args: dict, ctx: ToolContext) -> str:
    if args.get("name"):
        s = skills.get(str(args["name"]))
        if s is None:
            raise ToolError(f"no skill '{args['name']}'")
        return json.dumps(s.to_dict(body=True), indent=1, ensure_ascii=False)
    rows = [f"- {s.name}{' (built in)' if s.builtin else ''}: {s.description}"
            + (f" [files: {', '.join(s.files)}]" if s.files else "")
            for s in skills.all_skills().values()]
    return "\n".join(rows) or "No skills yet."


@meta("create_skill", "Make (or replace) a skill: know-how an agent opens only when its task "
      "calls for it, instead of carrying it in its persona on every call. Prefer a skill to "
      "persona text for domain practice that only some tasks need. `description` is one "
      "line saying when to use it (\"Use when ...\"): it is all agents see until they open "
      "it. `body` is the instructions; `files` maps helper paths (scripts/check.py) to their "
      "text, and the body names them by those paths. Give it to agents with update_agent "
      "skills.",
      obj({"name": S, "description": S, "body": S,
           "files": {"type": "object", "additionalProperties": S}},
          ["name", "description", "body"]))
async def create_skill(args: dict, ctx: ToolContext) -> str:
    try:
        s = skills.save(str(args["name"]), str(args["description"]), str(args["body"]),
                        {str(k): str(v) for k, v in (args.get("files") or {}).items()})
    except skills.SkillError as e:
        raise ToolError(str(e)) from None
    return f"Saved the skill {s.name}" + (f" with {', '.join(s.files)}" if s.files else "") + "."


@meta("delete_skill", "Remove one of the user's skills (agents that had it are told it's gone "
      "in their tool warnings).", obj({"name": S}, ["name"]))
async def delete_skill(args: dict, ctx: ToolContext) -> str:
    try:
        skills.delete(str(args["name"]))
    except skills.SkillError as e:
        raise ToolError(str(e)) from None
    return f"Deleted the skill {args['name']}."
