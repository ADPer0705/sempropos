"""FastEmbed provider implementation for text embedding."""

from __future__ import annotations
import os
import numpy as np

# Silence HuggingFace Hub telemetry and symlink warnings before import
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["HF_HUB_DISABLE_EXPERIMENTAL_WARNING"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

from sempropos.intelligence.contracts import (
    ProviderUnavailableError,
    ProviderConfigurationError,
    ProviderExecutionError,
)

try:
    from fastembed import TextEmbedding
    _HAS_FASTEMBED = True
except ImportError:
    _HAS_FASTEMBED = False

# --- THE FIX: Cache the ONNX runtime in memory ---
_CACHED_MODEL_NAME: str | None = None
_CACHED_ENGINE = None

def is_available() -> bool:
    return _HAS_FASTEMBED

def get_available_models() -> list[str]:
    return [
        "nomic-ai/nomic-embed-text-v1.5-Q",
        "BAAI/bge-small-en-v1.5",
        "sentence-transformers/all-MiniLM-L6-v2"
    ]

def embed_texts(texts: list[str], model: str | None = None) -> np.ndarray:
    global _CACHED_MODEL_NAME, _CACHED_ENGINE

    if not is_available():
        raise ProviderUnavailableError("FastEmbed library is not installed.")
    
    if not model:
        raise ProviderConfigurationError("No model configured for FastEmbed embedding.")

    try:
        # Singleton pattern: Only load the heavy model into RAM ONCE per process
        if _CACHED_MODEL_NAME != model or _CACHED_ENGINE is None:
            _CACHED_MODEL_NAME = model
            _CACHED_ENGINE = TextEmbedding(model_name=model)
            
        embeddings_list = list(_CACHED_ENGINE.embed(texts))
        return np.vstack(embeddings_list).astype(np.float32)
    except Exception as e:
        raise ProviderExecutionError(f"FastEmbed execution failed: {e}") from e