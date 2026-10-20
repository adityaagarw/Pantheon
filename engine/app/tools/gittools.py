"""Git for agents whose organization uses it (services/workspaces.py).

Everyone who writes files works in their own worktree on their own branch; leads work on
the base branch. These tools come with git, and nobody needs a shell for them:
git_status, git_commit and git_update for everyone; team_work and merge_work for leads.
"""

from __future__ import annotations

from app.services import workspaces as ws
from app.tools.base import S, ToolContext, ToolError, obj, tool
from app.tools.leadtools import report as _report
from app.tools.leadtools import reports as _reports

AMBIENT = ("git_status", "git_commit", "git_update")
LEAD = ("team_work", "merge_work")


def gtool(name: str, description: str, params: dict):
    return tool(name, description, params, category="git", timeout=300)


def _git_on(ctx: ToolContext) -> dict:
    g = ws.git_settings(ctx.org)
    if not g["enabled"]:
        raise ToolError("this organization doesn't use git")
    return g


def _log(lines: str) -> str:
    return "\n".join(f"  {line}" for line in lines.strip().splitlines()) or "  (none)"


@gtool("git_status", "Your branch, what you've changed and not committed, and how far your "
       "branch is ahead of (and behind) the team's base branch.", obj({}))
async def git_status(args: dict, ctx: ToolContext) -> str:
    g = _git_on(ctx)
    branch = await ws.branch_of(ctx.workdir)
    changes = await ws.changes(ctx.workdir)
    ahead, behind = await ws.ahead_behind(ctx.workspace, g["base"], branch)
    log = await ws.git(ctx.workdir, "log", "-5", "--format=%h %s (%cr)", check=False)
    where = "the base branch (you merge your team's work here)" if branch == g["base"] else \
        f"your own branch, {branch}, in your own worktree"
    out = [f"You're on {where}. Base branch: {g['base']}."]
    if branch != g["base"]:
        out.append(f"{ahead} commit(s) ahead of {g['base']}, {behind} behind"
                   + (" (git_update brings them in)." if behind else "."))
    out.append(f"Not committed ({len(changes)}):\n{_log(chr(10).join(changes[:60]))}")
    out.append(f"Latest commits:\n{_log(log)}")
    return "\n".join(out)


@gtool("git_commit", "Commit everything you've changed in your working directory, with a "
       "message saying what and why. Commit each finished piece of work: your lead merges "
       "commits, not files lying around.", obj({"message": S}, ["message"]))
async def git_commit(args: dict, ctx: ToolContext) -> str:
    _git_on(ctx)
    try:
        sha = await ws.commit(ctx.workdir, str(args["message"]))
    except ws.GitError as e:
        raise ToolError(str(e)) from None
    return f"Committed {sha}." if sha else "Nothing to commit: no changes."


@gtool("git_update", "Bring the team's base branch (what your lead has merged) into your "
       "branch, so you build on everyone's latest work. Commit your own changes first. A "
       "conflict is undone and listed for you to resolve.", obj({}))
async def git_update(args: dict, ctx: ToolContext) -> str:
    g = _git_on(ctx)
    branch = await ws.branch_of(ctx.workdir)
    if branch == g["base"]:
        return "You're on the base branch already."
    if await ws.changes(ctx.workdir):
        raise ToolError("you have uncommitted changes: git_commit them first")
    merged, conflicts, out = await ws.merge(ctx.workdir, g["base"])
    if merged:
        return f"Your branch has the latest {g['base']}." + (f"\n{out[-1500:]}" if out else "")
    return (f"Merging {g['base']} into {branch} conflicts in: {', '.join(conflicts) or '?'}. "
            "Nothing was changed. Ask your lead, or change those files so they fit the "
            "latest work, commit, and try again.")


@gtool("team_work", "For leads: each person you lead, their branch, commits ahead of the base "
       "branch, uncommitted changes and latest commit. Give `person` to see what their "
       "branch changes (a summary and the diff).", obj({"person": S}))
async def team_work(args: dict, ctx: ToolContext) -> str:
    g = _git_on(ctx)
    if args.get("person"):
        a = await _report(ctx, args["person"])
        if not a.worktree:
            raise ToolError(f"{a.name} works on the base branch, with no branch of their own")
        d = await ws.diff(ctx.workspace, g["base"], (ctx.workspace / a.worktree).resolve())
        return (f"{a.name}'s branch {d['branch']} against {g['base']}:\n{d['stat'] or '(no commits yet)'}"
                f"\n\n{d['committed'] or ''}"
                + (f"\n\nNot committed yet:\n{d['uncommitted']}" if d["uncommitted"] else ""))
    reports = await _reports(ctx)
    if not reports:
        return "You don't lead anyone."
    trees = {t["agentId"]: t for t in await ws.worktrees(ctx.org) if t["agentId"]}
    lines = []
    for a in reports:
        t = trees.get(a.id)
        if t is None:
            lines.append(f"- {a.name}: works on the base branch")
            continue
        last = f"latest: {t['last']['subject']}" if t["last"] else "no commits"
        lines.append(f"- {a.name} ({t['branch']}): {t['ahead']} ahead, {t['behind']} behind, "
                     f"{len(t['changes'])} uncommitted; {last}")
    return f"Base branch: {g['base']}.\n" + "\n".join(lines) + \
        "\nmerge_work merges a branch that's ready; team_work with person shows its changes."


@gtool("merge_work", "For leads: merge a person's branch into the base branch, once you've "
       "reviewed it (team_work). A conflict is undone and listed; the person can git_update "
       "and resolve it.", obj({"person": S}, ["person"]))
async def merge_work(args: dict, ctx: ToolContext) -> str:
    g = _git_on(ctx)
    a = await _report(ctx, args["person"])
    if not a.worktree:
        raise ToolError(f"{a.name} has no branch of their own")
    if await ws.branch_of(ctx.workspace) != g["base"]:
        raise ToolError(f"the main checkout isn't on {g['base']}")
    if await ws.changes(ctx.workspace):
        raise ToolError("the main checkout has uncommitted changes: git_commit them first")
    branch = await ws.branch_of((ctx.workspace / a.worktree).resolve())
    merged, conflicts, out = await ws.merge(ctx.workspace, branch)
    if merged:
        return f"Merged {a.name}'s {branch} into {g['base']}."
    return (f"Merging {branch} conflicts in: {', '.join(conflicts) or '?'}. Nothing was "
            f"changed. Ask {a.name} to git_update (bring in {g['base']}), resolve, commit, "
            "and tell you when it's ready.")
