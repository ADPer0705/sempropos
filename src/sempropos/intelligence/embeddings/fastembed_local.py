"""Local fastembed implementation for embedding generation."""

from __future__ import annotations

import importlib.util
from typing import Any, Iterable

import numpy as np

from sempropos import config
from sempropos.intelligence.contracts import (
    EmbeddingProviderName,
    EmbeddingRequest,
    ProviderExecutionError,
)
from sempropos.intelligence.providers.base import EmbeddingProvider

_MODEL_CACHE: dict[str, Any] = {}


class FastEmbedLocalProvider(EmbeddingProvider):
    """Embedding provider backed by fastembed TextEmbedding."""

    @property
    def name(self) -> EmbeddingProviderName:
        return "fastembed_local"

    def available(self) -> bool:
        return importlib.util.find_spec("fastembed") is not None

    def _get_model(self, model_name: str) -> Any:
        if model_name in _MODEL_CACHE:
            return _MODEL_CACHE[model_name]

        try:
            from fastembed import TextEmbedding  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:
            raise ProviderExecutionError(
                "fastembed is required for embeddings. Install project dependencies and retry."
            ) from exc

        model = TextEmbedding(model_name=model_name)
        _MODEL_CACHE[model_name] = model
        return model

    def embed(self, request: EmbeddingRequest) -> np.ndarray:
        selected = request.model_name or (
            config.EMBEDDING_FLOOR_MODEL
            if request.force_floor
            else config.EMBEDDING_PRIMARY_MODEL
        )

        if not request.texts:
            return np.empty((0, config.get_embedding_dim(selected)), dtype=np.float32)

        model = self._get_model(selected)
        vectors_iter: Iterable[np.ndarray] = model.embed(request.texts)

        rows: list[np.ndarray] = []
        for vector in vectors_iter:
            arr = np.asarray(vector, dtype=np.float32)
            rows.append(arr)

        if not rows:
            return np.empty((0, config.get_embedding_dim(selected)), dtype=np.float32)

        matrix = np.vstack(rows)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (matrix / norms).astype(np.float32)


def reset_cache() -> None:
    """Reset in-process embedding cache for tests."""
    _MODEL_CACHE.clear()
