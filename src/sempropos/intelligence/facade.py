from __future__ import annotations

import numpy as np

from sempropos.config import (
    SUPPORTED_EMBEDDING_PROVIDERS, 
    SUPPORTED_SYNTHESIS_PROVIDERS, 
    load_intelligence_config
)
from sempropos.config import ProviderRuntimeConfig
from sempropos.intelligence.contracts import StructuredPrompt, SynthesisResult

# ----- Errors -----
from sempropos.intelligence.contracts import (
    ProviderUnavailableError,
)

# ----- Providers -----
import sempropos.intelligence.providers._tier0 as tier0_provider
import sempropos.intelligence.providers._fastembed as fastembed_provider
import sempropos.intelligence.providers._ollama as ollama_provider

# ==================================================
# Facade
# ==================================================
# TODO: Consider removing verification of whether a provider is available or not
def list_synthesis_providers() -> dict[str, bool]:
    """ 
    Check the availability of all synthesis providers.

    Returns:
        dict[str, bool]: e.g. {"ollama": False, "tier0": True}
    """
    #TODO: Figure out a better way to verify provider availability and avoid redundant dicts of provider names in the list methods.
    providers_status = {
        "tier0": tier0_provider.is_available(),
        "ollama": ollama_provider.is_available(),
    }

    return {p : providers_status.get(p) for p in SUPPORTED_SYNTHESIS_PROVIDERS}

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

    return {p : providers_status.get(p) for p in SUPPORTED_EMBEDDING_PROVIDERS}

def detect_synthesis_provider() -> tuple[str, ProviderRuntimeConfig | None]:
    """
    Determine the active synthesis backend and retrieve its configuration.

    Returns:
        tuple: (provider_name, provider_config)
    
    Raises:
        ProviderUnavailableError: If the configured provider is offline.
    """
    intelligence_config = load_intelligence_config()
    configured_provider = intelligence_config.synthesis_provider
    provider_config = intelligence_config.providers.get(configured_provider)

    if configured_provider == "ollama" and ollama_provider.is_available():
        return "ollama", provider_config
    elif configured_provider == "tier0" and tier0_provider.is_available():
        return "tier0", provider_config
    else:
        raise ProviderUnavailableError(f"Configured synthesis provider '{configured_provider}' is not available.")
    

def detect_embedding_provider() -> tuple[str, ProviderRuntimeConfig | None]:
    """
    Determine the active embedding backend and retrieve its configuration.

    Returns:
        tuple: (provider_name, provider_config)
    
    Raises:
        ProviderUnavailableError: If the configured provider is offline.
    """
    intelligence_config = load_intelligence_config()
    configured_provider = intelligence_config.embedding_provider
    provider_config = intelligence_config.providers.get(configured_provider)

    if configured_provider == "fastembed_local" and fastembed_provider.is_available():
        return "fastembed_local", provider_config
    elif configured_provider == "ollama" and ollama_provider.is_available():
        return "ollama", provider_config
    else:
        raise ProviderUnavailableError(f"Configured embedding provider '{configured_provider}' is not available.")

def embed_texts(
        texts: list[str],
        backend: str | None = None,
        model: str | None = None
    ) -> np.ndarray:
    """
    Convert a list of text chunks into a 2D float32 matrix.
    Routes to the configured embedding provider (e.g. FastEmbed).

    Args:
        texts (list[str]): A batch of strings to embed.

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
        provider_config = config_file.providers.get(configured_provider)
    else:
        configured_provider, provider_config = detect_embedding_provider()

    actual_model = model
    if not actual_model:
        actual_model = provider_config.model if provider_config else None

    match configured_provider:
        case "fastembed_local":
            return fastembed_provider.embed_texts(texts, actual_model)
        case "ollama":
            return ollama_provider.embed_texts(texts, actual_model)
        case _:
            raise ProviderUnavailableError(f"Configured embedding provider '{configured_provider}' is not available.")

def synthesize(
        prompt: StructuredPrompt, 
        backend: str | None = None, 
        model: str | None = None
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
        provider_config = config_file.providers.get(configured_provider)
    else:
        configured_provider, provider_config = detect_synthesis_provider()

    if configured_provider not in SUPPORTED_SYNTHESIS_PROVIDERS:
        raise ProviderUnavailableError(f"Configured synthesis provider '{backend}' is not available.")

    actual_model = model
    if not actual_model:
        actual_model = provider_config.model if provider_config else None

    match configured_provider:
        case "ollama":
            return ollama_provider.synthesize(prompt, actual_model)
        case "tier0":
            return tier0_provider.synthesize(prompt, actual_model)
        case _:
            raise ProviderUnavailableError(f"Configured synthesis provider '{configured_provider}' is not available.")


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
        case "tier0":
            return tier0_provider.get_available_models()
        case _:
            raise ProviderUnavailableError(f"Provider '{provider_name}' is not recognized or supported.")