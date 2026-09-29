from __future__ import annotations

import numpy as np

import sempropos.intelligence.providers._fastembed as fastembed_provider
import sempropos.intelligence.providers._ollama as ollama_provider
import sempropos.intelligence.providers._openai_compatible as openai_provider

# ----- Providers -----
import sempropos.intelligence.providers._tier0 as tier0_provider
from sempropos.config import (
    SUPPORTED_EMBEDDING_PROVIDERS,
    SUPPORTED_SYNTHESIS_PROVIDERS,
    RoleConfig,
    load_intelligence_config,
)

# ----- Errors -----
from sempropos.intelligence.contracts import (
    ProviderUnavailableError,
    StructuredPrompt,
    SynthesisOptions,
    SynthesisResult,
)
from sempropos.utils import keyring_load_secret

# ==================================================
# Facade
# ==================================================


def list_synthesis_providers() -> dict[str, bool]:
    """
    Check the availability of all synthesis providers.

    Returns:
        dict[str, bool]: e.g. {"ollama": False, "tier0": True}
    """
    providers_status = {
        "tier0": tier0_provider.is_available(),
        "ollama": ollama_provider.is_available(),
        "openai_compatible": openai_provider.is_available(),
    }

    return {p: providers_status.get(p, False) for p in SUPPORTED_SYNTHESIS_PROVIDERS}


def list_embedding_providers() -> dict[str, bool]:
    """
    Check the availability of all embedding providers.

    Returns:
        dict[str, bool]: e.g. {"fastembed_local": True}
    """
    providers_status = {
        "fastembed_local": fastembed_provider.is_available(),
        "ollama": ollama_provider.is_available(),
    }

    return {p: providers_status.get(p, False) for p in SUPPORTED_EMBEDDING_PROVIDERS}


def detect_synthesis_provider() -> tuple[str, RoleConfig]:
    """
    Determine the active synthesis backend and retrieve its configuration.

    Returns:
        tuple: (provider_name, role_config)

    Raises:
        ProviderUnavailableError: If the configured provider is offline.
    """
    intelligence_config = load_intelligence_config()
    role_config = intelligence_config.synthesis
    configured_provider = role_config.provider

    match configured_provider:
        case "ollama" if ollama_provider.is_available():
            return "ollama", role_config
        case "openai_compatible" if openai_provider.is_available(role_config):
            return "openai_compatible", role_config
        case "tier0" if tier0_provider.is_available():
            return "tier0", role_config
        case _:
            raise ProviderUnavailableError(
                f"Configured synthesis provider '{configured_provider}' is not available."
            )


def detect_embedding_provider() -> tuple[str, RoleConfig]:
    """
    Determine the active embedding backend and retrieve its configuration.

    Returns:
        tuple: (provider_name, role_config)

    Raises:
        ProviderUnavailableError: If the configured provider is offline.
    """
    intelligence_config = load_intelligence_config()
    role_config = intelligence_config.embedding
    configured_provider = role_config.provider

    if configured_provider == "fastembed_local" and fastembed_provider.is_available():
        return "fastembed_local", role_config
    elif configured_provider == "ollama" and ollama_provider.is_available():
        return "ollama", role_config
    else:
        raise ProviderUnavailableError(
            f"Configured embedding provider '{configured_provider}' is not available."
        )


def embed_texts(
    texts: list[str],
    backend: str | None = None,
    model: str | None = None,
    kind: str = "document",
) -> np.ndarray:
    """
    Convert a list of text chunks into a 2D float32 matrix.
    Routes to the configured embedding provider (e.g. FastEmbed).

    Args:
        texts (list[str]): A batch of strings to embed.
        kind (str): "document" when indexing, "query" when searching. Task-aware
            embedding models use different prefixes for each.

    Returns:
        np.ndarray: A 2D array of shape (len(texts), embedding_dim).

    Raises:
        ProviderUnavailableError: If the configured embedding provider is not available.
        ProviderExecutionError: If the provider fails during execution.
        IntelligenceError: For any other intelligence-layer failures.
    """
    if backend:
        configured_provider = backend
        config_file = load_intelligence_config()
        # Use the embedding role config; the backend override just selects which provider to dispatch to
        role_config = config_file.embedding
    else:
        configured_provider, role_config = detect_embedding_provider()

    actual_model = model
    if not actual_model:
        actual_model = role_config.model if role_config else None

    match configured_provider:
        case "fastembed_local":
            return fastembed_provider.embed_texts(texts, actual_model, kind)
        case "ollama":
            return ollama_provider.embed_texts(texts, actual_model)
        case _:
            raise ProviderUnavailableError(
                f"Configured embedding provider '{configured_provider}' is not available."
            )


