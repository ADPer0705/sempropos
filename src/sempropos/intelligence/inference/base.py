"""Base classes for synthesis and embedding providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from sempropos.intelligence.contracts import (
    EmbeddingProviderName,
    EmbeddingRequest,
    SynthesisRequest,
    SynthesisResult,
)


@dataclass(frozen=True)
class ProviderInfo:
    """Metadata describing a provider implementation."""

    name: str
    local: bool
    details: dict[str, Any] = field(default_factory=dict)


class SynthesisProvider(ABC):
    """Abstract synthesis provider contract."""

    @property
    @abstractmethod
    def info(self) -> ProviderInfo:
        """Return provider metadata."""

    @abstractmethod
    def available(self) -> bool:
        """Return whether this provider can be used in the current environment."""

    @abstractmethod
    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        """Run command synthesis for the given prompt."""


class EmbeddingProvider(ABC):
    """Abstract embedding provider contract."""

    @property
    @abstractmethod
    def name(self) -> EmbeddingProviderName:
        """Return embedding provider identity."""

    @abstractmethod
    def available(self) -> bool:
        """Return whether this embedding provider can be used."""

    @abstractmethod
    def embed(self, request: EmbeddingRequest) -> np.ndarray:
        """Return normalized embeddings for the request texts."""
