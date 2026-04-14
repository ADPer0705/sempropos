from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from sempropos import config
from sempropos.intelligence.config import ProviderRuntimeConfig
from sempropos.intelligence.contracts import (
    EmbeddingRequest,
    ProviderUnavailableError,
    SynthesisRequest,
)
from sempropos.intelligence.embeddings import remote as remote_embeddings
from sempropos.intelligence.providers import (
    anthropic,
    gemini,
    huggingface,
    llama_cpp,
    mistral,
    ollama,
    openai_compatible,
)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


def test_openai_compatible_provider_synthesize(monkeypatch) -> None:
    runtime = ProviderRuntimeConfig(
        base_url="https://api.example.com/v1",
        model="demo-model",
        api_key_plaintext="secret",
    )

    monkeypatch.setattr(
        openai_compatible.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(
            {"choices": [{"message": {"content": "Command: ls -la"}}]}
        ),
    )

    provider = openai_compatible.OpenAICompatibleProvider(runtime)
    result = provider.synthesize(SynthesisRequest(prompt="list files"))

    assert result.command == "ls -la"


def test_mistral_provider_synthesize(monkeypatch) -> None:
    runtime = ProviderRuntimeConfig(
        base_url="https://api.mistral.ai/v1",
        model="mistral-small",
        api_key_plaintext="secret",
    )

    monkeypatch.setattr(
        mistral.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(
            {"choices": [{"message": {"content": "Command: pwd"}}]}
        ),
    )

    provider = mistral.MistralProvider(runtime)
    result = provider.synthesize(SynthesisRequest(prompt="print dir"))

    assert result.command == "pwd"


def test_gemini_provider_synthesize(monkeypatch) -> None:
    runtime = ProviderRuntimeConfig(
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model="qwen3:0.6b",
        api_key_plaintext="secret",
    )

    monkeypatch.setattr(
        gemini.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(
            {"candidates": [{"content": {"parts": [{"text": "Command: echo hi"}]}}]}
        ),
    )

    provider = gemini.GeminiProvider(runtime)
    result = provider.synthesize(SynthesisRequest(prompt="say hi"))

    assert result.command == "echo hi"


def test_anthropic_provider_synthesize(monkeypatch) -> None:
    runtime = ProviderRuntimeConfig(
        base_url="https://api.anthropic.com/v1",
        model="qwen3:1.7b",
        api_key_plaintext="secret",
    )

    monkeypatch.setattr(
        anthropic.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(
            {"content": [{"text": "command: whoami"}]}
        ),
    )

    provider = anthropic.AnthropicProvider(runtime)
    result = provider.synthesize(SynthesisRequest(prompt="current user"))

    assert result.command == "whoami"


def test_huggingface_provider_synthesize(monkeypatch) -> None:
    runtime = ProviderRuntimeConfig(
        base_url="https://api-inference.huggingface.co/models",
        model="demo-model",
        api_key_plaintext="secret",
    )

    monkeypatch.setattr(
        huggingface.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse([{"generated_text": "Command: date"}]),
    )

    provider = huggingface.HuggingFaceProvider(runtime)
    result = provider.synthesize(SynthesisRequest(prompt="show date"))

    assert result.command == "date"


def test_ollama_provider_synthesize(monkeypatch) -> None:
    runtime = ProviderRuntimeConfig(base_url="http://localhost:11434")

    monkeypatch.setattr(ollama.OllamaProvider, "available", lambda self: True)
    monkeypatch.setattr(
        ollama.requests,
        "get",
        lambda *args, **kwargs: _FakeResponse({"models": [{"name": "qwen3:0.6b"}]}),
    )
    monkeypatch.setattr(
        ollama.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse({"response": "Command: ls"}),
    )

    provider = ollama.OllamaProvider(runtime)
    result = provider.synthesize(SynthesisRequest(prompt="list files"))

    assert result.command == "ls"


def test_llama_cpp_provider_synthesize(monkeypatch, tmp_path) -> None:
    model_path = tmp_path / "demo.gguf"
    model_path.write_text("ok", encoding="utf-8")

    monkeypatch.setattr(
        llama_cpp.LlamaCppProvider, "_executable", lambda self: "llama-cli"
    )
    monkeypatch.setattr(config, "resolve_model_path", lambda: model_path)
    monkeypatch.setattr(
        llama_cpp.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="Command: ls\n", stderr=""
        ),
    )

    provider = llama_cpp.LlamaCppProvider(ProviderRuntimeConfig())
    result = provider.synthesize(SynthesisRequest(prompt="list files"))

    assert result.command == "ls"


def test_remote_openai_embedding_provider(monkeypatch) -> None:
    runtime = ProviderRuntimeConfig(
        base_url="https://api.example.com/v1",
        model="demo-embedding",
        api_key_plaintext="secret",
    )

    monkeypatch.setattr(
        remote_embeddings.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(
            {
                "data": [
                    {"embedding": [1.0, 0.0]},
                    {"embedding": [0.0, 1.0]},
                ]
            }
        ),
    )

    provider = remote_embeddings.OpenAICompatibleEmbeddingProvider(runtime)
    matrix = provider.embed(EmbeddingRequest(texts=["a", "b"]))

    assert matrix.shape == (2, 2)
    assert np.allclose(np.linalg.norm(matrix, axis=1), np.array([1.0, 1.0]))


def test_remote_huggingface_embedding_provider(monkeypatch) -> None:
    runtime = ProviderRuntimeConfig(
        base_url="https://api-inference.huggingface.co/models",
        model="demo-model",
        api_key_plaintext="secret",
    )

    monkeypatch.setattr(
        remote_embeddings.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse([1.0, 2.0, 3.0]),
    )

    provider = remote_embeddings.HuggingFaceEmbeddingProvider(runtime)
    matrix = provider.embed(EmbeddingRequest(texts=["a", "b"]))

    assert matrix.shape == (2, 3)


def test_remote_openai_embedding_provider_unavailable_raises() -> None:
    provider = remote_embeddings.OpenAICompatibleEmbeddingProvider(
        ProviderRuntimeConfig(base_url=None, model="demo")
    )

    with pytest.raises(ProviderUnavailableError):
        provider.embed(EmbeddingRequest(texts=["a"]))


def test_llama_cpp_provider_unavailable_without_binary(monkeypatch, tmp_path) -> None:
    model_path = tmp_path / "demo.gguf"
    model_path.write_text("ok", encoding="utf-8")

    monkeypatch.setattr(llama_cpp.LlamaCppProvider, "_executable", lambda self: None)
    monkeypatch.setattr(config, "resolve_model_path", lambda: model_path)

    provider = llama_cpp.LlamaCppProvider(ProviderRuntimeConfig())
    with pytest.raises(ProviderUnavailableError):
        provider.synthesize(SynthesisRequest(prompt="list files"))
