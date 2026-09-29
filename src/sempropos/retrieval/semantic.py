"""Embedding-based semantic retrieval."""

from __future__ import annotations

import numpy as np

from sempropos import config
from sempropos.index import get_connection
from sempropos.intelligence import embed_texts

_TOOL_IDS: list[int] | None = None
_TOOL_EMBEDDINGS: np.ndarray | None = None


def _db_tool_ids() -> list[int]:
    with get_connection() as conn:
        rows = conn.execute("SELECT id FROM tools ORDER BY id").fetchall()
    return [int(row["id"]) for row in rows]


def _load_embeddings() -> tuple[list[int], np.ndarray]:
    """Load and cache tool ids plus the tool embedding matrix.

    The matrix is memory-mapped so a query does not read the whole file into
    resident memory; only the rows actually compared are touched.
    """
    global _TOOL_IDS, _TOOL_EMBEDDINGS

    if _TOOL_IDS is not None and _TOOL_EMBEDDINGS is not None:
        return _TOOL_IDS, _TOOL_EMBEDDINGS

    tool_embedding_path = config.tool_embeddings_path()
    if not tool_embedding_path.exists():
        _TOOL_IDS = []
        _TOOL_EMBEDDINGS = np.empty((0, 0), dtype=np.float32)
        return _TOOL_IDS, _TOOL_EMBEDDINGS

    embeddings = np.load(tool_embedding_path, mmap_mode="r")
    if embeddings.ndim != 2 or embeddings.dtype != np.float32:
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE)

    ids_path = config.tool_embedding_ids_path()
    if ids_path.exists():
        ids = [int(value) for value in np.load(ids_path).tolist()]
    else:
        # Legacy installs stored embeddings without a companion id file.
        ids = _db_tool_ids()

    if len(ids) != embeddings.shape[0]:
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE)

    if ids != _db_tool_ids():
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE)

    _TOOL_IDS = ids
    _TOOL_EMBEDDINGS = embeddings
    return _TOOL_IDS, _TOOL_EMBEDDINGS


def query_semantic_matches(query: str, top_k: int = 10) -> list[tuple[int, float]]:
    """Return top-k semantic matches based on true cosine similarity."""
    if top_k <= 0:
        return []

    tool_ids, embeddings = _load_embeddings()
    if not tool_ids or embeddings.size == 0:
        return []

    vector = embed_texts([query], kind="query")[0].astype(np.float32)
    if vector.shape[0] != embeddings.shape[1]:
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE)

    # Compute true cosine similarity via L2 normalization.
    norm_embeddings = np.linalg.norm(embeddings, axis=1)
    norm_embeddings[norm_embeddings == 0] = 1e-10  # Prevent division by zero
    norm_vector = np.linalg.norm(vector) or 1e-10

    scores = (embeddings @ vector) / (norm_embeddings * norm_vector)
    ranked_indices = np.argsort(-scores)[:top_k]

    return [(tool_ids[int(idx)], float(scores[int(idx)])) for idx in ranked_indices]
