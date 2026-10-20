"""In-process ranking for memory and attachment search: vector similarity and keywords.

An org's notes and document chunks number in the thousands at most, so they are ranked
here instead of in the database: cosine similarity over the stored embeddings, and BM25
over the words (whole words, case-folded, with simple English stemming).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Iterable

import numpy as np

STOP = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "has", "have",
    "how", "i", "if", "in", "into", "is", "it", "its", "me", "my", "no", "not", "of", "on",
    "or", "our", "so", "that", "the", "their", "them", "then", "there", "these", "they",
    "this", "to", "was", "we", "what", "when", "where", "which", "who", "why", "will", "with",
    "you", "your"})


def _stem(w: str) -> str:
    for suffix in ("ing", "edly", "ed", "es", "ly", "s"):
        if len(w) > len(suffix) + 2 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def terms(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP]


def nearest[T](items: Iterable[T], vector: list[float],
               embedding: Callable[[T], list[float] | None], k: int) -> list[tuple[T, float]]:
    """The ``k`` items most similar to ``vector``, with their cosine similarity."""
    rows = [(it, e) for it in items if (e := embedding(it)) is not None]
    if not rows:
        return []
    m = np.asarray([e for _, e in rows], dtype=np.float32)
    v = np.asarray(vector, dtype=np.float32)
    norms = np.linalg.norm(m, axis=1) * (np.linalg.norm(v) or 1.0)
    sims = (m @ v) / np.where(norms == 0, 1.0, norms)
    order = np.argsort(-sims)[:k]
    return [(rows[i][0], float(sims[i])) for i in order]


def keyword[T](items: Iterable[T], query: str, text: Callable[[T], str], k: int,
               k1: float = 1.2, b: float = 0.75) -> list[tuple[T, float]]:
    """The ``k`` best keyword matches for ``query`` (BM25); items with no match are left out."""
    q = set(terms(query))
    if not q:
        return []
    docs = [(it, Counter(terms(text(it)))) for it in items]
    if not docs:
        return []
    avg = sum(sum(c.values()) for _, c in docs) / len(docs) or 1.0
    df = Counter(t for _, c in docs for t in q if t in c)
    n = len(docs)
    scored = []
    for it, c in docs:
        length = sum(c.values())
        s = 0.0
        for t in q:
            if f := c.get(t):
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * length / avg))
        if s > 0:
            scored.append((it, s))
    scored.sort(key=lambda r: r[1], reverse=True)
    return scored[:k]
