"""Core contracts for sempropos intelligence providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# Normalized provider names for discovery and selection.
SynthesisProviderName = Literal[
    "ollama",
    "tier0",
]

EmbeddingProviderName = Literal[
    "fastembed_local",
    "ollama",
]


# Contracts for normalized provider interactions.
@dataclass(frozen=True)
class SynthesisRequest:
    """Input payload for a command synthesis call."""

    prompt: str
    max_tokens: int = 80
    temperature: float = 0.1
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class SynthesisResult:
    """Normalized synthesis output from any provider."""

    provider: SynthesisProviderName
    command: str
    raw_output: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbeddingRequest:
    """Input payload for embedding calls."""

    texts: list[str]
    force_floor: bool = False
    model_name: str | None = None


# Provider exception types for normalized error handling across different implementations.
class IntelligenceError(RuntimeError):
    """Base exception for intelligence-layer failures."""


class ProviderUnavailableError(IntelligenceError):
    """Raised when a provider is not available under current runtime state."""


class ProviderExecutionError(IntelligenceError):
    """Raised when a provider was selected but failed to execute."""
