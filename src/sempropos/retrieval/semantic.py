"""Embedding-based semantic retrieval."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer
else:
    SentenceTransformer = Any

from sempropos import config
from sempropos.index import schema


_MODEL: SentenceTransformer | None = None
_TOOL_IDS: list[int] | None = None
_TOOL_EMBEDDINGS: np.ndarray | None = None


def _get_model() -> SentenceTransformer:
    """Return a cached embedding model for semantic retrieval."""
    global _MODEL
    if _MODEL is None:
        try:
            from sentence_transformers import SentenceTransformer as _SentenceTransformer
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "sentence-transformers is required for semantic retrieval. "
                "Install project dependencies first."
            ) from exc
        _MODEL = _SentenceTransformer(config.EMBEDDING_MODEL_NAME)
    return _MODEL


def _load_embeddings() -> tuple[list[int], np.ndarray]:
    """Load and cache tool ids plus tool embedding matrix from disk."""
    global _TOOL_IDS, _TOOL_EMBEDDINGS

    if _TOOL_IDS is not None and _TOOL_EMBEDDINGS is not None:
        return _TOOL_IDS, _TOOL_EMBEDDINGS

    tool_path = config.tool_embeddings_path()
    if not tool_path.exists():
        _TOOL_IDS = []
        _TOOL_EMBEDDINGS = np.empty((0, config.EMBEDDING_DIM), dtype=np.float32)
        return _TOOL_IDS, _TOOL_EMBEDDINGS

    embeddings = np.load(tool_path)
    with schema.get_connection() as conn:
        rows = conn.execute("SELECT id FROM tools ORDER BY id").fetchall()

    ids = [int(row["id"]) for row in rows]
    if len(ids) != len(embeddings):
        size = min(len(ids), len(embeddings))
        ids = ids[:size]
        embeddings = embeddings[:size]

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

    model = _get_model()
    vector = model.encode(
        [query],
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )[0].astype(np.float32)

    scores = embeddings @ vector
    ranked_indices = np.argsort(-scores)[:top_k]
    return [(tool_ids[int(idx)], float(scores[int(idx)])) for idx in ranked_indices]


def _reset_cache() -> None:
    """Clear semantic retrieval caches for tests or process refresh."""
    global _MODEL, _TOOL_IDS, _TOOL_EMBEDDINGS
    _MODEL = None
    _TOOL_IDS = None
    _TOOL_EMBEDDINGS = None
