"""Unit tests for prompt JSON parsing, providers, models catalog, and lazy facade."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from sempropos import config
from sempropos.intelligence import (
    ProviderConfigurationError,
    StructuredPrompt,
    SynthesisOptions,
    build_prompt,
    extract_command,
    models,
    parse_synthesis_output,
    synthesize,
)
from sempropos.intelligence.providers import _local_gguf, _ollama, _openai_compatible, _tier0


class _FakeHTTPResponse(io.BytesIO):
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        super().__init__(json.dumps(payload).encode("utf-8"))
        self.status = status

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def test_parse_synthesis_output_json_and_fenced_bash_bugfix() -> None:
    # Structured JSON response
    parsed = parse_synthesis_output(
        '{"tool": "find", "command": "find . -type f -size +100M", '
        '"explanation": "Find files over 100MB", "alternatives": ["fd -S +100m"], "none": false}'
    )
    assert parsed.command == "find . -type f -size +100M"
    assert parsed.tool == "find"
    assert parsed.explanation == "Find files over 100MB"
    assert parsed.alternatives == ("fd -S +100m",)
    assert parsed.explicit_none is False

    # Explicit JSON none=true
    none_res = parse_synthesis_output(
        '{"tool": "", "command": "", "explanation": "", "none": true}'
    )
    assert none_res.command is None
    assert none_res.explicit_none is True

    # Regression test for v0.3.0 bug: fenced ```bash blocks must NOT be dropped!
    fenced = "```bash\nfind . -type f -size +100M\n```"
    assert extract_command(fenced) == "find . -type f -size +100M"

    # Reject usage synopsis echoes and prose
    assert extract_command("du [OPTION]... [FILE]...") is None
    assert extract_command("Here is the command you can use:") is None
    assert extract_command("$ tar -xzf archive.tar.gz") == "tar -xzf archive.tar.gz"


def test_ollama_pure_http_synthesis_and_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any] | None]] = []

    def fake_urlopen(req: Any, timeout: float = 30.0) -> _FakeHTTPResponse:
        body = json.loads(req.data.decode("utf-8")) if req.data else None
        calls.append((req.full_url, body))
        if req.full_url.endswith("/api/tags"):
            return _FakeHTTPResponse({"models": [{"name": "qwen2.5-coder:1.5b"}]})
        if req.full_url.endswith("/api/chat"):
            return _FakeHTTPResponse(
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "tool": "du",
                                "command": "du -sh *",
                                "explanation": "Show folder sizes",
                                "none": False,
                            }
                        )
                    }
                }
            )
        if req.full_url.endswith("/api/embed"):
            return _FakeHTTPResponse({"embeddings": [[0.1, 0.2, 0.3]]})
        raise AssertionError(f"Unexpected URL: {req.full_url}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    assert _ollama.get_available_models("http://127.0.0.1:11434") == ["qwen2.5-coder:1.5b"]

    prompt = build_prompt(
        "show disk usage by folder",
        [{"tool": "du", "summary": "estimate file space usage", "flags": [], "examples": []}],
    )
    res = _ollama.synthesize(prompt, model="qwen2.5-coder:1.5b", options=SynthesisOptions())
    assert res.command == "du -sh *"
    assert res.tool == "du"
    assert res.explanation == "Show folder sizes"

    vecs = _ollama.embed_texts(["hello"], model="nomic-embed-text")
    assert isinstance(vecs, np.ndarray) and vecs.shape == (1, 3)


def test_openai_compatible_structured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: Any, timeout: float = 30.0) -> _FakeHTTPResponse:
        return _FakeHTTPResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "tool": "ss",
                                    "command": "ss -tulnp",
                                    "explanation": "List listening ports",
                                    "none": False,
                                }
                            )
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    prompt = build_prompt("list open ports", [{"tool": "ss", "summary": "investigate sockets"}])
    res = _openai_compatible.synthesize(
        prompt,
        model="gpt-4o-mini",
        options=SynthesisOptions(base_url="http://localhost:8000/v1", api_key="test"),
    )
    assert res.command == "ss -tulnp"
    assert res.tool == "ss"


def test_local_gguf_model_catalog_and_mocked_driver(
    isolated_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert models.get_model_spec("balanced") is not None
    assert models.get_model_spec("tiny") is not None
    assert models.get_model_spec("quality") is not None

    # Create a fake GGUF file in isolated models dir
    gguf_file = config.models_dir() / "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"
    gguf_file.parent.mkdir(parents=True, exist_ok=True)
    gguf_file.write_bytes(b"GGUF-FAKE")

    assert models.resolve_model_path("balanced") == gguf_file
    assert "qwen2.5-coder-1.5b" in models.installed_model_names()

    monkeypatch.setattr(_local_gguf, "local_runtime_kind", lambda: "llama_cpp")
    monkeypatch.setattr(
        _local_gguf,
        "_synthesize_inprocess",
        lambda prompt, path, opts: json.dumps(
            {
                "tool": "7z",
                "command": "7z l archive.7z",
                "explanation": "List archive contents",
                "none": False,
            }
        ),
    )

    config.save_config(
        config.AppConfig(synthesis=config.SynthesisConfig(provider="local_gguf", model="balanced"))
    )
    prompt = build_prompt("list files in archive.7z", [{"tool": "7z", "summary": "archiver"}])
    res = synthesize(prompt)
    assert res.provider == "local_gguf"
    assert res.command == "7z l archive.7z"

    # Missing model raises configuration error
    with pytest.raises(ProviderConfigurationError):
        _local_gguf.synthesize(prompt, model="nonexistent-model.gguf")


def test_tier0_deterministic_example_fallback() -> None:
    prompt = StructuredPrompt(
        system="",
        query="list files in archive.7z",
        context=[
            {
                "tool": "7z",
                "examples": [
                    {"command": "7z l <archive.7z>", "context": "List files in a 7z archive"}
                ],
            }
        ],
    )
    res = _tier0.synthesize(prompt)
    assert res.provider == "tier0"
    assert res.command == "7z l <archive.7z>"
