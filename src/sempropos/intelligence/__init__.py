"""Pluggable intelligence layer for synthesis and embedding interactions."""

from sempropos.intelligence.contracts import (
    EmbeddingProviderName,
    EmbeddingRequest,
    IntelligenceError,
    ProviderExecutionError,
    ProviderUnavailableError,
    SynthesisProviderName,
    SynthesisRequest,
    SynthesisResult,
)
from sempropos.intelligence.core import (
    complete,
    embed_texts,
    embedding_provider_status,
    synthesis_provider_status,
)

__all__ = [
    "EmbeddingRequest",
    "EmbeddingProviderName",
    "IntelligenceError",
    "ProviderExecutionError",
    "SynthesisProviderName",
    "ProviderUnavailableError",
    "SynthesisRequest",
    "SynthesisResult",
    "complete",
    "embed_texts",
    "embedding_provider_status",
    "synthesis_provider_status",
]
