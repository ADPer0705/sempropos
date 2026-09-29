"""OpenAI-compatible synthesis provider.

Works with any HTTP endpoint that implements the OpenAI ``/chat/completions``
contract: OpenAI itself, vLLM, LM Studio, llama.cpp's server, Ollama's OpenAI
shim, or a self-hosted gateway. Configuration comes from the synthesis
``RoleConfig`` (``base_url`` / ``api_key_env`` / ``api_key_secret``) or the
``SEMPROPOS_OPENAI_BASE_URL`` / ``SEMPROPOS_OPENAI_API_KEY`` environment
variables.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from sempropos.config import RoleConfig, load_intelligence_config
from sempropos.intelligence.contracts import (
    ProviderConfigurationError,
    ProviderExecutionError,
    ProviderUnavailableError,
    StructuredPrompt,
    SynthesisOptions,
    SynthesisResult,
)
from sempropos.intelligence.prompt import context_to_str

DEFAULT_BASE_URL = "https://api.openai.com/v1"
BASE_URL_ENV = "SEMPROPOS_OPENAI_BASE_URL"
API_KEY_ENV = "SEMPROPOS_OPENAI_API_KEY"


def _resolved_config(role_config: RoleConfig | None) -> RoleConfig:
    """Return the synthesis role config to use for this provider."""
    if role_config is not None:
        return role_config
    return load_intelligence_config().synthesis


def _base_url(role_config: RoleConfig | None) -> str | None:
    config = _resolved_config(role_config)
    if config.base_url:
        return config.base_url
    if os.environ.get(BASE_URL_ENV):
        return os.environ[BASE_URL_ENV]
    if _api_key(role_config, None):
        return DEFAULT_BASE_URL
    return None


def _api_key(role_config: RoleConfig | None, options: SynthesisOptions | None) -> str | None:
    if options and options.api_key:
        return options.api_key
    config = _resolved_config(role_config)
    if config.api_key_env and os.environ.get(config.api_key_env):
        return os.environ[config.api_key_env].strip()
    if config.api_key_secret:
        from sempropos.utils import keyring_load_secret

        secret = keyring_load_secret(config.api_key_secret)
        if secret:
            return secret
    return config.api_key_plaintext or os.environ.get(API_KEY_ENV)


def is_available(role_config: RoleConfig | None = None) -> bool:
    """OpenAI-compatible endpoints are available once a base URL is configured."""
    return bool(_base_url(role_config))


def get_available_models() -> list[str]:
    base_url = _base_url(None)
    if not base_url:
        return []

    request = urllib.request.Request(f"{base_url.rstrip('/')}/models", method="GET")
    api_key = _api_key(None, None)
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.load(response)
    except Exception:  # noqa: BLE001 — listing is best-effort
        return []

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    return [str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")]


def synthesize(
    prompt: StructuredPrompt,
    model: str | None = None,
    options: SynthesisOptions | None = None,
) -> SynthesisResult:
    options = options or SynthesisOptions()
    role_config = _resolved_config(None)
    base_url = options.base_url or _base_url(role_config)
    if not base_url:
        raise ProviderConfigurationError(
            "No base_url configured for the openai_compatible synthesis provider."
        )
    if not model:
        raise ProviderConfigurationError(
            "No model configured for the openai_compatible synthesis provider."
        )

    formatted_context = context_to_str(prompt.context)
    body: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt.system},
            {
                "role": "user",
                "content": f"Context:\n{formatted_context}\n\nTask: {prompt.query}",
            },
        ],
        "temperature": options.temperature,
        "max_tokens": options.max_tokens,
        "stream": False,
    }
    if options.stop:
        body["stop"] = options.stop

    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    api_key = _api_key(role_config, options)
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(request, timeout=options.timeout_seconds) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise ProviderExecutionError(
            f"openai_compatible endpoint returned HTTP {exc.code}: {detail[:300]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ProviderUnavailableError(
            f"openai_compatible endpoint is unreachable: {exc.reason}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise ProviderExecutionError(f"openai_compatible synthesis failed: {exc}") from exc

    command = ""
    try:
        command = str(payload["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError):
        command = ""

    return SynthesisResult(
        provider="openai_compatible",
        command=command,
        raw_output=command,
        metadata={"model": model, "base_url": base_url},
    )
