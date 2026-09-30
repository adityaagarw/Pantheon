"""Test fixtures: a real Postgres test database, the full app, and helpers.

Integration tests run against Postgres (``docker compose up -d postgres``) on
a dedicated ``pantheon_test`` database whose schema is rebuilt per test.
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
os.environ["PANTHEON_DATABASE_URL"] = "postgresql+asyncpg://pantheon:pantheon@localhost:5433/pantheon_test"
os.environ["PANTHEON_AUTO_MIGRATE"] = "false"
os.environ["PANTHEON_DATA_DIR"] = str(_TMP / "data")
os.environ["PANTHEON_FERNET_KEY_PATH"] = str(_TMP / "data" / "secrets" / "test.key")
os.environ["PANTHEON_WORKSPACE_ROOTS"] = str(_TMP / "workspaces")
os.environ["PANTHEON_DISPATCH_POLL_SECONDS"] = "0.2"
os.environ["PANTHEON_EMBEDDINGS"] = "hash"  # deterministic, offline embeddings for tests

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

ADMIN_URL = "postgresql+asyncpg://pantheon:pantheon@localhost:5433/postgres"


async def _ensure_test_db() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        r = await conn.execute(text("SELECT 1 FROM pg_database WHERE datname = 'pantheon_test'"))
        if r.first() is None:
            await conn.execute(text('CREATE DATABASE "pantheon_test"'))
    await engine.dispose()


asyncio.run(_ensure_test_db())

from app import models  # noqa: E402,F401
from app.core.db import Base, engine  # noqa: E402
from app.llm import mock  # noqa: E402


async def reset_schema() -> None:
    # A background task left over from the previous test can briefly hold a lock and
    # get picked as the deadlock victim's peer; the drop is safe to retry.
    for attempt in range(5):
        try:
            async with engine.begin() as conn:
                await conn.execute(text("DROP SCHEMA public CASCADE"))
                await conn.execute(text("CREATE SCHEMA public"))
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await conn.run_sync(Base.metadata.create_all)
            return
        except Exception as exc:  # noqa: BLE001
            if "deadlock" not in str(exc).lower() or attempt == 4:
                raise
            await asyncio.sleep(0.5 * (attempt + 1))


@pytest.fixture
async def db():
    await reset_schema()
    mock.clear_scripts()
    yield
    mock.clear_scripts()


@pytest.fixture
async def app_client(db):
    """The full application (runtime running) + an HTTP client."""
    from app.agents import runtime as runtime_mod
    from app.main import app

    runtime_mod.BACKOFF[:] = [0, 0, 0, 0, 0]
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c


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
