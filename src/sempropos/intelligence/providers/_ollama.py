"""Ollama provider implementation for synthesis and embedding using the official SDK."""

from __future__ import annotations
import importlib.util
import numpy as np
import urllib.request

from sempropos.intelligence.contracts import (
    ProviderConfigurationError,
    ProviderExecutionError,
    ProviderUnavailableError,
    StructuredPrompt,
    SynthesisResult,
)
from sempropos.intelligence.prompt import context_to_str
from sempropos.utils import install_and_import

def _get_ollama_module():
    """Ensure the ollama SDK is installed and return the module reference."""
    if not importlib.util.find_spec("ollama"):
        try:
            install_and_import("ollama")
        except Exception as e:
            raise ProviderUnavailableError(f"Ollama SDK is not installed and could not be installed automatically: {e}") from e
    
    import ollama
    return ollama

def is_available() -> bool:
    """Check if the Ollama daemon is reachable locally, regardless of the SDK."""
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=1.5):
            return True
    except Exception:
        return False

def get_available_models() -> list[str]:
    if not is_available():
        raise ProviderUnavailableError("Ollama daemon is missing or unreachable. Please start Ollama.")
    
    ollama = _get_ollama_module()

    try:
        models_info = ollama.list()
        
        if hasattr(models_info, "models"):
            return [model.model for model in models_info.models]
        
        # fallback for older SDK versions
        model_list = models_info.get("models", models_info) if isinstance(models_info, dict) else models_info
        return [model["name"] if isinstance(model, dict) else model.model for model in model_list]
    
    except Exception as e:
        raise ProviderExecutionError(f"Failed to retrieve models from Ollama: {e}") from e

def synthesize(prompt: StructuredPrompt, model: str | None = None) -> SynthesisResult:
    if not is_available():
        raise ProviderUnavailableError("Ollama daemon is missing or unreachable. Please start Ollama.")
    if not model:
        raise ProviderConfigurationError("No model configured for Ollama synthesis.")

    ollama = _get_ollama_module()
    formatted_context = context_to_str(prompt.context)

    try:
        response = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": prompt.system},
                {
                    "role": "user",
                    "content": f"Context:\n{formatted_context}\n\nTask: {prompt.query}",
                },
            ],
            stream=False,
        )

        command = response.get("message", {}).get("content", "").strip()
        return SynthesisResult(
            provider="ollama",
            command=command,
            raw_output=command,
            metadata={"model": model},
        )
    except Exception as e:
        raise ProviderExecutionError(f"Ollama synthesis failed: {e}") from e


def embed_texts(texts: list[str], model: str | None = None) -> np.ndarray:
    if not is_available():
        raise ProviderUnavailableError("Ollama daemon is missing or unreachable. Please start Ollama.")
    if not model:
        raise ProviderConfigurationError("No model configured for Ollama embedding.")

    ollama = _get_ollama_module()

    try:
        response = ollama.embed(model=model, input=texts)
        embeddings = response.get("embeddings", [])
        return np.array(embeddings, dtype=np.float32)
    except Exception as e:
        raise ProviderExecutionError(f"Ollama embedding failed: {e}") from e