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
from sempropos.intelligence.facade import (
    detect_backend,
    embed_texts,
    list_embedding_providers,
    list_synthesis_providers,
    synthesize,
)
from sempropos.intelligence.policy import IntelligencePolicy, PolicyMode

__all__ = [
    "EmbeddingRequest",
    "EmbeddingProviderName",
    "IntelligenceError",
    "IntelligencePolicy",
    "PolicyMode",
    "ProviderExecutionError",
    "SynthesisProviderName",
    "ProviderUnavailableError",
    "SynthesisRequest",
    "SynthesisResult",
    "detect_backend",
    "embed_texts",
    "list_embedding_providers",
    "list_synthesis_providers",
    "synthesize",
]
