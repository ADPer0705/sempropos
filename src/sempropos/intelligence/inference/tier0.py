"""Structured fallback synthesis provider."""

from __future__ import annotations

from sempropos.intelligence.contracts import SynthesisRequest, SynthesisResult
from sempropos.intelligence.inference.base import ProviderInfo, SynthesisProvider


class Tier0Provider(SynthesisProvider):
    """Always-available provider that yields empty command output."""

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(name="tier0", local=True)

    def available(self) -> bool:
        return True

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        return SynthesisResult(
            provider="tier0",
            command="",
            raw_output="",
            metadata={"fallback_reason": "no_provider_available"},
        )
