"""Import outside benchmarks as SwarmBench tasks graded by a rubric (rubric.py).

- JobBench (https://huggingface.co/datasets/JobBench/job-bench, MIT): white-collar tasks from
  about 35 professions; each has instructions, input files and a weighted rubric. The "main"
  split has 65 tasks, "easy" 63 shorter ones.
- LAB (https://github.com/harveyai/harvey-labs, MIT, by Harvey): legal tasks by practice area,
  each with a data room of documents and pass/fail criteria. The repository is large (about
  2 GB), so pick practice areas (`--areas`).

These are the benchmarks harness2 (Google Research, 2026) measured on. Each task's rubric
stays outside its repo/ (the agents' starting files), and its task.json names the hosts where
the rubric is public, which Pantheon blocks while the task runs: in harness2's validation an
agent with web access downloaded its task's rubric unasked.

Imported tasks go to ~/.swarmbench/tasks-external (or SWARMBENCH_IMPORT_DIR); add that
folder to SWARMBENCH_TASK_DIRS to run them (Pantheon does this itself).
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

HF = "https://huggingface.co"
JB_REPO = "JobBench/job-bench"
JB_SPLITS = {"main": "dataset", "easy": "dataset_easy"}
JB_BLOCK = ["huggingface.co", "hf.co", "github.com/Job-Bench", "raw.githubusercontent.com/Job-Bench"]
LAB_GIT = "https://github.com/harveyai/harvey-labs"
LAB_BLOCK = ["github.com/harveyai", "raw.githubusercontent.com/harveyai", "codeload.github.com/harveyai"]
MINUTES = 45
UA = {"User-Agent": "swarmbench-import/1.0"}


def default_dest():
    return Path(os.environ.get("SWARMBENCH_IMPORT_DIR") or Path.home() / ".swarmbench" / "tasks-external")


def _get(url, *, tries=3):
    last = None
    for n in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                return r.read()
        except OSError as e:  # urllib's errors are OSErrors
            last = e
            time.sleep(1 + 2 * n)
    raise RuntimeError(f"couldn't download {url}: {last}")


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def write_task(root, task_id, *, title, category, summary, prompt, files, rubric, source,
               blocked_hosts, minutes=MINUTES, card=""):
    """One rubric task: task.json, prompt.md, repo/ (the starting files) and rubric/."""
    d = Path(root) / task_id
    if d.exists():
        shutil.rmtree(d)
    (d / "repo").mkdir(parents=True)
    (d / "rubric").mkdir()
    for rel, data in files.items():
        fp = d / "repo" / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_bytes(data)
    (d / "prompt.md").write_text(prompt.strip() + "\n", encoding="utf-8")
    (d / "rubric" / "RUBRICS.json").write_text(json.dumps(rubric, indent=1, ensure_ascii=False),
                                               encoding="utf-8")
    if card:
        (d / "rubric" / "card.md").write_text(card, encoding="utf-8")  # it explains the answers
    meta = {"id": task_id, "title": title[:200], "category": category, "difficulty": "hard",
            "workflow": "none", "summary": summary[:600], "time_limit_minutes": minutes,
            "grade_timeout_seconds": 1800, "agent_tests_dir": None,
            "protected_files": sorted(files), "grading": "rubric", "output_dir": "output",
            "blocked_hosts": blocked_hosts, "source": source}
    (d / "task.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    return d


# --- JobBench -------------------------------------------------------------------------------


def _hf_tree(path, revision):
    url = (f"{HF}/api/datasets/{JB_REPO}/tree/{revision}/"
           f"{urllib.parse.quote(path)}?recursive=true")
    return json.loads(_get(url).decode("utf-8"))


def _hf_file(path, revision):
    return _get(f"{HF}/datasets/{JB_REPO}/resolve/{revision}/{urllib.parse.quote(path)}")


def import_jobbench(dest=None, *, split="main", only=None, limit=None, revision=None,
                    with_search_files=False, log=print):
    """Import JobBench tasks; `only` filters by profession or "profession/taskN"."""
    dest = Path(dest or default_dest())
    dest.mkdir(parents=True, exist_ok=True)
    folder = JB_SPLITS.get(split)
    if folder is None:
        raise ValueError(f"no JobBench split '{split}' (main or easy)")
    revision = revision or json.loads(_get(f"{HF}/api/datasets/{JB_REPO}").decode("utf-8"))["sha"]
    tasks = sorted(e["path"] for e in _hf_tree(folder, revision)
                   if e["type"] == "directory" and e["path"].count("/") == 2)
    if only:
        wanted = [o.strip().lower() for o in only]
        tasks = [t for t in tasks if any(w in t.lower().split("/", 1)[1] for w in wanted)]
    made = []
    for path in tasks[:limit] if limit else tasks:
        _, profession, name = path.split("/")
        files_meta = [e for e in _hf_tree(path, revision) if e["type"] == "file"]
        rel = {e["path"][len(path) + 1:]: e["path"] for e in files_meta}
        if "RUBRICS.json" not in rel or "task_folder/TASK_INSTRUCTIONS.txt" not in rel:
            log(f"skip {path}: no rubric or instructions")
            continue
        repo = {}
        for r, full in rel.items():
            if r.startswith("task_folder/") and r != "task_folder/TASK_INSTRUCTIONS.txt":
                repo[r[len("task_folder/"):]] = _hf_file(full, revision)
            elif with_search_files and r.startswith("files_required_to_search/"):
                repo["reference/" + r[len("files_required_to_search/"):]] = _hf_file(full, revision)
        prompt = _hf_file(rel["task_folder/TASK_INSTRUCTIONS.txt"], revision).decode("utf-8", "replace")
        rubric = json.loads(_hf_file(rel["RUBRICS.json"], revision).decode("utf-8"))
        card = _hf_file(rel["task_card.md"], revision).decode("utf-8", "replace") \
            if "task_card.md" in rel else ""
        title = (card.splitlines()[0].lstrip("# ").strip() if card.strip()
                 else f"{profession.replace('_', ' ')} {name}")
        task_id = f"jb-{_slug(profession)}-{_slug(name)}" + ("" if split == "main" else "-easy")
        write_task(dest, task_id, title=title, category=profession.replace("_", " "),
                   summary=prompt.strip().split("\n")[0], prompt=prompt, files=repo,
                   rubric=rubric, card=card, blocked_hosts=JB_BLOCK,
                   source={"benchmark": "JobBench", "license": "MIT", "split": split,
                           "path": path, "revision": revision,
                           "url": f"{HF}/datasets/{JB_REPO}"})
        made.append(task_id)
        log(f"imported {task_id}")
    return made


# --- LAB ------------------------------------------------------------------------------------


def lab_rubric(criteria):
    """LAB's pass/fail criteria as rubric items of weight 1."""
    items = []
    for c in criteria or []:
        if isinstance(c, dict):
            text = str(c.get("match_criteria") or c.get("title") or "")
            items.append({"rubric": str(c.get("title") or text)[:300], "weight": 1, "criterion": [text]})
    return {"rubrics": items}


