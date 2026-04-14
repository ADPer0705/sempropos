"""High-level intelligence facade for synthesis and embeddings."""

from __future__ import annotations

from sempropos.intelligence import registry
from sempropos.intelligence.config import load_settings
from sempropos.intelligence.contracts import (
    EmbeddingProviderName,
    EmbeddingRequest,
    SynthesisProviderName,
    SynthesisRequest,
    SynthesisResult,
)


def detect_backend(
    *, forced_provider: SynthesisProviderName | None = None
) -> SynthesisProviderName:
    """Return the configured synthesis provider."""
    settings = load_settings()
    provider = registry.resolve_synthesis_provider(
        settings, forced_provider=forced_provider
    )
    return provider.info.name


def synthesize(
    prompt: str,
    *,
    forced_provider: SynthesisProviderName | None = None,
    max_tokens: int = 80,
    temperature: float = 0.1,
    timeout_seconds: float = 30.0,
) -> SynthesisResult:
    """Synthesize a command using configured or forced provider."""
    settings = load_settings()
    provider = registry.resolve_synthesis_provider(
        settings, forced_provider=forced_provider
    )
    request = SynthesisRequest(
        prompt=prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_seconds=timeout_seconds,
    )
    return provider.synthesize(request)


def embed_texts(
    texts: list[str],
    *,
    force_floor: bool = False,
    model_name: str | None = None,
    forced_provider: EmbeddingProviderName | None = None,
):
    """Generate normalized embeddings through the configured embedding provider."""
    settings = load_settings()
    provider = registry.get_embedding_provider(
        settings, forced_provider=forced_provider
    )
    selected_model = model_name or settings.embedding_model
    request = EmbeddingRequest(
        texts=texts,
        force_floor=force_floor,
        model_name=selected_model,
    )
    return provider.embed(request)


def list_synthesis_providers() -> dict[str, bool]:
    """Return availability status of supported synthesis providers."""
    settings = load_settings()
    return registry.synthesis_provider_status(settings)


def list_embedding_providers() -> dict[str, bool]:
    """Return availability status of supported embedding providers."""
    settings = load_settings()
    return registry.embedding_provider_status(settings)
