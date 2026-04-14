"""Remote embedding provider implementations."""

from __future__ import annotations

import numpy as np
import requests

from sempropos.intelligence.config import (
    ProviderRuntimeConfig,
    resolve_provider_api_key,
)
from sempropos.intelligence.contracts import (
    EmbeddingProviderName,
    EmbeddingRequest,
    ProviderExecutionError,
    ProviderUnavailableError,
)
from sempropos.intelligence.providers.base import EmbeddingProvider


def _normalize(matrix: np.ndarray) -> np.ndarray:
    if matrix.size == 0:
        return matrix.astype(np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """Embedding provider using OpenAI-compatible /embeddings endpoint."""

    def __init__(self, runtime: ProviderRuntimeConfig | None) -> None:
        self._runtime = runtime or ProviderRuntimeConfig(
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
        )

    @property
    def name(self) -> EmbeddingProviderName:
        return "openai_compatible"

    def _api_key(self) -> str | None:
        return resolve_provider_api_key(self._runtime, "OPENAI_API_KEY")

    def available(self) -> bool:
        return bool(self._runtime.base_url and self._api_key())

    def embed(self, request: EmbeddingRequest) -> np.ndarray:
        if not self.available():
            raise ProviderUnavailableError(
                "OpenAI-compatible embedding provider requires base_url and API key"
            )

        model = request.model_name or self._runtime.model
        if not model:
            raise ProviderUnavailableError(
                "OpenAI-compatible embedding provider requires a model name"
            )
        if not request.texts:
            return np.empty((0, 0), dtype=np.float32)

        payload = {
            "model": model,
            "input": request.texts,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(
                f"{self._runtime.base_url.rstrip('/')}/embeddings",
                json=payload,
                headers=headers,
                timeout=self._runtime.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise ProviderExecutionError(str(exc)) from exc

        entries = data.get("data") or []
        rows: list[np.ndarray] = []
        for item in entries:
            embedding = item.get("embedding") if isinstance(item, dict) else None
            if isinstance(embedding, list):
                rows.append(np.asarray(embedding, dtype=np.float32))
        if not rows:
            raise ProviderExecutionError("Embedding API returned no vectors")

        matrix = np.vstack(rows)
        return _normalize(matrix)


class HuggingFaceEmbeddingProvider(EmbeddingProvider):
    """Embedding provider using Hugging Face feature extraction API."""

    def __init__(self, runtime: ProviderRuntimeConfig | None) -> None:
        self._runtime = runtime or ProviderRuntimeConfig(
            base_url="https://api-inference.huggingface.co/models",
            api_key_env="HF_TOKEN",
        )

    @property
    def name(self) -> EmbeddingProviderName:
        return "huggingface"

    def _api_key(self) -> str | None:
        return resolve_provider_api_key(self._runtime, "HF_TOKEN")

    def available(self) -> bool:
        return bool(self._runtime.base_url and self._runtime.model and self._api_key())

    def embed(self, request: EmbeddingRequest) -> np.ndarray:
        model = request.model_name or self._runtime.model
        if not self._runtime.base_url or not model or not self._api_key():
            raise ProviderUnavailableError(
                "Hugging Face embedding provider requires base_url, model, and API key"
            )
        if not request.texts:
            return np.empty((0, 0), dtype=np.float32)

        endpoint = f"{self._runtime.base_url.rstrip('/')}/{model}"
        headers = {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
        }

        rows: list[np.ndarray] = []
        for text in request.texts:
            payload = {
                "inputs": text,
                "options": {"wait_for_model": True},
            }
            try:
                response = requests.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                    timeout=self._runtime.timeout_seconds,
                )
                response.raise_for_status()
                data = response.json()
            except requests.RequestException as exc:
                raise ProviderExecutionError(str(exc)) from exc

            if isinstance(data, list) and data and isinstance(data[0], (int, float)):
                rows.append(np.asarray(data, dtype=np.float32))
                continue
            if isinstance(data, list) and data and isinstance(data[0], list):
                # Some models return token-level embeddings. Use pooled mean.
                token_matrix = np.asarray(data, dtype=np.float32)
                rows.append(token_matrix.mean(axis=0))
                continue
            raise ProviderExecutionError("Unexpected Hugging Face embedding response")

        matrix = np.vstack(rows)
        return _normalize(matrix)
