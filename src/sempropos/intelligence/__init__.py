"""Pluggable intelligence layer for grounded command synthesis, embeddings, and safety."""

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
    is_embedding_model_warm,
    list_embedding_providers,
    list_synthesis_providers,
    shutdown_providers,
    synthesize,
    warm_synthesis_provider,
)
from sempropos.intelligence.prompt import (
    COMMAND_JSON_SCHEMA,
    ParsedSynthesis,
    build_prompt,
    context_to_str,
    extract_command,
    parse_synthesis_output,
)
from sempropos.intelligence.safety import (
    CommandVerification,
    FlagAnnotation,
    extract_primary_executable,
    verify_command,
)

__all__ = [
    "IntelligenceError",
    "ProviderExecutionError",
    "ProviderUnavailableError",
    "ProviderConfigurationError",
    "list_embedding_providers",
    "list_synthesis_providers",
    "detect_synthesis_provider",
    "detect_embedding_provider",
    "is_embedding_model_warm",
    "embed_texts",
    "synthesize",
    "warm_synthesis_provider",
    "shutdown_providers",
    "get_provider_models",
    "StructuredPrompt",
    "SynthesisOptions",
    "SynthesisResult",
    "COMMAND_JSON_SCHEMA",
    "ParsedSynthesis",
    "build_prompt",
    "extract_command",
    "parse_synthesis_output",
    "context_to_str",
    "CommandVerification",
    "FlagAnnotation",
    "extract_primary_executable",
    "verify_command",
]
