"""Compatibility wrappers for synthesis provider selection and execution."""

from __future__ import annotations

from typing import get_args

from sempropos.intelligence import facade
from sempropos.intelligence.contracts import SynthesisProviderName

_KNOWN_BACKENDS = set(get_args(SynthesisProviderName))


def run(prompt: str, backend: str) -> str:
    """Run synthesis using the selected backend name as a forced provider."""
    if backend not in _KNOWN_BACKENDS:
        raise ValueError(f"Unknown backend: {backend}")

    forced: SynthesisProviderName = backend  # type: ignore[assignment]

    result = facade.synthesize(prompt, forced_provider=forced)
    return result.command


def detect() -> str:
    """Detect the selected synthesis backend under active intelligence policy."""
    return facade.detect_backend()
