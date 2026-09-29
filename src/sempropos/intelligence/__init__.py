"""Pluggable intelligence layer for synthesis and embedding interactions."""

from __future__ import annotations

from sempropos.intelligence.contracts import (
    IntelligenceError,
    ProviderConfigurationError,
    ProviderExecutionError,
    ProviderUnavailableError,
    StructuredPrompt,
    SynthesisOptions,
    SynthesisResult,
)
from sempropos.intelligence.facade import (
    detect_embedding_provider,
    detect_synthesis_provider,
    embed_texts,
    get_provider_models,
    list_embedding_providers,
    list_synthesis_providers,
    synthesize,
    warm_synthesis_provider,
)
from sempropos.intelligence.prompt import (
    build_prompt,
    context_to_str,
    extract_command,
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
    "warm_synthesis_provider",
    # ----- Contracts -----
    "StructuredPrompt",
    "SynthesisOptions",
    "SynthesisResult",
    # ----- Prompt  -----
    "build_prompt",
    "extract_command",
    # ----- Misc -----
    "get_provider_models",
    "context_to_str",
]
