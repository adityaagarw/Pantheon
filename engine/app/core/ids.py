"""Sortable, prefixed identifiers (time-ordered so ids sort by creation)."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime

_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"  # Crockford base32, lowercase


def _b32(n: int, width: int) -> str:
    out = []
    for _ in range(width):
        out.append(_ALPHABET[n & 31])
        n >>= 5
    return "".join(reversed(out))


def new_id(prefix: str) -> str:
    """``<prefix>_<10 chars ms timestamp><10 chars randomness>``."""
    ts = int(time.time() * 1000)
    rnd = int.from_bytes(os.urandom(7), "big")
    return f"{prefix}_{_b32(ts, 10)}{_b32(rnd, 10)}"


def utcnow() -> datetime:
    return datetime.now(UTC)
