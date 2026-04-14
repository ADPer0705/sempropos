"""Embedding-based semantic retrieval."""

from __future__ import annotations

import numpy as np

from sempropos import __version__, config
from sempropos.index import embedder, schema
from sempropos.intelligence.config import load_settings
from sempropos.intelligence.contracts import EmbeddingProviderName

_TOOL_IDS: list[int] | None = None
_TOOL_EMBEDDINGS: np.ndarray | None = None


def _get_embedding_meta() -> tuple[str, int, EmbeddingProviderName]:
    """Read embedding metadata and validate compatibility."""
    meta = config.read_index_meta()
    if meta is None:
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE)

    model_name = str(meta.get("embedding_model") or "")
    embedding_dim = meta.get("embedding_dim")
    embedding_provider = str(meta.get("embedding_provider") or "")
    sempropos_version = str(meta.get("sempropos_version") or "")

    if not model_name or embedding_dim is None or not embedding_provider:
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE)

    if embedding_provider not in {
        "fastembed_local",
        "openai_compatible",
        "huggingface",
    }:
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE)

    try:
        dim_value = int(embedding_dim)
    except (TypeError, ValueError):
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE) from None
    if dim_value <= 0:
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE)

    if sempropos_version and sempropos_version != __version__:
        raise RuntimeError(
            config.VERSION_UPDATE_NOTICE_TEMPLATE.format(version=__version__)
        )

    settings = load_settings()
    if settings.embedding_provider != embedding_provider:
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE)

    return model_name, dim_value, embedding_provider


def _load_embeddings() -> tuple[list[int], np.ndarray]:
    """Load and cache tool ids plus tool embedding matrix from disk."""
    global _TOOL_IDS, _TOOL_EMBEDDINGS

    if _TOOL_IDS is not None and _TOOL_EMBEDDINGS is not None:
        return _TOOL_IDS, _TOOL_EMBEDDINGS

    _model_name, expected_dim, _provider_name = _get_embedding_meta()
    tool_path = config.tool_embeddings_path()
    if not tool_path.exists():
        _TOOL_IDS = []
        _TOOL_EMBEDDINGS = np.empty((0, expected_dim), dtype=np.float32)
        return _TOOL_IDS, _TOOL_EMBEDDINGS

    embeddings = np.load(tool_path)
    if embeddings.ndim != 2 or embeddings.shape[1] != expected_dim:
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

    model_name, expected_dim, provider_name = _get_embedding_meta()
    vector = embedder.embed_texts(
        [query],
        model_name=model_name,
        provider_name=provider_name,
    )[0].astype(np.float32)
    if vector.shape[0] != expected_dim:
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE)

    scores = embeddings @ vector
    ranked_indices = np.argsort(-scores)[:top_k]
    return [(tool_ids[int(idx)], float(scores[int(idx)])) for idx in ranked_indices]


def _reset_cache() -> None:
    """Clear semantic retrieval caches for tests or process refresh."""
    global _TOOL_IDS, _TOOL_EMBEDDINGS
    _TOOL_IDS = None
    _TOOL_EMBEDDINGS = None
