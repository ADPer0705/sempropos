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
    """Raised when a provider is misconfigured (e.g. missing API key or model)."""


# ----- Request/Response Contracts -----
@dataclass(frozen=True)
class StructuredPrompt:
    """Provider-agnostic container for grounded command synthesis."""

    system: str
    context: list[dict[str, Any]]
    query: str
    json_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class SynthesisResult:
    """Normalized synthesis output from any provider."""

    provider: SynthesisProviders
    command: str = ""
    raw_output: str = ""
    tool: str | None = None
    explanation: str = ""
    alternatives: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SynthesisOptions:
    """Provider-agnostic generation tuning passed to synthesis backends."""

    think: bool = False
    temperature: float = 0.0
    max_tokens: int = 256
    keep_alive: str | int | None = "30m"
    stop: list[str] | None = None
    base_url: str | None = None
    api_key: str | None = None
    timeout_seconds: float = 30.0
    n_ctx: int = 2048
    n_gpu_layers: int = -1
    threads: int | None = None
    structured_output: bool = True


# ----- Provider Interface Protocols -----
class SynthesisProviderBackend(Protocol):
    def is_available(self) -> bool: ...

    def get_available_models(self) -> list[str]: ...

    def synthesize(
        self,
        prompt: StructuredPrompt,
        model: str | None = None,
        options: SynthesisOptions | None = None,
    ) -> SynthesisResult: ...


class EmbeddingProviderBackend(Protocol):
    def is_available(self) -> bool: ...

    def get_available_models(self) -> list[str]: ...

    def embed_texts(
        self, texts: list[str], model: str | None = None, kind: str = "document"
    ) -> np.ndarray[Any, Any]: ...
