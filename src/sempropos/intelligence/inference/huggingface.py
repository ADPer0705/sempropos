"""Hugging Face inference synthesis provider."""

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
from sempropos.intelligence.inference.base import ProviderInfo, SynthesisProvider


def _extract_text(payload: object) -> str:
    if isinstance(payload, list):
        first = payload[0] if payload else ""
        if isinstance(first, dict):
            generated = first.get("generated_text")
            if isinstance(generated, str):
                return generated
        if isinstance(first, str):
            return first
    if isinstance(payload, dict):
        generated = payload.get("generated_text")
        if isinstance(generated, str):
            return generated
    return str(payload)


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


class HuggingFaceProvider(SynthesisProvider):
    """Remote provider using Hugging Face text generation inference API."""

    def __init__(self, runtime: ProviderRuntimeConfig | None) -> None:
        self._runtime = runtime or ProviderRuntimeConfig(
            base_url="https://api-inference.huggingface.co/models",
            api_key_env="HF_TOKEN",
        )

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(name="huggingface", local=False)

    def _api_key(self) -> str | None:
        return resolve_provider_api_key(self._runtime, "HF_TOKEN")

    def _endpoint(self) -> str | None:
        if not self._runtime.base_url or not self._runtime.model:
            return None
        return f"{self._runtime.base_url.rstrip('/')}/{self._runtime.model}"

    def available(self) -> bool:
        return bool(self._endpoint() and self._api_key())

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        endpoint = self._endpoint()
        if not endpoint or not self._api_key():
            raise ProviderUnavailableError(
                "Hugging Face provider requires base_url, model, and API key"
            )

        headers = {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
        }
        payload = {
            "inputs": request.prompt,
            "parameters": {
                "temperature": request.temperature,
                "max_new_tokens": request.max_tokens,
                "return_full_text": False,
            },
            "options": {"wait_for_model": True},
        }

        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=self._runtime.timeout_seconds or request.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise ProviderExecutionError(str(exc)) from exc

        raw = _extract_text(data).strip()
        return SynthesisResult(
            provider="huggingface",
            command=_extract_command(raw),
            raw_output=raw,
            metadata={"model": self._runtime.model},
        )
