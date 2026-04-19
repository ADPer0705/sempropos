"""Ollama synthesis provider."""

from __future__ import annotations

import socket

import requests

from sempropos import config
from sempropos.intelligence.config import ProviderRuntimeConfig
from sempropos.intelligence.contracts import (
    ProviderExecutionError,
    ProviderUnavailableError,
    SynthesisRequest,
    SynthesisResult,
)
from sempropos.intelligence.inference.base import ProviderInfo, SynthesisProvider


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


class OllamaProvider(SynthesisProvider):
    """Provider that uses the local Ollama HTTP API."""

    def __init__(self, runtime: ProviderRuntimeConfig | None) -> None:
        self._runtime = runtime or ProviderRuntimeConfig(
            base_url="http://localhost:11434"
        )

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(name="ollama", local=True)

    def _base_url(self) -> str:
        return self._runtime.base_url or "http://localhost:11434"

    def _host_port(self) -> tuple[str, int]:
        base = self._base_url().replace("http://", "").replace("https://", "")
        host_port = base.split("/", maxsplit=1)[0]
        if ":" not in host_port:
            return host_port, 11434
        host, port_text = host_port.rsplit(":", maxsplit=1)
        try:
            return host, int(port_text)
        except ValueError:
            return host, 11434

    def available(self) -> bool:
        host, port = self._host_port()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.2)
        try:
            sock.connect((host, port))
            return True
        except OSError:
            return False
        finally:
            sock.close()

    def _select_model(self) -> str:
        if self._runtime.model:
            return self._runtime.model

        response = requests.get(f"{self._base_url()}/api/tags", timeout=2.0)
        response.raise_for_status()
        payload = response.json()

        models = [
            item.get("name") for item in payload.get("models", []) if item.get("name")
        ]
        selected = config.select_ollama_model(models)
        if selected:
            return selected

        raise ProviderUnavailableError(
            "Ollama reachable but no compatible model found. "
            "Install one of: gemma4:e2b, qwen3:1.7b, qwen3:0.6b, gemma3:1b"
        )

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        if not self.available():
            raise ProviderUnavailableError("Ollama is not reachable")

        model = self._select_model()
        final_prompt = _build_final_prompt(request.prompt, model)

        try:
            response = requests.post(
                f"{self._base_url()}/api/generate",
                json={
                    "model": model,
                    "prompt": final_prompt,
                    "stream": False,
                    "options": {
                        "temperature": request.temperature,
                        "num_predict": request.max_tokens,
                    },
                },
                timeout=self._runtime.timeout_seconds or request.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise ProviderExecutionError(str(exc)) from exc

        raw = str(payload.get("response", ""))
        return SynthesisResult(
            provider="ollama",
            command=_extract_command(raw),
            raw_output=raw,
            metadata={"model": model},
        )
