"""Provider registry for explicit synthesis and embedding provider selection."""

from __future__ import annotations

from sempropos.intelligence.config import IntelligenceSettings
from sempropos.intelligence.contracts import (
    EmbeddingProviderName,
    ProviderUnavailableError,
    SynthesisProviderName,
)
from sempropos.intelligence.embeddings.fastembed_local import FastEmbedLocalProvider
from sempropos.intelligence.embeddings.remote import (
    HuggingFaceEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
)
from sempropos.intelligence.providers.anthropic import AnthropicProvider
from sempropos.intelligence.providers.base import EmbeddingProvider, SynthesisProvider
from sempropos.intelligence.providers.gemini import GeminiProvider
from sempropos.intelligence.providers.huggingface import HuggingFaceProvider
from sempropos.intelligence.providers.llama_cpp import LlamaCppProvider
from sempropos.intelligence.providers.mistral import MistralProvider
from sempropos.intelligence.providers.ollama import OllamaProvider
from sempropos.intelligence.providers.openai_compatible import OpenAICompatibleProvider
from sempropos.intelligence.providers.tier0 import Tier0Provider

SYNTHESIS_FALLBACK_ORDER: tuple[SynthesisProviderName, ...] = (
    "llama_cpp",
    "ollama",
    "openai_compatible",
    "anthropic",
    "gemini",
    "mistral",
    "huggingface",
    "tier0",
)


def get_synthesis_provider(
    settings: IntelligenceSettings,
    *,
    forced_provider: SynthesisProviderName | None = None,
) -> SynthesisProvider:
    """Instantiate and return selected synthesis provider."""
    selected = forced_provider or settings.synthesis_provider
    runtime = settings.providers

    if selected == "llama_cpp":
        return LlamaCppProvider(runtime.get("llama_cpp"))
    if selected == "ollama":
        return OllamaProvider(runtime.get("ollama"))
    if selected == "openai_compatible":
        return OpenAICompatibleProvider(runtime.get("openai_compatible"))
    if selected == "anthropic":
        return AnthropicProvider(runtime.get("anthropic"))
    if selected == "gemini":
        return GeminiProvider(runtime.get("gemini"))
    if selected == "mistral":
        return MistralProvider(runtime.get("mistral"))
    if selected == "huggingface":
        return HuggingFaceProvider(runtime.get("huggingface"))
    if selected == "tier0":
        return Tier0Provider()

    raise ProviderUnavailableError(f"Unsupported synthesis provider: {selected}")


def resolve_synthesis_provider(
    settings: IntelligenceSettings,
    *,
    forced_provider: SynthesisProviderName | None = None,
) -> SynthesisProvider:
    """Resolve a healthy synthesis provider using preference then fallback order."""
    if forced_provider is not None:
        provider = get_synthesis_provider(settings, forced_provider=forced_provider)
        if provider.available():
            return provider
        raise ProviderUnavailableError(
            f"Configured provider is unavailable: {forced_provider}"
        )

    preferred = get_synthesis_provider(settings)
    if preferred.available():
        return preferred

    for candidate in SYNTHESIS_FALLBACK_ORDER:
        if candidate == settings.synthesis_provider:
            continue
        provider = get_synthesis_provider(settings, forced_provider=candidate)
        if provider.available():
            return provider

    return get_synthesis_provider(settings, forced_provider="tier0")


def get_embedding_provider(
    settings: IntelligenceSettings,
    *,
    forced_provider: EmbeddingProviderName | None = None,
) -> EmbeddingProvider:
    """Instantiate and return selected embedding provider."""
    selected = forced_provider or settings.embedding_provider
    runtime = settings.providers

    if selected == "fastembed_local":
        return FastEmbedLocalProvider()
    if selected == "openai_compatible":
        return OpenAICompatibleEmbeddingProvider(runtime.get("openai_compatible"))
    if selected == "huggingface":
        return HuggingFaceEmbeddingProvider(runtime.get("huggingface"))

    raise ProviderUnavailableError(f"Unsupported embedding provider: {selected}")


def synthesis_provider_status(settings: IntelligenceSettings) -> dict[str, bool]:
    """Return availability status for each synthesis provider."""
    statuses: dict[str, bool] = {}
    for name in (
        "llama_cpp",
        "ollama",
        "openai_compatible",
        "anthropic",
        "gemini",
        "mistral",
        "huggingface",
        "tier0",
    ):
        provider = get_synthesis_provider(settings, forced_provider=name)  # type: ignore[arg-type]
        statuses[name] = provider.available()
    return statuses


def embedding_provider_status(settings: IntelligenceSettings) -> dict[str, bool]:
    """Return availability status for each embedding provider."""
    statuses: dict[str, bool] = {}
    for name in ("fastembed_local", "openai_compatible", "huggingface"):
        provider = get_embedding_provider(settings, forced_provider=name)  # type: ignore[arg-type]
        statuses[name] = (
            provider.available() if hasattr(provider, "available") else True
        )
    return statuses
