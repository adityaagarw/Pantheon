"""Text embeddings for semantic memory search.

``local`` runs BAAI/bge-small-en-v1.5 on the CPU with fastembed (ONNX, no GPU,
no server; the ~130 MB model downloads once into ``data/models``). ``hash`` is
a deterministic bag-of-words embedding used by tests. ``off`` disables vectors;
memory search then falls back to keyword ranking.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
from typing import Any

from app.core.config import settings
from app.models import EMBED_DIM

log = logging.getLogger(__name__)
_model: Any = None
_failed: str | None = None
_lock = asyncio.Lock()


def enabled() -> bool:
    return settings.embeddings in ("local", "hash") and _failed is None


def _hash_embed(text: str) -> list[float]:
    v = [0.0] * EMBED_DIM
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if len(w) < 3:
            continue
        h = int(hashlib.md5(w[:6].encode()).hexdigest(), 16)  # crude stemming: prefix
        v[h % EMBED_DIM] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _load() -> Any:
    from fastembed import TextEmbedding

    return TextEmbedding(settings.embeddings_model,
                         cache_dir=str(settings.data_path / "models"))


async def embed(texts: list[str]) -> list[list[float]] | None:
    """Unit vectors for the texts, or None when embeddings are unavailable."""
    global _model, _failed
    if not texts or not enabled():
        return None
    if settings.embeddings == "hash":
        return [_hash_embed(t) for t in texts]
    async with _lock:
        if _model is None:
            try:
                _model = await asyncio.to_thread(_load)
            except Exception as e:  # noqa: BLE001 - offline, missing model...
                _failed = f"{type(e).__name__}: {e}"
                log.warning("embeddings unavailable, using keyword search: %s", _failed)
                return None
        model = _model
    vectors = await asyncio.to_thread(lambda: [list(map(float, v)) for v in model.embed(texts)])
    return vectors


def status() -> dict[str, Any]:
    return {"mode": settings.embeddings, "model": settings.embeddings_model,
            "loaded": _model is not None, "error": _failed}
