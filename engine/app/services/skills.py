"""Skills: instructions an agent opens only when its task calls for them.

harness2 (Google Research, 2026) found on-demand skills the component that mattered most
in an improved harness (removing them cost 5.3 points; always-on prompt text, 1.5), and a
persona that holds everything costs every model call. So a practitioner's know-how for one
kind of work (reconciling sources, spreadsheet deliverables) is a skill: a SKILL.md with a
name, a one-line trigger ("Use when ...") and the instructions, plus any helper files.
An agent's system prompt lists only the triggers of its skills; use_skill opens one.

Skills live in two places: built in (app/skills/<name>/) and the user's own
(<data>/skills/<name>/, made by Zeus with create_skill or by team tuning); a user skill
with a built-in's name replaces it.

Rules, from harness2's checks of the skills its editor wrote: the trigger says when to use
it; helpers ship with the skill and the body names them by path; no template braces left in.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings

BUILTIN = Path(__file__).resolve().parents[1] / "skills"
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$")
MAX_BODY = 20_000
MAX_FILE = 200_000
TRIGGER = re.compile(r"\b(use (it |this )?(when|for|if|before|after)|when\b|whenever\b)", re.I)
BRACES = re.compile(r"\{\{[^}]*\}\}|<<[A-Z_]+>>")


class SkillError(ValueError):
    pass


@dataclass
class Skill:
    name: str
    description: str
    body: str
    folder: Path
    builtin: bool
    files: list[str] = field(default_factory=list)  # helper files, relative to the folder

    def to_dict(self, *, body: bool = False) -> dict:
        out = {"name": self.name, "description": self.description, "builtin": self.builtin,
               "files": self.files}
        return out | ({"body": self.body} if body else {})


def user_dir() -> Path:
    return settings.data_path / "skills"


def _parse(text: str) -> tuple[dict[str, str], str]:
    meta: dict[str, str] = {}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if not m:
        return meta, text.strip()
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        if sep:
            meta[k.strip().lower()] = v.strip().strip("\"'")
    return meta, text[m.end():].strip()


def _load(folder: Path, builtin: bool) -> Skill | None:
    fp = folder / "SKILL.md"
    try:
        meta, body = _parse(fp.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    files = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")
                   if p.is_file() and p.name != "SKILL.md" and "__pycache__" not in p.parts)
    return Skill(meta.get("name") or folder.name, meta.get("description", ""), body, folder,
                 builtin, files)


def all_skills() -> dict[str, Skill]:
    out: dict[str, Skill] = {}
    for root, builtin in ((BUILTIN, True), (user_dir(), False)):
        if not root.is_dir():
            continue
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            if (s := _load(folder, builtin)) is not None:
                out[s.name] = s
    return out


def get(name: str) -> Skill | None:
    return all_skills().get(str(name).strip())


def validate(name: str, description: str, body: str, files: dict[str, str] | None = None) -> None:
    problems = []
    if not NAME.match(name):
        problems.append("name: 3-50 lowercase letters, digits and hyphens")
    if not description.strip() or len(description) > 300:
        problems.append("description: one line of at most 300 characters")
    elif not TRIGGER.search(description):
        problems.append("description must say when to use the skill (\"Use when ...\"): it "
                        "is all an agent sees until it opens the skill")
    if not body.strip():
        problems.append("the instructions are empty")
    elif len(body) > MAX_BODY:
        problems.append(f"the instructions are over {MAX_BODY} characters: split the skill")
    if m := BRACES.search(body):
        problems.append(f"template text left in the instructions: {m.group(0)}")
    files = files or {}
    for rel, text in files.items():
        if rel.startswith(("/", "\\")) or ".." in Path(rel).parts or rel == "SKILL.md":
            problems.append(f"file {rel}: a relative path inside the skill")
        elif len(text) > MAX_FILE:
            problems.append(f"file {rel} is over {MAX_FILE} characters")
    for ref in set(re.findall(r"\b(?:scripts|templates|reference)/[\w./-]+\.\w+", body)):
        if ref not in files:
            problems.append(f"the instructions name {ref}, which the skill doesn't include")
    if problems:
        raise SkillError("; ".join(problems))


def save(name: str, description: str, body: str, files: dict[str, str] | None = None) -> Skill:
    """Make or replace a user skill."""
    name = name.strip()
    validate(name, description, body, files)
    folder = user_dir() / name
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    desc = " ".join(description.split())
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n\n"
                                     f"{body.strip()}\n", encoding="utf-8")
    for rel, text in (files or {}).items():
        fp = folder / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(text, encoding="utf-8")
    skill = _load(folder, False)
    assert skill is not None
    return skill


def delete(name: str) -> None:
    folder = user_dir() / name.strip()
    if not NAME.match(name.strip()) or not folder.is_dir():
        raise SkillError(f"no skill of yours named '{name}' (built-in skills can't be deleted)")
    shutil.rmtree(folder)


def prompt_section(names: list[str]) -> tuple[str, list[str]]:
    """(the system prompt's skills section, warnings for skills that don't exist)."""
    have = all_skills()
    lines, missing = [], []
    for n in dict.fromkeys(str(x) for x in names or []):
        if (s := have.get(n)) is None:
            missing.append(f"skill '{n}' doesn't exist")
        else:
            lines.append(f"- {s.name}: {s.description}")
    if not lines:
        return "", missing
    return ("# Skills\nKnow-how you open when it fits: before you start a part of the work that "
            "a skill's trigger names, open it with use_skill and follow it.\n"
            + "\n".join(lines)), missing


def export(names: list[str]) -> dict[str, dict]:
    """Definitions of the user's own skills among these, for an organization file."""
    out = {}
    for n in names:
        s = get(n)
        if s is not None and not s.builtin:
            out[s.name] = {"description": s.description, "body": s.body,
                           "files": {rel: (s.folder / rel).read_text(encoding="utf-8",
                                                                     errors="replace")
                                     for rel in s.files}}
    return out


def install(defs: dict[str, dict]) -> list[str]:
    """Skills an organization file carries that this computer doesn't have yet."""
    added = []
    for name, d in (defs or {}).items():
        if get(name) is not None or not isinstance(d, dict):
            continue
        try:
            save(name, str(d.get("description") or ""), str(d.get("body") or ""),
                 {str(k): str(v) for k, v in (d.get("files") or {}).items()})
            added.append(name)
        except SkillError:
            continue
    return added


def open_for(skill: Skill, workdir: Path) -> str:
    """The skill's instructions, with its helper files copied where the agent can run them."""
    text = f"# Skill: {skill.name}\n{skill.body}"
    if skill.files:
        dest = Path(workdir) / ".pantheon" / "skills" / skill.name
        for rel in skill.files:
            fp = dest / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(skill.folder / rel, fp)
        rel_dest = f".pantheon/skills/{skill.name}"
        text += (f"\n\nIts files are in {rel_dest}/ (relative to your workdir): "
                 + ", ".join(f"{rel_dest}/{f}" for f in skill.files)
                 + ". Where the instructions say scripts/x, use that path.")
    return text
