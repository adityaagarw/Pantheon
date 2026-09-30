"""LangGraph checkpoint saver on SQLAlchemy/asyncpg.

The official ``langgraph-checkpoint-postgres`` needs async psycopg, which
refuses Windows' Proactor loop (the loop MCP stdio subprocesses need). This
saver stores the same data model (checkpoints, per-channel blobs, pending
writes) through the app's existing asyncpg engine, so it works everywhere.

Every thread keeps only its newest ``keep`` checkpoints: agent threads are
long-lived, and the full history is recorded separately (turns, llm_calls,
tool_calls), so old checkpoints would only grow the database.
"""

from __future__ import annotations

import random
from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from sqlalchemy import and_, delete, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import LgBlob, LgCheckpoint, LgWrite


class SqlCheckpointSaver(BaseCheckpointSaver[str]):
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], keep: int = 20) -> None:
        super().__init__()
        self._sessions = sessionmaker
        self.keep = keep

    # --- helpers -------------------------------------------------------------

    def _cfg(self, thread_id: str, ns: str, checkpoint_id: str) -> RunnableConfig:
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def _load_blobs(
        self, session: AsyncSession, thread_id: str, ns: str, versions: ChannelVersions
    ) -> dict[str, Any]:
        if not versions:
            return {}
        keys = [(ch, str(v)) for ch, v in versions.items()]
        rows = (
            await session.execute(
                select(LgBlob).where(
                    LgBlob.thread_id == thread_id,
                    LgBlob.checkpoint_ns == ns,
                    tuple_(LgBlob.channel, LgBlob.version).in_(keys),
                )
            )
        ).scalars()
        out: dict[str, Any] = {}
        for row in rows:
            if row.type == "empty" or row.blob is None:
                continue
            out[row.channel] = self.serde.loads_typed((row.type, row.blob))
        return out

    async def _tuple(self, session: AsyncSession, row: LgCheckpoint) -> CheckpointTuple:
        checkpoint: Checkpoint = self.serde.loads_typed((row.type, row.checkpoint))
        writes = (
            await session.execute(
                select(LgWrite)
                .where(
                    LgWrite.thread_id == row.thread_id,
                    LgWrite.checkpoint_ns == row.checkpoint_ns,
                    LgWrite.checkpoint_id == row.checkpoint_id,
                )
                .order_by(LgWrite.task_id, LgWrite.idx)
            )
        ).scalars()
        values = await self._load_blobs(
            session, row.thread_id, row.checkpoint_ns, checkpoint["channel_versions"]
        )
        return CheckpointTuple(
            config=self._cfg(row.thread_id, row.checkpoint_ns, row.checkpoint_id),
            checkpoint={**checkpoint, "channel_values": values},
            metadata=self.serde.loads_typed((row.metadata_type, row.metadata_blob)),
            parent_config=(
                self._cfg(row.thread_id, row.checkpoint_ns, row.parent_checkpoint_id)
                if row.parent_checkpoint_id
                else None
            ),
            pending_writes=[
                (w.task_id, w.channel, self.serde.loads_typed((w.type, w.blob))) for w in writes
            ],
        )

    # --- async API -------------------------------------------------------------

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        stmt = select(LgCheckpoint).where(
            LgCheckpoint.thread_id == thread_id, LgCheckpoint.checkpoint_ns == ns
        )
        if checkpoint_id := get_checkpoint_id(config):
            stmt = stmt.where(LgCheckpoint.checkpoint_id == checkpoint_id)
        else:
            stmt = stmt.order_by(LgCheckpoint.checkpoint_id.desc()).limit(1)
        async with self._sessions() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                return None
            return await self._tuple(session, row)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        stmt = select(LgCheckpoint).order_by(LgCheckpoint.checkpoint_id.desc())
        if config is not None:
            stmt = stmt.where(LgCheckpoint.thread_id == config["configurable"]["thread_id"])
            ns = config["configurable"].get("checkpoint_ns")
            if ns is not None:
                stmt = stmt.where(LgCheckpoint.checkpoint_ns == ns)
            if checkpoint_id := get_checkpoint_id(config):
                stmt = stmt.where(LgCheckpoint.checkpoint_id == checkpoint_id)
        if before is not None and (before_id := get_checkpoint_id(before)):
            stmt = stmt.where(LgCheckpoint.checkpoint_id < before_id)
        async with self._sessions() as session:
            rows = list((await session.execute(stmt)).scalars())
            remaining = limit
            for row in rows:
                if filter:
                    meta = self.serde.loads_typed((row.metadata_type, row.metadata_blob))
                    if not all(meta.get(k) == v for k, v in filter.items()):
                        continue
                if remaining is not None:
                    if remaining <= 0:
                        break
                    remaining -= 1
                yield await self._tuple(session, row)

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        parent_id = config["configurable"].get("checkpoint_id")
        c = checkpoint.copy()
        values: dict[str, Any] = c.pop("channel_values")  # type: ignore[misc]
        ctype, cblob = self.serde.dumps_typed(c)
        mtype, mblob = self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))
        async with self._sessions() as session:
            for channel, version in new_versions.items():
                if channel in values:
                    btype, blob = self.serde.dumps_typed(values[channel])
                else:
                    btype, blob = "empty", None
                await session.execute(
                    pg_insert(LgBlob)
                    .values(
                        thread_id=thread_id, checkpoint_ns=ns, channel=channel,
                        version=str(version), type=btype, blob=blob,
                    )
                    .on_conflict_do_nothing()
                )
            await session.execute(
                pg_insert(LgCheckpoint)
                .values(
                    thread_id=thread_id, checkpoint_ns=ns, checkpoint_id=checkpoint["id"],
                    parent_checkpoint_id=parent_id, type=ctype, checkpoint=cblob,
                    metadata_type=mtype, metadata_blob=mblob,
                )
                .on_conflict_do_update(
                    index_elements=["thread_id", "checkpoint_ns", "checkpoint_id"],
                    set_={"type": ctype, "checkpoint": cblob,
                          "metadata_type": mtype, "metadata_blob": mblob},
                )
            )
            await session.commit()
        if random.random() < 0.2:  # amortized pruning
            await self._prune(thread_id, ns)
        return self._cfg(thread_id, ns, checkpoint["id"])

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]
        overwrite = all(w[0] in WRITES_IDX_MAP for w in writes)
        async with self._sessions() as session:
            for idx, (channel, value) in enumerate(writes):
                wtype, blob = self.serde.dumps_typed(value)
                stmt = pg_insert(LgWrite).values(
                    thread_id=thread_id, checkpoint_ns=ns, checkpoint_id=checkpoint_id,
                    task_id=task_id, idx=WRITES_IDX_MAP.get(channel, idx), channel=channel,
                    type=wtype, blob=blob, task_path=task_path,
                )
                if overwrite:
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["thread_id", "checkpoint_ns", "checkpoint_id",
                                        "task_id", "idx"],
                        set_={"channel": channel, "type": wtype, "blob": blob},
                    )
                else:
                    stmt = stmt.on_conflict_do_nothing()
                await session.execute(stmt)
            await session.commit()

    async def adelete_thread(self, thread_id: str) -> None:
        async with self._sessions() as session:
            for model in (LgCheckpoint, LgBlob, LgWrite):
                await session.execute(delete(model).where(model.thread_id == thread_id))
            await session.commit()

    async def _prune(self, thread_id: str, ns: str) -> None:
        async with self._sessions() as session:
            rows = list(
                (
                    await session.execute(
                        select(LgCheckpoint.checkpoint_id, LgCheckpoint.type,
                               LgCheckpoint.checkpoint)
                        .where(LgCheckpoint.thread_id == thread_id,
                               LgCheckpoint.checkpoint_ns == ns)
                        .order_by(LgCheckpoint.checkpoint_id.desc())
                    )
                ).all()
            )
            if len(rows) <= self.keep:
                return
            kept, dropped = rows[: self.keep], rows[self.keep :]
            live: set[tuple[str, str]] = set()
            for _cid, ctype, cblob in kept:
                cp = self.serde.loads_typed((ctype, cblob))
                live.update((ch, str(v)) for ch, v in cp["channel_versions"].items())
            drop_ids = [r[0] for r in dropped]
            await session.execute(
                delete(LgCheckpoint).where(
                    LgCheckpoint.thread_id == thread_id, LgCheckpoint.checkpoint_ns == ns,
                    LgCheckpoint.checkpoint_id.in_(drop_ids),
                )
            )
            await session.execute(
                delete(LgWrite).where(
                    LgWrite.thread_id == thread_id, LgWrite.checkpoint_ns == ns,
                    LgWrite.checkpoint_id.in_(drop_ids),
                )
            )
            blobs = (
                await session.execute(
                    select(LgBlob.channel, LgBlob.version).where(
                        LgBlob.thread_id == thread_id, LgBlob.checkpoint_ns == ns
                    )
                )
            ).all()
            dead = [(ch, v) for ch, v in blobs if (ch, v) not in live]
            if dead:
                await session.execute(
                    delete(LgBlob).where(
                        and_(LgBlob.thread_id == thread_id, LgBlob.checkpoint_ns == ns),
                        tuple_(LgBlob.channel, LgBlob.version).in_(dead),
                    )
                )
            await session.commit()

    def get_next_version(self, current: str | None, channel: None) -> str:  # type: ignore[override]
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(str(current).split(".")[0])
        return f"{current_v + 1:032}.{random.random():016}"

    # Sync API is unused (the runtime is fully async).
    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:  # pragma: no cover
        raise NotImplementedError("use the async API")

    def list(self, *args: Any, **kwargs: Any):  # type: ignore[override]  # pragma: no cover
        raise NotImplementedError("use the async API")

    def put(self, *args: Any, **kwargs: Any) -> RunnableConfig:  # pragma: no cover
        raise NotImplementedError("use the async API")

    def put_writes(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        raise NotImplementedError("use the async API")
