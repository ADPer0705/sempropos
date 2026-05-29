"""Core contracts for sempropos intelligence providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from sempropos.config import SynthesisProviders


# ----- Exceptions -----
class IntelligenceError(RuntimeError):
    """Base exception for intelligence-layer failures."""

class ProviderUnavailableError(IntelligenceError):
    """Raised when a provider is not available under current runtime state."""

class ProviderExecutionError(IntelligenceError):
    """Raised when a provider was selected but failed to execute."""

class ProviderConfigurationError(IntelligenceError):
    """Raised when a provider is misconfigured (e.g. missing API key)."""


# ----- Request/Response Contracts -----
@dataclass(frozen=True)
class StructuredPrompt:
    """A provider-agnostic container for synthesis context."""
    system: str
    context: list[dict]  # The truncated list of man page candidates
    query: str           # The user's actual natural language task


@dataclass(frozen=True)
class SynthesisResult:
    """Normalized synthesis output from any provider."""
    provider: SynthesisProviders
    command: str = ""
    raw_output: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ----- Provider Interface Protocols -----
class SynthesisProviderBackend(Protocol):
    def is_available(self) -> bool:
        """Check if the library/daemon/service is reachable."""
        ...

    def get_available_models(self) -> list[str]:
        """Return a list of available models for this provider."""
        ...

    def synthesize(
        self, prompt: StructuredPrompt, model: str | None = None
    ) -> SynthesisResult:
        """Execute the synthesis and return a normalized result."""
        ...


class EmbeddingProviderBackend(Protocol):
    def is_available(self) -> bool:
        """Check if the library/daemon/service is reachable."""
        ...
        
    def get_available_models(self) -> list[str]:
        """Return a list of available models for this provider."""
        ...

    def embed_texts(self, texts: list[str], model: str | None = None) -> np.ndarray:
        """Execute the embedding and return a 2D float32 matrix."""
        ...