"""Lazy-loading facade for synthesis and embedding providers.

Provider modules (especially FastEmbed/ONNX and llama-cpp-python) are imported
strictly on demand so CLI startup and pure-lexical commands never pay heavy
ML runtime import costs.
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np

from sempropos.config import (
    SUPPORTED_EMBEDDING_PROVIDERS,
    SUPPORTED_SYNTHESIS_PROVIDERS,
    EmbeddingConfig,
    SynthesisConfig,
    load_config,
)
from sempropos.intelligence.contracts import (
    ProviderUnavailableError,
    StructuredPrompt,
    SynthesisOptions,
    SynthesisResult,
)
from sempropos.utils import keyring_load_secret


def list_synthesis_providers() -> dict[str, bool]:
    """Check availability of all supported synthesis providers."""
    from sempropos.intelligence.providers import (
        _local_gguf,
        _ollama,
        _openai_compatible,
        _tier0,
    )

    cfg = load_config()
    status = {
        "local_gguf": _local_gguf.is_available(cfg.synthesis),
        "ollama": _ollama.is_available(cfg.synthesis.base_url),
        "openai_compatible": _openai_compatible.is_available(cfg.synthesis),
        "tier0": _tier0.is_available(),
    }
    return {name: status.get(name, False) for name in SUPPORTED_SYNTHESIS_PROVIDERS}


def list_embedding_providers() -> dict[str, bool]:
    """Check availability of all supported embedding providers."""
    from sempropos.intelligence.providers import _fastembed, _ollama

    cfg = load_config()
    status = {
        "fastembed_local": _fastembed.is_available(),
        "ollama": _ollama.is_available(cfg.embedding.base_url),
        "none": True,
    }
    return {name: status.get(name, False) for name in SUPPORTED_EMBEDDING_PROVIDERS}


def detect_synthesis_provider() -> tuple[str, SynthesisConfig]:
    """Return the active configured synthesis provider and its config."""
    role = load_config().synthesis
    provider = role.provider

    if provider == "local_gguf":
        from sempropos.intelligence.providers import _local_gguf

        if _local_gguf.is_available(role):
            return "local_gguf", role
    elif provider == "ollama":
        from sempropos.intelligence.providers import _ollama

        if _ollama.is_available(role.base_url):
            return "ollama", role
    elif provider == "openai_compatible":
        from sempropos.intelligence.providers import _openai_compatible

        if _openai_compatible.is_available(role):
            return "openai_compatible", role
    elif provider == "tier0":
        return "tier0", role

    raise ProviderUnavailableError(
        f"Configured synthesis provider '{provider}' is not available."
    )


def detect_embedding_provider() -> tuple[str, EmbeddingConfig]:
    """Return the active configured embedding provider and its config."""
    role = load_config().embedding
    provider = role.provider

    if provider == "none":
        return "none", role
    if provider == "fastembed_local":
        from sempropos.intelligence.providers import _fastembed

        if _fastembed.is_available():
            return "fastembed_local", role
    elif provider == "ollama":
        from sempropos.intelligence.providers import _ollama

        if _ollama.is_available(role.base_url):
            return "ollama", role

    raise ProviderUnavailableError(
        f"Configured embedding provider '{provider}' is not available."
    )


def is_embedding_model_warm() -> bool:
    """Return True when the local embedding engine is already resident in RAM."""
    import sys

    mod = sys.modules.get("sempropos.intelligence.providers._fastembed")
    if mod is None:
        return False
    return bool(getattr(mod, "is_model_loaded", lambda: False)())


def embed_texts(
    texts: list[str],
    backend: str | None = None,
    model: str | None = None,
    kind: str = "document",
) -> np.ndarray[Any, Any]:
    """Embed a batch of texts using the configured (or overridden) embedding provider."""
    if backend:
        provider = backend
        role = load_config().embedding
    else:
        provider, role = detect_embedding_provider()

    actual_model = model or role.model
    if provider == "fastembed_local":
        from sempropos.intelligence.providers import _fastembed

        return _fastembed.embed_texts(texts, actual_model, kind)
    if provider == "ollama":
        from sempropos.intelligence.providers import _ollama

        return _ollama.embed_texts(
            texts, actual_model, base_url=role.base_url, timeout_seconds=role.timeout_seconds
        )
    if provider == "none":
        return np.empty((0, 0), dtype=np.float32)

    raise ProviderUnavailableError(f"Configured embedding provider '{provider}' is not available.")


def _resolve_api_key(role: SynthesisConfig | None) -> str | None:
    if role is None:
        return None
    if role.api_key_env:
        val = os.environ.get(role.api_key_env)
        if val:
            return val.strip()
    if role.api_key_secret:
        secret = keyring_load_secret(role.api_key_secret)
        if secret:
            return secret
    return role.api_key_plaintext


def _options_from_role(role: SynthesisConfig | None) -> SynthesisOptions:
    if role is None:
        return SynthesisOptions()
    return SynthesisOptions(
        think=role.think,
        temperature=role.temperature,
        max_tokens=role.max_tokens,
        keep_alive=role.keep_alive,
        stop=role.stop,
        base_url=role.base_url,
        api_key=_resolve_api_key(role),
        timeout_seconds=role.timeout_seconds,
        n_ctx=role.n_ctx,
        n_gpu_layers=role.n_gpu_layers,
        threads=role.threads,
    )


def synthesize(
    prompt: StructuredPrompt,
    backend: str | None = None,
    model: str | None = None,
) -> SynthesisResult:
    """Execute grounded command synthesis against the selected provider."""
    if backend:
        provider = backend
        role = load_config().synthesis
    else:
        provider, role = detect_synthesis_provider()

    if provider not in SUPPORTED_SYNTHESIS_PROVIDERS:
        raise ProviderUnavailableError(
            f"Configured synthesis provider '{provider}' is not supported."
        )

    actual_model = model or role.model
    options = _options_from_role(role)

    if provider == "local_gguf":
        from sempropos.intelligence.providers import _local_gguf

        return _local_gguf.synthesize(prompt, actual_model, options)
    if provider == "ollama":
        from sempropos.intelligence.providers import _ollama

        return _ollama.synthesize(prompt, actual_model, options)
    if provider == "openai_compatible":
        from sempropos.intelligence.providers import _openai_compatible

        return _openai_compatible.synthesize(prompt, actual_model, options)
    if provider == "tier0":
        from sempropos.intelligence.providers import _tier0

        return _tier0.synthesize(prompt, actual_model, options)

    raise ProviderUnavailableError(f"Configured synthesis provider '{provider}' is not available.")


def warm_synthesis_provider() -> tuple[str, str] | None:
    """Opportunistically warm the configured synthesis model."""
    try:
        provider, role = detect_synthesis_provider()
    except ProviderUnavailableError:
        return None

    if provider == "ollama" and role.model:
        from sempropos.intelligence.providers import _ollama

        try:
            _ollama.warm_model(role.model, keep_alive=role.keep_alive, base_url=role.base_url)
            return provider, f"warmed {role.model}"
        except Exception as exc:  # noqa: BLE001
            return provider, f"warm-up failed: {exc}"

    if provider == "local_gguf":
        from sempropos.intelligence.providers import _local_gguf

        try:
            detail = _local_gguf.warm_model(role.model, _options_from_role(role))
            return provider, detail
        except Exception as exc:  # noqa: BLE001
            return provider, f"warm-up failed: {exc}"

    return provider, "no warm-up required"


def shutdown_providers() -> None:
    """Cleanly release any loaded models or managed local child processes."""
    import sys

    gguf_mod = sys.modules.get("sempropos.intelligence.providers._local_gguf")
    if gguf_mod is not None:
        gguf_mod.shutdown()
    fe_mod = sys.modules.get("sempropos.intelligence.providers._fastembed")
    if fe_mod is not None:
        fe_mod.unload_model()


def get_provider_models(provider_name: str) -> list[str]:
    """Retrieve available models for a given synthesis or embedding provider."""
    if provider_name == "local_gguf":
        from sempropos.intelligence.providers import _local_gguf

        return _local_gguf.get_available_models()
    if provider_name == "ollama":
        from sempropos.intelligence.providers import _ollama

        return _ollama.get_available_models(load_config().synthesis.base_url)
    if provider_name == "fastembed_local":
        from sempropos.intelligence.providers import _fastembed

        return _fastembed.get_available_models()
    if provider_name == "openai_compatible":
        from sempropos.intelligence.providers import _openai_compatible

        return _openai_compatible.get_available_models()
    if provider_name == "tier0":
        from sempropos.intelligence.providers import _tier0

        return _tier0.get_available_models()
    if provider_name == "none":
        return []
    raise ProviderUnavailableError(f"Provider '{provider_name}' is not recognized or supported.")
