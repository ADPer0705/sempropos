"""Pluggable intelligence layer for synthesis and embedding interactions."""

from __future__ import annotations

from sempropos.intelligence.facade import (
    list_embedding_providers,
    list_synthesis_providers,
    detect_synthesis_provider,
    detect_embedding_provider,
    embed_texts,
    synthesize,
    get_provider_models,
)

from sempropos.intelligence.contracts import (
    IntelligenceError,
    ProviderExecutionError,
    ProviderUnavailableError,
    ProviderConfigurationError,
    StructuredPrompt,
    SynthesisResult,
)

from sempropos.intelligence.prompt import (
    context_to_str,
    build_prompt,
)

__all__ = [
    # ----- Errors -----
    "IntelligenceError",
    "ProviderExecutionError",
    "ProviderUnavailableError",
    "ProviderConfigurationError",
    # ----- Facade -----
    "list_embedding_providers",
    "list_synthesis_providers",
    "detect_synthesis_provider",
    "detect_embedding_provider",
    "embed_texts",
    "synthesize",
    # ----- Contracts -----
    "StructuredPrompt",
    "SynthesisResult",
    # ----- Prompt  -----
    "build_prompt",
    # ----- Misc -----
    "get_provider_models",
    "context_to_str",
]