def _resolve_api_key(role_config: RoleConfig | None) -> str | None:
    """Resolve an API key from (in order) env var, keyring, then plaintext."""
    if role_config is None:
        return None

    if role_config.api_key_env:
        import os

        value = os.environ.get(role_config.api_key_env)
        if value:
            return value.strip()

    if role_config.api_key_secret:
        value = keyring_load_secret(role_config.api_key_secret)
        if value:
            return value

    return role_config.api_key_plaintext


def _options_from_role(role_config: RoleConfig | None) -> SynthesisOptions:
    """Translate a role configuration into provider-agnostic options."""
    if role_config is None:
        return SynthesisOptions()

    return SynthesisOptions(
        think=role_config.think,
        temperature=role_config.temperature,
        max_tokens=role_config.max_tokens,
        keep_alive=role_config.keep_alive,
        stop=role_config.stop,
        base_url=role_config.base_url,
        api_key=_resolve_api_key(role_config),
        timeout_seconds=role_config.timeout_seconds,
    )


def synthesize(
    prompt: StructuredPrompt,
    backend: str | None = None,
    model: str | None = None,
) -> SynthesisResult:
    """
    Send the assembled few-shot prompt to the LLM to generate the final shell command.

    Args:
        prompt (StructuredPrompt): The fully constructed context from synthesis.prompt
        backend (str | None): An optional override (like if the user passed --backend to the CLI)
        model (str | None): The model to use for synthesis, if applicable

    Returns:
        SynthesisResult: The normalized output from the provider, including the final command and any raw output.

    Raises:
        ProviderUnavailableError: If the selected synthesis provider is not available.
        ProviderExecutionError: If the provider fails during execution.
        IntelligenceError: For any other intelligence-layer failures.
    """
    if backend:
        configured_provider = backend
        config_file = load_intelligence_config()
        # Use the synthesis role config; the backend override just selects which provider to dispatch to
        role_config = config_file.synthesis
    else:
        configured_provider, role_config = detect_synthesis_provider()

    if configured_provider not in SUPPORTED_SYNTHESIS_PROVIDERS:
        raise ProviderUnavailableError(
            f"Configured synthesis provider '{configured_provider}' is not available."
        )

    actual_model = model
    if not actual_model:
        actual_model = role_config.model if role_config else None

    options = _options_from_role(role_config)

    match configured_provider:
        case "ollama":
            return ollama_provider.synthesize(prompt, actual_model, options)
        case "openai_compatible":
            return openai_provider.synthesize(prompt, actual_model, options)
        case "tier0":
            return tier0_provider.synthesize(prompt, actual_model, options)
        case _:
            raise ProviderUnavailableError(
                f"Configured synthesis provider '{configured_provider}' is not available."
            )


def warm_synthesis_provider() -> tuple[str, str] | None:
    """Best-effort warm-up of the configured synthesis provider.

    Loading a model on first use is the dominant cold-start cost. Calling this
    after `install` (or on demand) moves that cost off the critical path of the
    first user query.

    Returns:
        A ``(provider, message)`` tuple describing what happened, or ``None``
        when there is nothing to warm.
    """
    try:
        provider_name, role_config = detect_synthesis_provider()
    except ProviderUnavailableError:
        return None

    if provider_name == "ollama" and role_config.model:
        try:
            ollama_provider.warm_model(
                role_config.model, keep_alive=role_config.keep_alive
            )
            return provider_name, f"warmed {role_config.model}"
        except Exception as exc:  # noqa: BLE001
            return provider_name, f"warm-up failed: {exc}"

    return provider_name, "no warm-up required"


# ----- Other utilities -----
def get_provider_models(provider_name: str) -> list[str]:
    """
    Retrieve the list of available models for a given provider.

    Args:
        provider_name (str): The name of the provider (e.g. "ollama", "fastembed_local")

    Returns:
        list[str]: A list of available models for the specified provider.
    """
    match provider_name:
        case "ollama":
            return ollama_provider.get_available_models()
        case "fastembed_local":
            return fastembed_provider.get_available_models()
        case "openai_compatible":
            return openai_provider.get_available_models()
        case "tier0":
            return tier0_provider.get_available_models()
        case _:
            raise ProviderUnavailableError(
                f"Provider '{provider_name}' is not recognized or supported."
            )
