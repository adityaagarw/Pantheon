"""Async SQLAlchemy engine + session management."""

from __future__ import annotations

import asyncio
import logging
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import MetaData, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

log = logging.getLogger(__name__)

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    # Fetch server-generated values (timestamps, sequences) via RETURNING so
    # objects stay fully loaded after commit and can be serialized anywhere.
    __mapper_args__ = {"eager_defaults": True}


def make_engine(url: str | None = None) -> AsyncEngine:
    url = url or settings.database_url
    if not url.startswith("sqlite"):
        return create_async_engine(url, pool_pre_ping=True, pool_size=20, max_overflow=20)
    eng = create_async_engine(url, connect_args={"timeout": 60})

    @event.listens_for(eng.sync_engine, "connect")
    def _pragmas(dbapi_conn, _record) -> None:
        # pysqlite's own transaction handling is off, so BEGIN is ours (below).
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        for pragma in ("journal_mode=WAL", "synchronous=NORMAL", "foreign_keys=ON",
                       "busy_timeout=60000"):
            cur.execute(f"PRAGMA {pragma}")
        cur.close()

    @event.listens_for(eng.sync_engine, "begin")
    def _begin(conn) -> None:
        # Every transaction takes the write lock up front. SQLite allows one writer, and
        # a read that later turns into a write can't take the lock while another
        # connection holds it; it fails at once instead of waiting. Taking it up front
        # makes transactions queue (busy_timeout), and also stands in for the row locks
        # (FOR UPDATE) the code relies on.
        task = _task()
        dbapi = conn.connection.dbapi_connection
        held = _holders.get(task) if task is not None else None
        if held is not None and held is not dbapi:
            # This task already holds the write lock in another transaction and would
            # wait for itself until the timeout. Finish (commit) the outer one first, or
            # pass its session down.
            where = _where(task)
            if settings.db_strict:
                raise RuntimeError(f"nested database transaction in one task:\n{where}")
            log.error("nested database transaction in one task:\n%s", where)
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        if task is not None:
            _holders[task] = dbapi

    def _release(dbapi) -> None:
        for task, held in list(_holders.items()):
            if held is dbapi:
                del _holders[task]

    event.listen(eng.sync_engine, "commit", lambda c: _release(c.connection.dbapi_connection))
    event.listen(eng.sync_engine, "rollback", lambda c: _release(c.connection.dbapi_connection))
    # A session closed without commit ends its transaction when the pool resets it.
    event.listen(eng.sync_engine.pool, "checkin", lambda dbapi, _rec: _release(dbapi))
    return eng


def holds_transaction() -> bool:
    """Is the current task inside a database transaction (holding the write lock)?"""
    task = _task()
    return task is not None and task in _holders


def _where(task: asyncio.Task) -> str:
    """The app's code that asked for the transaction. SQLAlchemy runs the SQL in a
    greenlet, so the caller's frames are on the greenlet's parent."""
    import greenlet

    g = greenlet.getcurrent()
    f = g.parent.gr_frame if g.parent is not None else None
    lines = []
    while f is not None and len(lines) < 12:
        name = f.f_code.co_filename
        if "site-packages" not in name and "asyncio" not in name:
            lines.append(f"  {name.rsplit('engine', 1)[-1]}:{f.f_lineno} {f.f_code.co_name}\n")
        f = f.f_back
    return "".join(lines) or "  (unknown)\n"


# Which task holds which connection's transaction (to report a task waiting on itself).
_holders: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _task() -> asyncio.Task | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


engine = make_engine()
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


@asynccontextmanager
async def session_scope(session: AsyncSession | None = None) -> AsyncIterator[AsyncSession]:
    """The caller's session if it has one, or a new one. A helper that reads the database
    takes ``session=`` and uses this, so a caller inside a transaction doesn't open a
    second one (which SQLite would make wait for the first)."""
    if session is not None:
        yield session
        return
    async with SessionLocal() as own:
        yield own
