"""FastEmbed local ONNX embedding provider."""

from __future__ import annotations

import os
from typing import Any

import numpy as np

# Silence HuggingFace Hub telemetry and symlink warnings before import
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_EXPERIMENTAL_WARNING", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from sempropos import config
from sempropos.intelligence.contracts import (
    ProviderConfigurationError,
    ProviderExecutionError,
    ProviderUnavailableError,
)

try:
    from fastembed import TextEmbedding

    _HAS_FASTEMBED = True
except ImportError:
    TextEmbedding = None  # type: ignore[assignment,misc]
    _HAS_FASTEMBED = False

_CACHED_MODEL_NAME: str | None = None
_CACHED_ENGINE: Any = None
_ONNX_THREADS = min(os.cpu_count() or 2, 4)


def is_available() -> bool:
    return _HAS_FASTEMBED


def is_model_loaded() -> bool:
    """Return True when an ONNX embedding model is already resident in memory."""
    return _CACHED_ENGINE is not None


def unload_model() -> None:
    """Release the cached ONNX model to free memory."""
    global _CACHED_MODEL_NAME, _CACHED_ENGINE
    _CACHED_ENGINE = None
    _CACHED_MODEL_NAME = None


def get_available_models() -> list[str]:
    return [
        "nomic-ai/nomic-embed-text-v1.5-Q",
        "BAAI/bge-small-en-v1.5",
        "sentence-transformers/all-MiniLM-L6-v2",
    ]


def embed_texts(
    texts: list[str], model: str | None = None, kind: str = "document"
) -> np.ndarray[Any, Any]:
    """Embed texts using task-aware ``query_embed`` / ``passage_embed`` when supported."""
    global _CACHED_MODEL_NAME, _CACHED_ENGINE

    if not is_available() or TextEmbedding is None:
        raise ProviderUnavailableError("FastEmbed library is not installed.")
    if not model:
        raise ProviderConfigurationError("No model configured for FastEmbed embedding.")
    if not texts:
        return np.empty((0, 0), dtype=np.float32)

    try:
        if model != _CACHED_MODEL_NAME or _CACHED_ENGINE is None:
            _CACHED_MODEL_NAME = model
            _CACHED_ENGINE = TextEmbedding(
                model_name=model,
                cache_dir=str(config.embeddings_cache_dir()),
                threads=_ONNX_THREADS,
            )

        engine = _CACHED_ENGINE
        if kind == "query" and hasattr(engine, "query_embed"):
            gen = iter(engine.query_embed(texts))
        elif kind == "document" and hasattr(engine, "passage_embed"):
            gen = iter(engine.passage_embed(texts))
        else:
            gen = iter(engine.embed(texts))

        first = next(gen)
        dim = int(first.shape[0])
        out = np.empty((len(texts), dim), dtype=np.float32)
        out[0] = first
        for idx, vec in enumerate(gen, start=1):
            out[idx] = vec
        return out
    except StopIteration as exc:
        raise ProviderExecutionError("FastEmbed returned no embeddings.") from exc
    except Exception as exc:  # noqa: BLE001
        raise ProviderExecutionError(f"FastEmbed execution failed: {exc}") from exc
