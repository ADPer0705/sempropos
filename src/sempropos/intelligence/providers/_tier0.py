"""Tier0 fallback synthesis provider."""

from __future__ import annotations

from sempropos.intelligence.contracts import SynthesisResult, StructuredPrompt

def is_available() -> bool:
    """Tier0 is always available as the ultimate fallback."""
    return True

def get_available_models() -> list[str]:
    """Tier0 has no models, but we return a placeholder to satisfy the interface."""
    return ["tier0-placeholder-model"]

def synthesize(prompt: StructuredPrompt, model: str | None = None) -> SynthesisResult:
    """
    Tier0 is the fallback for no model available.
    The tier0 output is handled by the cli. This return is just a placeholder to satisfy the interface and should never be used.
    """
    return SynthesisResult(provider="tier0")