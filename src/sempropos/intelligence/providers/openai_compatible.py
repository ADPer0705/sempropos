"""OpenAI-compatible synthesis provider."""

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


class OpenAICompatibleProvider(SynthesisProvider):
    """Remote provider using OpenAI-compatible chat completions."""

    def __init__(self, runtime: ProviderRuntimeConfig | None) -> None:
        self._runtime = runtime or ProviderRuntimeConfig(
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
        )

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(name="openai_compatible", local=False)

    def _api_key(self) -> str | None:
        return resolve_provider_api_key(self._runtime, "OPENAI_API_KEY")

    def available(self) -> bool:
        return bool(self._runtime.base_url and self._runtime.model and self._api_key())

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        if not self.available():
            raise ProviderUnavailableError(
                "OpenAI-compatible provider requires base_url, model, and API key"
            )

        headers = {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._runtime.model,
            "messages": [
                {"role": "user", "content": request.prompt},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }

        try:
            response = requests.post(
                f"{self._runtime.base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self._runtime.timeout_seconds or request.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise ProviderExecutionError(str(exc)) from exc

        choices = data.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        raw = str(message.get("content", ""))
        return SynthesisResult(
            provider="openai_compatible",
            command=_extract_command(raw),
            raw_output=raw,
            metadata={"model": self._runtime.model},
        )
