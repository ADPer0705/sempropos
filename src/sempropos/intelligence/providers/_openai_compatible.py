"""OpenAI-compatible HTTP synthesis provider (OpenAI, vLLM, LM Studio, llama-server, gateways)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from sempropos.config import SynthesisConfig, load_config
from sempropos.intelligence.contracts import (
    ProviderConfigurationError,
    ProviderExecutionError,
    ProviderUnavailableError,
    StructuredPrompt,
    SynthesisOptions,
    SynthesisResult,
)
from sempropos.intelligence.prompt import context_to_str, parse_synthesis_output

DEFAULT_BASE_URL = "https://api.openai.com/v1"
BASE_URL_ENV = "SEMPROPOS_OPENAI_BASE_URL"
API_KEY_ENV = "SEMPROPOS_OPENAI_API_KEY"


def _resolved_config(role_config: SynthesisConfig | None) -> SynthesisConfig:
    if role_config is not None:
        return role_config
    return load_config().synthesis


def _api_key(role_config: SynthesisConfig | None, options: SynthesisOptions | None) -> str | None:
    if options and options.api_key:
        return options.api_key
    cfg = _resolved_config(role_config)
    if cfg.api_key_env and os.environ.get(cfg.api_key_env):
        return os.environ[cfg.api_key_env].strip()
    if cfg.api_key_secret:
        from sempropos.utils import keyring_load_secret

        secret = keyring_load_secret(cfg.api_key_secret)
        if secret:
            return secret
    return cfg.api_key_plaintext or os.environ.get(API_KEY_ENV)


def _base_url(role_config: SynthesisConfig | None) -> str | None:
    cfg = _resolved_config(role_config)
    if cfg.base_url:
        return cfg.base_url
    if os.environ.get(BASE_URL_ENV):
        return os.environ[BASE_URL_ENV]
    if _api_key(role_config, None):
        return DEFAULT_BASE_URL
    return None


def is_available(role_config: SynthesisConfig | None = None) -> bool:
    """OpenAI-compatible endpoints are available once a base URL or API key is configured."""
    return bool(_base_url(role_config))


def get_available_models() -> list[str]:
    base_url = _base_url(None)
    if not base_url:
        return []

    req = urllib.request.Request(f"{base_url.rstrip('/')}/models", method="GET")
    api_key = _api_key(None, None)
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            payload = json.load(response)
    except Exception:  # noqa: BLE001
        return []

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    return [str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")]


def _post_completion(
    base_url: str,
    body: dict[str, Any],
    api_key: str | None,
    timeout: float,
) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            decoded = json.load(response)
            return decoded if isinstance(decoded, dict) else {}
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


def synthesize(
    prompt: StructuredPrompt,
    model: str | None = None,
    options: SynthesisOptions | None = None,
) -> SynthesisResult:
    opts = options or SynthesisOptions()
    role_config = _resolved_config(None)
    base_url = opts.base_url or _base_url(role_config)
    if not base_url:
        raise ProviderConfigurationError(
            "No base_url configured for the openai_compatible synthesis provider."
        )
    if not model:
        raise ProviderConfigurationError(
            "No model configured for the openai_compatible synthesis provider."
        )

    formatted_context = context_to_str(prompt.context)
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt.system},
            {
                "role": "user",
                "content": f"Context:\n{formatted_context}\n\nTask: {prompt.query}",
            },
        ],
        "temperature": opts.temperature,
        "max_tokens": opts.max_tokens,
        "stream": False,
    }
    if opts.stop:
        body["stop"] = opts.stop
    if opts.structured_output and prompt.json_schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "sem_command",
                "strict": True,
                "schema": prompt.json_schema,
            },
        }

    api_key = _api_key(role_config, opts)
    try:
        payload = _post_completion(base_url, body, api_key, opts.timeout_seconds)
    except ProviderExecutionError as exc:
        if "response_format" in body and any(
            tok in str(exc).lower() for tok in ("response_format", "json_schema", "400", "422")
        ):
            body.pop("response_format", None)
            payload = _post_completion(base_url, body, api_key, opts.timeout_seconds)
        else:
            raise

    raw = ""
    try:
        raw = str(payload["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError):
        raw = ""

    parsed = parse_synthesis_output(raw)
    return SynthesisResult(
        provider="openai_compatible",
        command=parsed.command or "",
        raw_output=raw,
        tool=parsed.tool,
        explanation=parsed.explanation,
        alternatives=parsed.alternatives,
        metadata={"model": model, "base_url": base_url},
    )
