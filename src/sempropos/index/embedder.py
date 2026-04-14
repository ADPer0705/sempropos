"""Embedding model helpers backed by fastembed."""

from __future__ import annotations

from typing import Any

import numpy as np

from sempropos import config
from sempropos.intelligence import facade
from sempropos.intelligence.contracts import EmbeddingProviderName
from sempropos.intelligence.embeddings.fastembed_local import (
    FastEmbedLocalProvider,
)
from sempropos.intelligence.embeddings.fastembed_local import (
    reset_cache as reset_fastembed_cache,
)


def get_embedding_model(
    force_floor: bool = False, model_name: str | None = None
) -> Any:
    """Return a cached fastembed TextEmbedding instance."""
    selected = model_name or (
        config.EMBEDDING_FLOOR_MODEL if force_floor else config.EMBEDDING_PRIMARY_MODEL
    )
    provider = FastEmbedLocalProvider()
    # Keep backward compatibility for callers that still need direct model access.
    return provider._get_model(selected)  # type: ignore[attr-defined]


def embed_texts(
    texts: list[str],
    *,
    force_floor: bool = False,
    model_name: str | None = None,
    provider_name: EmbeddingProviderName | None = None,
) -> np.ndarray:
    """Embed text rows and return a normalized float32 matrix."""
    selected = model_name or (
        config.EMBEDDING_FLOOR_MODEL if force_floor else config.EMBEDDING_PRIMARY_MODEL
    )
    if not texts:
        return np.empty((0, config.get_embedding_dim(selected)), dtype=np.float32)
    return facade.embed_texts(
        texts,
        force_floor=force_floor,
        model_name=selected,
        forced_provider=provider_name,
    )


def reset_cache() -> None:
    """Reset in-process embedding model cache (test helper)."""
    reset_fastembed_cache()
