"""Anthropic synthesis provider."""

from __future__ import annotations

import requests

from sempropos.intelligence.config import (
    ProviderRuntimeConfig,
    resolve_provider_api_key,
)
from sempropos.intelligence.contracts import (
    ProviderExecutionError,
    ProviderUnavailableError,
    SynthesisRequest,
    SynthesisResult,
)
from sempropos.intelligence.providers.base import ProviderInfo, SynthesisProvider


def _extract_command(text: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip().strip("`")
        if not cleaned:
            continue
        if cleaned.lower().startswith("command:"):
            cleaned = cleaned.split(":", maxsplit=1)[1].strip()
        if cleaned:
            return cleaned
    return text.strip()


def _build_final_prompt(base_prompt: str, model_hint: str) -> str:
    lowered = model_hint.lower()
    if "qwen3" in lowered:
        return base_prompt + "\n/no_think"
    return base_prompt


class AnthropicProvider(SynthesisProvider):
    """Remote provider using Anthropic messages API."""

    def __init__(self, runtime: ProviderRuntimeConfig | None) -> None:
        self._runtime = runtime or ProviderRuntimeConfig(
            base_url="https://api.anthropic.com/v1",
            api_key_env="ANTHROPIC_API_KEY",
            model="claude-3-5-sonnet-latest",
        )

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(name="anthropic", local=False)

    def _api_key(self) -> str | None:
        return resolve_provider_api_key(self._runtime, "ANTHROPIC_API_KEY")

    def available(self) -> bool:
        return bool(self._runtime.base_url and self._runtime.model and self._api_key())

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        if not self.available():
            raise ProviderUnavailableError(
                "Anthropic provider requires base_url, model, and API key"
            )

        final_prompt = _build_final_prompt(request.prompt, str(self._runtime.model))
        payload = {
            "model": self._runtime.model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "messages": [{"role": "user", "content": final_prompt}],
        }
        headers = {
            "x-api-key": str(self._api_key()),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        try:
            response = requests.post(
                f"{self._runtime.base_url.rstrip('/')}/messages",
                headers=headers,
                json=payload,
                timeout=self._runtime.timeout_seconds or request.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise ProviderExecutionError(str(exc)) from exc

        content = data.get("content") or []
        chunks: list[str] = []
        for item in content:
            text = item.get("text") if isinstance(item, dict) else None
            if isinstance(text, str):
                chunks.append(text)
        raw = "\n".join(chunks).strip()
        return SynthesisResult(
            provider="anthropic",
            command=_extract_command(raw),
            raw_output=raw,
            metadata={"model": self._runtime.model},
        )
