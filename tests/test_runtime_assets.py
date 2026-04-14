from __future__ import annotations

from types import SimpleNamespace

import pytest

from sempropos import runtime_assets


class _FakeResponse:
    def __init__(self) -> None:
        self.headers = {"content-length": "3"}

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        _ = chunk_size
        yield b"abc"


def test_download_timeout_uses_env_override(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    def _fake_get(url: str, stream: bool, timeout: int):
        captured["url"] = url
        captured["stream"] = stream
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setenv("SEMPROPOS_DOWNLOAD_TIMEOUT", "123")
    monkeypatch.setattr(runtime_assets.requests, "get", _fake_get)

    out = tmp_path / "file.bin"
    runtime_assets._download_to_file("https://example.com/f.bin", out, progress=False)

    assert out.read_bytes() == b"abc"
    assert captured["timeout"] == 123


def test_platform_asset_name_maps_arch_alias(monkeypatch) -> None:
    monkeypatch.setattr(runtime_assets.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runtime_assets.platform, "machine", lambda: "amd64")

    name = runtime_assets._platform_asset_name()
    assert name.endswith("ubuntu-x64.zip")


def test_ensure_runtime_assets_prefers_ollama(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_assets, "_detect_ollama_compatible_model", lambda: "qwen3:0.6b"
    )

    result = runtime_assets.ensure_runtime_assets(progress=False)

    assert result["backend"] == "ollama"
    assert result["synthesis_model"] == "qwen3:0.6b"


def test_ensure_runtime_assets_falls_back_to_tier0(monkeypatch) -> None:
    monkeypatch.setattr(runtime_assets, "_detect_ollama_compatible_model", lambda: None)
    monkeypatch.setattr(
        runtime_assets,
        "ensure_llama_cli",
        lambda progress=True: (_ for _ in ()).throw(RuntimeError("no cli")),
    )
    monkeypatch.setattr(
        runtime_assets,
        "ensure_model",
        lambda tier="primary", progress=True: (_ for _ in ()).throw(
            RuntimeError("no model")
        ),
    )
    monkeypatch.setattr(runtime_assets, "which", lambda _: None)

    result = runtime_assets.ensure_runtime_assets(progress=False)

    assert result["backend"] == "tier0"
    assert result["synthesis_model"] == ""


def test_ensure_model_checks_minimum_free_space(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_assets, "disk_usage", lambda _path: SimpleNamespace(free=1)
    )

    with pytest.raises(RuntimeError):
        runtime_assets.ensure_model(tier="primary", progress=False)
