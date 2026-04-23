"""Embedding-based semantic retrieval."""

from __future__ import annotations

import numpy as np

from sempropos import config
from sempropos.index import schema
from sempropos.intelligence import facade

_TOOL_IDS: list[int] | None = None
_TOOL_EMBEDDINGS: np.ndarray | None = None


def _load_embeddings() -> tuple[list[int], np.ndarray]:
    """Load and cache tool ids plus tool embedding matrix from disk."""
    global _TOOL_IDS, _TOOL_EMBEDDINGS

    if _TOOL_IDS is not None and _TOOL_EMBEDDINGS is not None:
        return _TOOL_IDS, _TOOL_EMBEDDINGS

    tool_path = config.tool_embeddings_path()
    if not tool_path.exists():
        _TOOL_IDS = []
        _TOOL_EMBEDDINGS = np.empty((0, 0), dtype=np.float32)
        return _TOOL_IDS, _TOOL_EMBEDDINGS

    embeddings = np.load(tool_path)
    if embeddings.ndim != 2:
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE)

    with schema.get_connection() as conn:
        rows = conn.execute("SELECT id FROM tools ORDER BY id").fetchall()

    ids = [int(row["id"]) for row in rows]
    if len(ids) != len(embeddings):
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE)

    _TOOL_IDS = ids
    _TOOL_EMBEDDINGS = embeddings.astype(np.float32)
    assert _TOOL_EMBEDDINGS is not None
    return _TOOL_IDS, _TOOL_EMBEDDINGS


def search(query: str, top_k: int = 10) -> list[tuple[int, float]]:
    """Return top-k semantic matches based on cosine similarity."""
    if top_k <= 0:
        return []

    tool_ids, embeddings = _load_embeddings()
    if not tool_ids or embeddings.size == 0:
        return []

    vector = facade.embed_texts([query])[0].astype(np.float32)
    if vector.shape[0] != embeddings.shape[1]:
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE)

    scores = embeddings @ vector
    ranked_indices = np.argsort(-scores)[:top_k]
    return [(tool_ids[int(idx)], float(scores[int(idx)])) for idx in ranked_indices]


def _reset_cache() -> None:
    """Clear semantic retrieval caches for tests or process refresh."""
    global _TOOL_IDS, _TOOL_EMBEDDINGS
    _TOOL_IDS = None
    _TOOL_EMBEDDINGS = None