def lab_prompt(task):
    parts = [str(task.get("instructions") or "").strip()]
    if deliv := task.get("deliverables"):
        lines = [f"- `{k}`" + (f": {v}" if v and v != k else "") for k, v in dict(deliv).items()]
        parts.append("Deliverables:\n" + "\n".join(lines))
    parts.append("The source documents are in `documents/`. Keep every figure, name and claim "
                 "traceable to them.")
    return "\n\n".join(parts)


def import_lab(dest=None, *, areas, limit=None, log=print):
    """Import LAB tasks of these practice areas (folders under tasks/ in harvey-labs)."""
    if not areas:
        raise ValueError("name practice areas to import (e.g. contracts,tax): LAB is about 2 GB")
    dest = Path(dest or default_dest())
    dest.mkdir(parents=True, exist_ok=True)
    made = []
    with tempfile.TemporaryDirectory(prefix="swb-lab-") as tmp:
        repo = Path(tmp) / "lab"
        subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", LAB_GIT,
                        str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "sparse-checkout", "set",
                        *[f"tasks/{a.strip()}" for a in areas]], check=True, capture_output=True)
        sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                             capture_output=True, text=True).stdout.strip()
        found = sorted((repo / "tasks").rglob("task.json"))
        for tj in found[:limit] if limit else found:
            rel = tj.parent.relative_to(repo / "tasks").as_posix()
            try:
                task = json.loads(tj.read_text(encoding="utf-8"))
            except ValueError:
                log(f"skip {rel}: task.json isn't JSON")
                continue
            if not task.get("criteria"):
                log(f"skip {rel}: no criteria")
                continue
            docs = tj.parent / "documents"
            files = {f"documents/{p.relative_to(docs).as_posix()}": p.read_bytes()
                     for p in sorted(docs.rglob("*")) if p.is_file()} if docs.is_dir() else {}
            task_id = "lab-" + _slug(rel)
            write_task(dest, task_id, title=str(task.get("title") or rel), category=rel.split("/")[0],
                       summary=str(task.get("instructions") or "")[:300], prompt=lab_prompt(task),
                       files=files, rubric=lab_rubric(task["criteria"]), blocked_hosts=LAB_BLOCK,
                       source={"benchmark": "LAB (Harvey)", "license": "MIT", "path": rel,
                               "revision": sha, "url": LAB_GIT})
            made.append(task_id)
            log(f"imported {task_id}")
    return made
