"""Test fixtures: a real SQLite test database, the full app, and helpers.

Integration tests run against a SQLite file in a temporary folder, emptied before
each test.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

_TMP = Path(tempfile.mkdtemp(prefix="pantheon-test-"))
os.environ["PANTHEON_DATABASE_URL"] = f"sqlite+aiosqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["PANTHEON_AUTO_MIGRATE"] = "false"
os.environ["PANTHEON_DB_STRICT"] = "true"
os.environ["PANTHEON_DATA_DIR"] = str(_TMP / "data")
os.environ["PANTHEON_FERNET_KEY_PATH"] = str(_TMP / "data" / "secrets" / "test.key")
os.environ["PANTHEON_KEY_STORE"] = "file"  # never touch the real keychain in tests
os.environ["PANTHEON_WORKSPACE_ROOTS"] = str(_TMP / "workspaces")
os.environ["PANTHEON_DISPATCH_POLL_SECONDS"] = "0.2"
os.environ["PANTHEON_EMBEDDINGS"] = "hash"  # deterministic, offline embeddings for tests

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import models  # noqa: E402,F401
from app.core.db import Base, engine  # noqa: E402
from app.llm import mock  # noqa: E402

_created = False


async def reset_schema() -> None:
    """The schema is made once; each test starts from empty tables."""
    global _created
    from app.events import bus

    await bus.drain()
    async with engine.begin() as conn:
        if not _created:
            await conn.run_sync(Base.metadata.create_all)
            _created = True
        await conn.execute(text("PRAGMA defer_foreign_keys = ON"))
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


@pytest.fixture
async def db():
    from app.services import secrets

    await reset_schema()
    secrets._values.clear()
    mock.clear_scripts()
    yield
    mock.clear_scripts()


@pytest.fixture
async def app_client(db):
    """The full application (runtime running) + an HTTP client."""
    from app.agents import runtime as runtime_mod
    from app.main import app

    runtime_mod.BACKOFF[:] = [0, 0, 0, 0, 0]
    runtime_mod.FULL_BACKOFF[:] = [0, 0, 0, 0]
    watchdog = asyncio.get_running_loop().call_later(10_000, lambda: None)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
        # A shutdown that hangs shows what every task is waiting on.
        watchdog.cancel()
        watchdog = asyncio.get_running_loop().call_later(30, _dump_tasks)
    watchdog.cancel()


def _dump_tasks() -> None:
    import io

    out = io.StringIO()
    for t in asyncio.all_tasks():
        t.print_stack(limit=12, file=out)
    sys.stderr.write("\n=== shutdown is stuck; tasks:\n" + out.getvalue())


@pytest.fixture
def workspace_root() -> Path:
    p = _TMP / "workspaces"
    p.mkdir(parents=True, exist_ok=True)
    return p


async def wait_for(cond: Callable, timeout: float = 15.0, interval: float = 0.05,
                   msg: str = "condition") -> object:
    """Poll ``cond`` (sync or async) until truthy."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = cond()
        if asyncio.iscoroutine(last):
            last = await last
        if last:
            return last
        await asyncio.sleep(interval)
    raise AssertionError(f"timed out waiting for {msg} (last={last!r})")


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    shutil.rmtree(_TMP, ignore_errors=True)
