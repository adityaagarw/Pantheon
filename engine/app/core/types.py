"""Column types for SQLite that behave like the Postgres ones the code was written for."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
from sqlalchemy import BigInteger, DateTime, Integer, LargeBinary, text
from sqlalchemy.types import TypeDecorator

# A column default for rows inserted in SQL, to the millisecond (CURRENT_TIMESTAMP stops at
# the second). Rows made through the ORM get Python's time, to the microsecond, so
# rows ordered by time keep the order they were made in.
NOW = text("(strftime('%Y-%m-%d %H:%M:%f', 'now'))")

# An auto-numbered key: SQLite only numbers an INTEGER PRIMARY KEY.
BigId = BigInteger().with_variant(Integer, "sqlite")


class UTCDateTime(TypeDecorator):
    """Stored as naive UTC text ("YYYY-MM-DD HH:MM:SS.ffffff"), the same form as SQLite's
    CURRENT_TIMESTAMP, so SQL comparisons with it work; read back timezone-aware."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value


class Embedding(TypeDecorator):
    """A vector as float32 bytes; similarity is computed in Python (see cosine_rank)."""

    impl = LargeBinary
    cache_ok = True

    def __init__(self, dim: int = 384) -> None:
        super().__init__()
        self.dim = dim

    def process_bind_param(self, value, dialect) -> bytes | None:
        if value is None:
            return None
        return np.asarray(value, dtype=np.float32).tobytes()

    def process_result_value(self, value: bytes | None, dialect) -> list[float] | None:
        if value is None:
            return None
        return np.frombuffer(value, dtype=np.float32).tolist()


def cosine_distance(a: list[float] | None, b: list[float]) -> float:
    """1 - cosine similarity (pgvector's ``<=>``); 2.0 when ``a`` is missing."""
    if a is None:
        return 2.0
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    n = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return 1.0 - float(va @ vb) / n if n else 2.0
