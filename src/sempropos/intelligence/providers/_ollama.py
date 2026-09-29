"""Ollama provider implementation for synthesis and embedding using the official SDK."""

from __future__ import annotations

import contextlib
import importlib.util
import urllib.request

import numpy as np

from sempropos.intelligence.contracts import (
    ProviderConfigurationError,
    ProviderExecutionError,
    ProviderUnavailableError,
    StructuredPrompt,
    SynthesisOptions,
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
            raise ProviderUnavailableError(
                f"Ollama SDK is not installed and could not be installed automatically: {e}"
            ) from e

    import ollama

    return ollama


def is_available() -> bool:
    """Check if the Ollama daemon is reachable locally, regardless of the SDK."""
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=1.5):
            return True
    except Exception:  # noqa: BLE001
        return False


def get_available_models() -> list[str]:
    if not is_available():
        raise ProviderUnavailableError(
            "Ollama daemon is missing or unreachable. Please start Ollama."
        )

    ollama = _get_ollama_module()

    try:
        models_info = ollama.list()

        if hasattr(models_info, "models"):
            return [model.model for model in models_info.models]

        # fallback for older SDK versions
        model_list = (
            models_info.get("models", models_info)
            if isinstance(models_info, dict)
            else models_info
        )
        return [
            model["name"] if isinstance(model, dict) else model.model
            for model in model_list
        ]

    except Exception as e:
        raise ProviderExecutionError(f"Failed to retrieve models from Ollama: {e}") from e


def _extract_content(response: object) -> str:
    """Pull the assistant message text out of an Ollama chat response."""
    if response is None:
        return ""

    # pydantic-style response (modern SDK)
    message = getattr(response, "message", None)
    if message is not None:
        content = getattr(message, "content", None)
        if content:
            return str(content)

    # dict-style response (older SDK / raw API)
    if isinstance(response, dict):
        message = response.get("message")
        if isinstance(message, dict):
            return str(message.get("content") or "")

    return ""


def _build_chat_kwargs(
    prompt: StructuredPrompt,
    model: str,
    options: SynthesisOptions,
) -> dict:
    formatted_context = context_to_str(prompt.context)

    generation: dict = {
        "temperature": options.temperature,
        "num_predict": options.max_tokens,
    }
    if options.stop:
        generation["stop"] = options.stop

    kwargs: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt.system},
            {
                "role": "user",
                "content": f"Context:\n{formatted_context}\n\nTask: {prompt.query}",
            },
        ],
        "stream": False,
        "options": generation,
    }
    if options.keep_alive is not None:
        kwargs["keep_alive"] = options.keep_alive
    if options.think is not None:
        kwargs["think"] = options.think

    return kwargs


def synthesize(
    prompt: StructuredPrompt,
    model: str | None = None,
    options: SynthesisOptions | None = None,
) -> SynthesisResult:
    if not is_available():
        raise ProviderUnavailableError(
            "Ollama daemon is missing or unreachable. Please start Ollama."
        )
    if not model:
        raise ProviderConfigurationError("No model configured for Ollama synthesis.")

    options = options or SynthesisOptions()
    ollama = _get_ollama_module()
    kwargs = _build_chat_kwargs(prompt, model, options)

    try:
        try:
            response = ollama.chat(**kwargs)
        except Exception as exc:  # noqa: BLE001
            # Some models do not support the `think` parameter at all. Retrying
            # without it keeps those models usable instead of failing outright.
            if "think" in str(exc).lower() and "think" in kwargs:
                kwargs.pop("think")
                response = ollama.chat(**kwargs)
            else:
                raise

        command = _extract_content(response).strip()
        return SynthesisResult(
            provider="ollama",
            command=command,
            raw_output=command,
            metadata={"model": model},
        )
    except ProviderExecutionError:
        raise
    except Exception as e:
        raise ProviderExecutionError(f"Ollama synthesis failed: {e}") from e


def warm_model(model: str, keep_alive: str | int | None = "30m") -> None:
    """Best-effort: load *model* into memory so the first query is fast."""
    ollama = _get_ollama_module()
    kwargs: dict = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
        "options": {"num_predict": 1},
    }
    if keep_alive is not None:
        kwargs["keep_alive"] = keep_alive
    with contextlib.suppress(Exception):
        # Warm-up is opportunistic; a failure here must never break setup.
        ollama.chat(**kwargs)


def embed_texts(texts: list[str], model: str | None = None) -> np.ndarray:
    if not is_available():
        raise ProviderUnavailableError(
            "Ollama daemon is missing or unreachable. Please start Ollama."
        )
    if not model:
        raise ProviderConfigurationError("No model configured for Ollama embedding.")

    ollama = _get_ollama_module()

    try:
        response = ollama.embed(model=model, input=texts)
        embeddings = getattr(response, "embeddings", None)
        if embeddings is None and isinstance(response, dict):
            embeddings = response.get("embeddings", [])
        return np.array(embeddings or [], dtype=np.float32)
    except Exception as e:
        raise ProviderExecutionError(f"Ollama embedding failed: {e}") from e
