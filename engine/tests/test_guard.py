"""Agents are kept out of Pantheon's own data folder."""

from __future__ import annotations

import pytest

from app.core import guard
from app.core.config import settings
from app.tools.base import ToolError


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    ws = tmp_path / "data" / "workspaces" / "acme"
    (ws / "src").mkdir(parents=True)
    return ws


@pytest.mark.parametrize("command", [
    r'type "{data}\pantheon.db"',
    r'python -c "import os; print(os.path.expandvars(r\"%APPDATA%\ai.pantheon.studio\"))"',
    "cat ~/.local/share/ai.pantheon.studio/secrets/pantheon.key",
    r"dir {data}\workspaces\other-org",
    "copy pantheon.db x.db",
])
def test_naming_the_data_folder_is_refused(root, command):
    with pytest.raises(ToolError, match="off limits"):
        guard.check(command.format(data=settings.data_path), [root], cwd=root)


@pytest.mark.parametrize("command,cwd", [
    (r"type ..\..\secrets\x", "."),
    ("cat ../notes.md", "."),
    ("ls ../..", "src"),
    ("cd .. && dir", "."),
])
def test_climbing_out_of_the_workspace_is_refused(root, command, cwd):
    with pytest.raises(ToolError):
        guard.check(command, [root], cwd=root / cwd)


@pytest.mark.parametrize("command,cwd", [
    ("npm test && git status", "."),
    ("cat ../README.md", "src"),
    (r"dir {ws}\src", "."),
    ("python build.py --out dist", "src"),
])
def test_ordinary_work_is_fine(root, command, cwd):
    guard.check(command.format(ws=root), [root], cwd=root / cwd)


def test_pantheons_source_is_refused_too(root):
    # A timed run's team wrote its pages and screenshots into the engine's source folder.
    with pytest.raises(ToolError, match="off limits"):
        guard.check(rf'cd /d "{guard.SOURCE / "engine"}" && python qa.py', [root], cwd=root)
    guard.check("python qa.py", [root], cwd=root)  # the workspace itself is fine
