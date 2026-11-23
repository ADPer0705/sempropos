"""Pure-HTTP Ollama provider for command synthesis and embeddings.

Uses Python stdlib ``urllib.request`` directly against the Ollama REST API —
requiring neither the ``ollama`` Python SDK nor runtime ``pip install`` calls.
"""

from __future__ import annotations

import contextlib
import json
import os
import urllib.error
import urllib.request
from typing import Any

import numpy as np

from sempropos.intelligence.contracts import (
    ProviderConfigurationError,
    ProviderExecutionError,
    ProviderUnavailableError,
    StructuredPrompt,
    SynthesisOptions,
    SynthesisResult,
)
from sempropos.intelligence.prompt import context_to_str, parse_synthesis_output

DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"


def resolve_base_url(base_url: str | None = None) -> str:
    """Resolve the Ollama daemon URL from explicit config, ``OLLAMA_HOST``, or default."""
    raw = (base_url or os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST).strip()
    if not raw.startswith(("http://", "https://")):
        raw = f"http://{raw}"
    return raw.rstrip("/")


def is_available(base_url: str | None = None) -> bool:
    """Check if the Ollama HTTP daemon is reachable."""
    url = f"{resolve_base_url(base_url)}/api/tags"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=1.5):
            return True
    except Exception:  # noqa: BLE001
        return False


def _http_json(
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            decoded = json.load(resp)
            return decoded if isinstance(decoded, dict) else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise ProviderExecutionError(f"Ollama HTTP {exc.code}: {detail[:300]}") from exc
    except urllib.error.URLError as exc:
        raise ProviderUnavailableError(
            f"Ollama daemon is unreachable at {url}: {exc.reason}"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderExecutionError(f"Ollama request failed: {exc}") from exc


def get_available_models(base_url: str | None = None) -> list[str]:
    """List installed models via ``GET /api/tags``."""
    root = resolve_base_url(base_url)
    payload = _http_json(f"{root}/api/tags", timeout=5.0)
    models = payload.get("models")
    if not isinstance(models, list):
        return []
    names: list[str] = []
    for item in models:
        if isinstance(item, dict):
            name = item.get("name") or item.get("model")
            if isinstance(name, str) and name:
                names.append(name)
    return names


def _build_chat_body(
    prompt: StructuredPrompt,
    model: str,
    options: SynthesisOptions,
) -> dict[str, Any]:
    formatted_context = context_to_str(prompt.context)
    gen_opts: dict[str, Any] = {
        "temperature": options.temperature,
        "num_predict": options.max_tokens,
    }
    if options.stop:
        gen_opts["stop"] = options.stop

    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt.system},
            {
                "role": "user",
                "content": f"Context:\n{formatted_context}\n\nTask: {prompt.query}",
            },
        ],
        "stream": False,
        "options": gen_opts,
        "think": options.think,
    }
    if options.keep_alive is not None:
        body["keep_alive"] = options.keep_alive
    if options.structured_output and prompt.json_schema is not None:
        body["format"] = prompt.json_schema
    return body


def _extract_message_content(payload: dict[str, Any]) -> str:
    message = payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
    return ""


def synthesize(
    prompt: StructuredPrompt,
    model: str | None = None,
    options: SynthesisOptions | None = None,
) -> SynthesisResult:
    """Synthesize a shell command via Ollama ``POST /api/chat``."""
    opts = options or SynthesisOptions()
    root = resolve_base_url(opts.base_url)
    if not model:
        raise ProviderConfigurationError("No model configured for Ollama synthesis.")

    body = _build_chat_body(prompt, model, opts)
    chat_url = f"{root}/api/chat"

    try:
        payload = _http_json(chat_url, body, timeout=opts.timeout_seconds)
    except ProviderExecutionError as exc:
        lowered = str(exc).lower()
        retried = False
        if "think" in lowered and "think" in body:
            body.pop("think", None)
            retried = True
        if "format" in lowered and "format" in body:
            body.pop("format", None)
            retried = True
        if not retried:
            raise
        payload = _http_json(chat_url, body, timeout=opts.timeout_seconds)

    raw = _extract_message_content(payload).strip()
    parsed = parse_synthesis_output(raw)
    return SynthesisResult(
        provider="ollama",
        command=parsed.command or "",
        raw_output=raw,
        tool=parsed.tool,
        explanation=parsed.explanation,
        alternatives=parsed.alternatives,
        metadata={"model": model, "base_url": root},
    )


def warm_model(
    model: str,
    keep_alive: str | int | None = "30m",
    base_url: str | None = None,
) -> None:
    """Best-effort warm-up so the first user query avoids cold model load."""
    root = resolve_base_url(base_url)
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
        "options": {"num_predict": 1},
    }
    if keep_alive is not None:
        body["keep_alive"] = keep_alive
    with contextlib.suppress(Exception):
        _http_json(f"{root}/api/chat", body, timeout=15.0)


def embed_texts(
    texts: list[str],
    model: str | None = None,
    *,
    base_url: str | None = None,
    timeout_seconds: float = 30.0,
) -> np.ndarray[Any, Any]:
    """Generate float32 embeddings via Ollama ``POST /api/embed``."""
    if not model:
        raise ProviderConfigurationError("No model configured for Ollama embedding.")
    if not texts:
        return np.empty((0, 0), dtype=np.float32)

    root = resolve_base_url(base_url)
    payload = _http_json(
        f"{root}/api/embed",
        {"model": model, "input": texts},
        timeout=timeout_seconds,
    )
    embeddings = payload.get("embeddings")
    if not isinstance(embeddings, list) or not embeddings:
        raise ProviderExecutionError("Ollama /api/embed returned no embeddings.")
    return np.asarray(embeddings, dtype=np.float32)
