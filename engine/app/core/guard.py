r"""Keeping agents out of Pantheon's own data folder and source.

The data folder holds the database (every org's conversations, settings and encrypted
secrets), the uploads and other orgs' workspaces. Agents' file tools are already confined
to their org's workspace; shell commands, the scripts agents write and notebook cells run
as the user, so they are screened here: anything that names the data folder (its path,
its name, or the database and key files) is refused, unless the path is inside the
agent's own workspace. Pantheon's source (when it runs from a checkout) is screened the same
way: a timed run's team, told to run its checks "from PantheonStudio\engine", wrote its
pages, scripts and screenshots into the engine's source folder, and they were committed.

This stops the ordinary ways in. It is not a sandbox: a determined program could still
build the path at run time. The encryption key itself lives in the OS keychain
(app/core/security.py), so the database alone gives away no secrets.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import settings

OFF_LIMITS = ("Pantheon's own data folder (its database, keys and other organizations' files) "
              "and its source are off limits to agents")
SOURCE = Path(__file__).resolve().parents[3]  # the checkout: engine/, ui/, packs/
MARKERS = ("ai.pantheon.studio", "pantheon.db", "pantheon.key", "pantheonstudio\\runtime",
           "pantheonstudio/runtime")


def _variants(path: Path | str) -> list[str]:
    text = str(path).lower().rstrip("\\/")
    return list({text, text.replace("\\", "/"), text.replace("/", "\\"),
                 text.replace("\\", "\\\\")})


def touches_data(text: str, allowed: list[Path] | None = None) -> bool:
    """Whether text (a command, a script, a cell) names Pantheon's data folder outside the
    allowed folders (the agent's own workspace)."""
    if not text:
        return False
    t = text.lower()
    # Pantheon's own Python (its .venv) may run things; that isn't reaching into the source.
    for root in [*(allowed or []), SOURCE / "engine" / ".venv"]:
        for v in _variants(root):
            t = t.replace(v, " ")
    for v in [*_variants(settings.data_path), *_variants(SOURCE)]:
        if v and v in t:
            return True
    return any(m in t for m in MARKERS) or re.search(r"pantheon[ _.-]?studio[\\/]+data", t) is not None


def climbs_out(command: str, cwd: Path, root: Path) -> bool:
    """Whether a command climbs with ../ above the workspace root (the data folder holds
    every workspace, so two levels up from one is the data folder itself)."""
    try:
        depth = len(cwd.resolve().relative_to(root.resolve()).parts)
    except ValueError:
        return True
    runs = re.findall(r"(?:\.\.[\\/])+(?:\.\.)?|\.\.$|\.\.(?=\s)", command)
    return any(r.count("..") > depth for r in runs)


def check(text: str, allowed: list[Path] | None = None, *, cwd: Path | None = None) -> None:
    from app.tools.base import ToolError

    if touches_data(text, allowed) or (cwd is not None and allowed and climbs_out(text, cwd, allowed[0])):
        raise ToolError(OFF_LIMITS + ". Work in your workspace instead.")
