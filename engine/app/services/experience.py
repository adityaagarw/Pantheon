"""The experience library: what tuning teams on real work taught, per kind of work.

After harness2's per-domain library (Google Research, 2026): each time a team is tuned on a
task (services/tuning.py), the team it adopted is stored with why, what each edit did and one
transferable lesson. Zeus reads the library before designing or changing an organization for
that kind of work, so a lesson learned on one task carries to the next. In harness2, leaving
the library out cost 1.8 to 3.1 points, and libraries carried over between benchmarks.

    <data>/experience/<domain>/INDEX.md               one line per entry: its cue
    <data>/experience/<domain>/<entry>/EXPERIENCE.md   why, the edits' verdicts, the lesson
    <data>/experience/<domain>/<entry>/team.json       the adopted organization definition

A lesson has a cue (when it applies), the lesson, its evidence and a confidence; an edit's
verdict is helped, hurt, no observed effect or confounded (one run can't separate effects
that changed together, and saying so is better than crediting the wrong one). Nothing from a
grader goes in: the composer that writes these never sees scores or rubrics, and add() refuses
text that names them.

ORG_DESIGN_LESSONS (agents/prompt.py) stays as the general rules every design starts from;
the library holds what is specific to a kind of work.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.core.config import settings

VERDICTS = ("helped", "hurt", "no observed effect", "confounded")
INDEX_CHARS = 3500  # of the library in Zeus's prompt
# A grader's words in a lesson mean the evidence leaked (harness2: answer-free editing).
GRADER = re.compile(r"\b(rubric|hidden[ _]tests?|grader|graded|score[sd]?\s*(of|=|:)?\s*\d)", re.I)


class ExperienceError(ValueError):
    pass


def root() -> Path:
    return settings.data_path / "experience"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:60] or "general"


def domains() -> list[str]:
    r = root()
    return sorted(p.name for p in r.iterdir() if p.is_dir()) if r.is_dir() else []


def entries(domain: str) -> list[str]:
    d = root() / slug(domain)
    return sorted(p.name for p in d.iterdir() if (p / "EXPERIENCE.md").is_file()) if d.is_dir() else []


def has_lesson(lesson: dict | None) -> bool:
    return isinstance(lesson, dict) and all(
        isinstance(lesson.get(k), str) and lesson[k].strip() for k in ("cue", "lesson", "evidence"))


def add(domain: str, key: str, *, team: dict[str, Any], why: str, edits: list[dict[str, Any]],
        lesson: dict[str, Any], source: str = "") -> Path:
    """Store one adopted team with why it was adopted, and rebuild the domain's INDEX.md."""
    if not has_lesson(lesson):
        raise ExperienceError("a library entry needs a lesson with a cue, the lesson and its evidence")
    text = json.dumps([why, edits, lesson], ensure_ascii=False)
    if m := GRADER.search(text):
        raise ExperienceError(f"the entry names grading ({m.group(0)!r}); lessons come from "
                              "what the runs showed, never from a grader")
    d = root() / slug(domain) / slug(key)
    d.mkdir(parents=True, exist_ok=True)
    lines = [f"# {key}", "", f"- kind of work: {domain}"]
    if source:
        lines.append(f"- from: {source}")
    lines.append("")
    if why.strip():
        lines += ["## Why this team was adopted", why.strip(), ""]
    if edits:
        lines.append("## What each edit did")
        for e in edits:
            verdict = str(e.get("verdict") or "confounded")
            lines.append(f"- [{verdict if verdict in VERDICTS else 'confounded'}] "
                         f"{str(e.get('summary') or e.get('change') or '').strip()}"
                         + (f" ({str(e.get('evidence')).strip()})" if e.get("evidence") else ""))
        lines.append("")
    lines.append("## Transferable lesson")
    lines += [f"- **{f}**: {' '.join(str(lesson[f]).split())}"
              for f in ("cue", "lesson", "evidence", "confidence") if lesson.get(f)]
    (d / "EXPERIENCE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (d / "team.json").write_text(json.dumps(team, indent=1, ensure_ascii=False), encoding="utf-8")
    rebuild_index(domain)
    return d


def _field(path: Path, name: str) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"- **{name}**:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return ""


def rebuild_index(domain: str) -> Path:
    d = root() / slug(domain)
    rows = [f"# Experience: {domain}", "",
            "Each entry is a team tuned on a task of this kind, with what each change did and "
            "one lesson. Read the entries whose cue fits the work in front of you.", ""]
    for name in entries(domain):
        exp = d / name / "EXPERIENCE.md"
        rows += [f"## {name}", f"- cue: {_field(exp, 'cue')}",
                 f"- lesson: {_field(exp, 'lesson')}",
                 f"- confidence: {_field(exp, 'confidence') or 'not stated'}", ""]
    d.mkdir(parents=True, exist_ok=True)
    (d / "INDEX.md").write_text("\n".join(rows), encoding="utf-8")
    return d / "INDEX.md"


def read(domain: str = "", entry: str = "") -> str:
    """The whole library's domains, one domain's index, or one entry."""
    if not domain:
        ds = domains()
        if not ds:
            return "The experience library is empty: no team has been tuned yet (tune_team)."
        return "\n".join(f"- {d}: {len(entries(d))} entr{'y' if len(entries(d)) == 1 else 'ies'}"
                         for d in ds)
    d = root() / slug(domain)
    if not d.is_dir():
        raise ExperienceError(f"no experience for '{domain}'; kinds of work: "
                              + (", ".join(domains()) or "none yet"))
    if not entry:
        return (d / "INDEX.md").read_text(encoding="utf-8")
    e = d / slug(entry)
    if not (e / "EXPERIENCE.md").is_file():
        raise ExperienceError(f"no entry '{entry}' under {domain}; entries: {', '.join(entries(domain))}")
    text = (e / "EXPERIENCE.md").read_text(encoding="utf-8")
    try:
        team = json.loads((e / "team.json").read_text(encoding="utf-8"))
        roster = [f"- {a.get('name')} ({a.get('role') or 'no role'}): {len(a.get('persona') or '')} "
                  f"character persona; skills {', '.join(a.get('skills') or []) or 'none'}"
                  for a in team.get("agents") or []]
        text += "\n## The adopted team\n" + "\n".join(roster) + \
            f"\n(The full definition: {(e / 'team.json').as_posix()}.)"
    except (OSError, ValueError):
        pass
    return text


def prompt_index() -> str:
    """For Zeus: each kind of work in the library with its entries' cues and lessons."""
    rows: list[str] = []
    for dom in domains():
        d = root() / dom
        for name in entries(dom):
            exp = d / name / "EXPERIENCE.md"
            rows.append(f"- {dom} / {name}: when {_field(exp, 'cue')} -> {_field(exp, 'lesson')}")
    if not rows:
        return ""
    text = "\n".join(rows)
    if len(text) > INDEX_CHARS:
        text = text[:INDEX_CHARS].rsplit("\n", 1)[0] + "\n- (more: read_experience)"
    return ("# Experience library\nLessons from tuning teams on real tasks, by kind of work. "
            "Before designing or changing an organization for one of these, read_experience "
            "its entries and follow the lessons that fit.\n" + text)
