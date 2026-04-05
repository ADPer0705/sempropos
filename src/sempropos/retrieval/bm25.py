"""BM25 retrieval over tool descriptions."""

from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

from sempropos.index import schema


_BM25: BM25Okapi | None = None
_TOOL_IDS: list[int] = []


def _tokenize(text: str) -> list[str]:
    """Lowercase and tokenize free text into BM25 terms."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _build_index() -> None:
    """Load tool descriptions from SQLite and build in-memory BM25 state."""
    global _BM25, _TOOL_IDS

    with schema.get_connection() as conn:
        rows = conn.execute("SELECT id, description FROM tools ORDER BY id").fetchall()

    _TOOL_IDS = [int(row["id"]) for row in rows]
    corpus = [_tokenize(row["description"]) for row in rows]

    if not corpus:
        _BM25 = None
        return

    _BM25 = BM25Okapi(corpus)


def _ensure_index() -> None:
    """Initialize BM25 state lazily on first retrieval call."""
    if _BM25 is None and not _TOOL_IDS:
        _build_index()


def search(query_tokens: list[str], top_k: int = 10) -> list[tuple[int, float]]:
    """Return top-k BM25 matches for the query token set."""
    if top_k <= 0:
        return []

    _ensure_index()
    if _BM25 is None or not _TOOL_IDS:
        return []

    scores = _BM25.get_scores(query_tokens)
    ranked = sorted(
        zip(_TOOL_IDS, scores),
        key=lambda item: item[1],
        reverse=True,
    )
    return [(tool_id, float(score)) for tool_id, score in ranked[:top_k]]


def _reset_cache() -> None:
    """Reset cached BM25 state for tests or reinitialization."""
    global _BM25, _TOOL_IDS
    _BM25 = None
    _TOOL_IDS = []
