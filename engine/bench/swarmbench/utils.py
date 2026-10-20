"""Small file helpers used by the runner and the grader."""

import difflib
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

IGNORED_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".git", ".venv", "node_modules"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ignore(directory, names):
    return [n for n in names if n in IGNORED_DIRS or Path(n).suffix in IGNORED_SUFFIXES]


def copy_tree(src, dst):
    """Copy a folder. The target folder can exist."""
    shutil.copytree(src, dst, ignore=_ignore, dirs_exist_ok=True)


def list_files(root):
    """Return {relative posix path: absolute Path} for all normal files."""
    root = Path(root)
    found = {}
    if not root.exists():
        return found
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(part in IGNORED_DIRS for part in rel.parts):
            continue
        if path.is_file() and path.suffix not in IGNORED_SUFFIXES:
            found[rel.as_posix()] = path
    return found


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_lines(path):
    try:
        return Path(path).read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):
        return None


def diff_stats(before_root, after_root):
    """Count added, removed and changed files, and changed lines."""
    before = list_files(before_root)
    after = list_files(after_root)
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = []
    lines_added = 0
    lines_removed = 0
    for rel in sorted(set(before) & set(after)):
        if file_hash(before[rel]) == file_hash(after[rel]):
            continue
        changed.append(rel)
        old, new = _read_lines(before[rel]), _read_lines(after[rel])
        if old is None or new is None:
            continue
        for line in difflib.unified_diff(old, new, lineterm="", n=0):
            if line.startswith("+") and not line.startswith("+++"):
                lines_added += 1
            elif line.startswith("-") and not line.startswith("---"):
                lines_removed += 1
    for rel in added:
        lines_added += len(_read_lines(after[rel]) or [])
    for rel in removed:
        lines_removed += len(_read_lines(before[rel]) or [])
    return {
        "files_added": added,
        "files_removed": removed,
        "files_changed": changed,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
    }


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return default


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")
