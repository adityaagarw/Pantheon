"""Zeus's tools for Stage libraries: see them, add any npm library, give them out."""

from __future__ import annotations

from app import packs
from app.services import npmpack, orgs
from app.tools.base import S, ToolContext, ToolError, arr, obj
from app.tools.meta import _agent, _org, meta

ALL = "all"


@meta("list_libraries", "The Stage libraries installed (pages import them by name), and which "
      "an organization or agent may use. Give `org` (and `agent`) to see their choice.",
      obj({"org": S, "agent": S}))
async def list_libraries(args: dict, ctx: ToolContext) -> str:
    libs = packs.libraries()
    lines = [f"- {lib.id}: {lib.name} — imports {', '.join(lib.imports)}"
             f"{' (core: always on)' if lib.core else ''}. {lib.summary}"
             for lib in libs.values()]
    out = "Installed libraries:\n" + "\n".join(lines)
    if args.get("org"):
        org = await _org(args["org"])
        agent = await _agent(org, args["agent"]) if args.get("agent") else None
        sel = packs.selection_for(org.settings, agent.libraries if agent else None)
        who = f"{agent.name} (in {org.name})" if agent else org.name
        source = "its own choice" if agent and agent.libraries is not None else \
            f"{org.name}'s choice" if (org.settings or {}).get("libraries") is not None else \
            "every installed library"
        out += (f"\n\n{who} uses {source}: "
                f"{', '.join(lib.id for lib in packs.chosen(sel))}")
    return out


@meta("set_libraries", "Choose which Stage libraries an organization's pages may import, or one "
      "agent's (overriding its organization). `libraries`: library ids (list_libraries), or "
      "[\"all\"] for every installed one; for an agent, [] goes back to the organization's "
      "choice. Core libraries (three.js, KaTeX) are always included. Fewer libraries keep "
      "agents focused: they read docs only for what they have.",
      obj({"org": S, "agent": S, "libraries": arr(S)}, ["org", "libraries"]))
async def set_libraries(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    wanted = [str(x).strip() for x in args["libraries"]]
    unknown = [x for x in wanted if x != ALL and x not in packs.libraries()]
    if unknown:
        raise ToolError(f"no library {', '.join(unknown)} (list_libraries shows them; "
                        "install_library adds any npm library)")
    if args.get("agent"):
        agent = await _agent(org, args["agent"])
        if not wanted:
            await orgs.update_agent(agent.id, {"libraries": None})
            return f"{agent.name} uses {org.name}'s libraries again."
        value = list(packs.libraries()) if ALL in wanted else wanted
        await orgs.update_agent(agent.id, {"libraries": value})
        who = f"{agent.name} now uses"
    else:
        value = None if ALL in wanted else wanted  # None: every library, new ones too
        await orgs.update_org(org.id, {"settings": {"libraries": value}})
        who = f"{org.name}'s agents now use"
    return f"{who}: {', '.join(lib.id for lib in packs.chosen(value))}."


@meta("install_library", "Add any npm library to Pantheon as a Stage library pack (downloaded "
      "once, then works offline and in packaged sites), e.g. \"chart.js\", \"matter-js@0.20\", "
      "\"three-globe\". Its README becomes the docs agents read. It's then available to "
      "organizations that use every library; give it to others with set_libraries.",
      obj({"package": {**S, "description": "npm name, optionally @version"}}, ["package"]),
      approval="ask")
async def install_library(args: dict, ctx: ToolContext) -> str:
    try:
        pack, warnings = await npmpack.install(args["package"])
    except (npmpack.NpmError, packs.PackError) as e:
        raise ToolError(str(e)) from None
    lib = pack.libraries[0]
    out = (f"Installed {pack.name} {pack.version} as the library '{lib.id}' "
           f"(import from {', '.join(repr(n) for n in lib.imports if not n.startswith('three/'))}).")
    if lib.requires:
        out += f" It shares {', '.join(lib.requires)} with the Stage."
    if warnings:
        out += "\nWarnings:\n" + "\n".join(f"- {w}" for w in warnings)
    return out

