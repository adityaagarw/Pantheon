"""Pantheon Host's Kronos operations: what they may touch, and the code-change lifecycle
(on a scratch repo)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the repo root, for host/

from host import kronos_ops, paths  # noqa: E402


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def runner(tmp_path, monkeypatch):
    mod = kronos_ops
    repo = tmp_path / "repo"
    (repo / "data" / "secrets").mkdir(parents=True)
    (repo / "data" / "secrets" / "pantheon.key").write_text("k")
    (repo / ".env").write_text("OPENAI_API_KEY=x")
    (repo / "README.md").write_text("hello\n")
    (repo / ".gitignore").write_text("/data/\n.env\n.kronos/\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    allowed = tmp_path / "reports"
    allowed.mkdir()
    config = tmp_path / "home" / "host.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"allowed": [str(allowed)]}))
    monkeypatch.setattr(mod, "REPO", repo.resolve())
    monkeypatch.setattr(mod, "WORK", repo.resolve() / ".kronos" / "work")
    monkeypatch.setattr(mod, "CHANGES", repo.resolve() / ".kronos" / "changes.json")
    # Never the user's real settings.
    monkeypatch.setattr(paths, "CONFIG", config)
    monkeypatch.setattr(paths, "OLD_CONFIG", tmp_path / "home" / "kronos.json")
    monkeypatch.setattr(mod, "test_paths", {"repo": repo.resolve(), "allowed": allowed.resolve(),
                                            "outside": tmp_path.resolve()}, raising=False)
    return mod


def test_reach_is_the_repo_and_allowed_folders(runner):
    p = runner.test_paths
    assert "hello" in runner.op_read({"path": "README.md"})
    for secret in (".env", "data/secrets/pantheon.key"):
        with pytest.raises(runner.Refused, match="secrets"):
            runner.op_read({"path": secret})
    with pytest.raises(runner.Refused, match="outside"):
        runner.op_read({"path": str(p["outside"] / "elsewhere.txt")})
    with pytest.raises(runner.Refused, match="read-only"):
        runner.op_write({"path": "README.md", "content": "hacked"})

    target = p["allowed"] / "q3" / "summary.md"
    runner.op_write({"path": str(target), "content": "draft"})
    runner.op_edit({"path": str(target), "old": "draft", "new": "final"})
    assert target.read_text() == "final"
    assert "summary.md" in runner.op_search({"pattern": "final", "path": str(p["allowed"])})


def test_a_change_is_checked_merged_and_cleaned_up(runner):
    repo = runner.test_paths["repo"]
    started = runner.op_change_start({"title": "Add notes"})
    cid = started.split()[1]
    wt = repo / ".kronos" / "work" / cid
    runner.op_write({"path": str(wt / "notes" / "hello.txt"), "content": "hi\n"})
    assert "notes/hello.txt" in runner.op_change_diff({"change": cid})
    assert "All checks passed" in runner.op_change_check({"change": cid})

    out = runner.op_change_apply({"change": cid, "message": "Add notes"})
    assert out.startswith("Applied"), out
    assert (repo / "notes" / "hello.txt").read_text() == "hi\n"
    assert not wt.exists()
    assert f"kronos/{cid}" not in _git(repo, "branch")
    assert "Kronos: Add notes" in _git(repo, "log", "-1", "--format=%s")
    with pytest.raises(runner.Refused, match="no open change"):
        runner.op_change_apply({"change": cid, "message": "again"})


def test_apply_stops_at_the_users_uncommitted_work(runner):
    repo = runner.test_paths["repo"]
    cid = runner.op_change_start({"title": "Edit readme"}).split()[1]
    runner.op_edit({"path": str(repo / ".kronos" / "work" / cid / "README.md"),
                    "old": "hello", "new": "hello from Kronos"})
    (repo / "README.md").write_text("the user's own edit\n")

    out = runner.op_change_apply({"change": cid, "message": "Readme"})
    assert "not merged" in out and "README.md" in out
    assert (repo / "README.md").read_text() == "the user's own edit\n"

    runner.op_change_discard({"change": cid})
    assert f"kronos/{cid}" not in _git(repo, "branch")
