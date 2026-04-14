"""Compatibility shim for explicit intelligence configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sempropos.intelligence.contracts import (
    EmbeddingProviderName,
    SynthesisProviderName,
)

PolicyMode = Literal["explicit"]


@dataclass(frozen=True)
class IntelligencePolicy:
    """Explicit provider configuration for synthesis and embeddings."""

    synthesis_provider: SynthesisProviderName
    embedding_provider: EmbeddingProviderName
    mode: PolicyMode = "explicit"

    def is_allowed(self, provider: str) -> bool:
        """Return whether provider is one of the configured providers."""
        return provider in {self.synthesis_provider, self.embedding_provider}
