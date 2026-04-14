"""Gemini synthesis provider."""

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


class GeminiProvider(SynthesisProvider):
    """Remote provider using Google's Gemini generateContent endpoint."""

    def __init__(self, runtime: ProviderRuntimeConfig | None) -> None:
        self._runtime = runtime or ProviderRuntimeConfig(
            base_url="https://generativelanguage.googleapis.com/v1beta",
            api_key_env="GEMINI_API_KEY",
        )

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(name="gemini", local=False)

    def _api_key(self) -> str | None:
        return resolve_provider_api_key(self._runtime, "GEMINI_API_KEY")

    def available(self) -> bool:
        return bool(self._runtime.model and self._api_key())

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        if not self.available():
            raise ProviderUnavailableError("Gemini provider requires model and API key")

        base = (
            self._runtime.base_url or "https://generativelanguage.googleapis.com/v1beta"
        )
        model = self._runtime.model
        api_key = self._api_key()
        final_prompt = _build_final_prompt(request.prompt, model)

        url = f"{base.rstrip('/')}/models/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [
                {"parts": [{"text": final_prompt}]},
            ],
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
            },
        }

        try:
            response = requests.post(
                url,
                json=payload,
                timeout=self._runtime.timeout_seconds or request.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise ProviderExecutionError(str(exc)) from exc

        candidates = data.get("candidates") or []
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        raw = "\n".join(str(part.get("text", "")) for part in parts).strip()

        return SynthesisResult(
            provider="gemini",
            command=_extract_command(raw),
            raw_output=raw,
            metadata={"model": model},
        )
