from __future__ import annotations

from types import SimpleNamespace

import pytest

from sempropos.intelligence import facade, registry
from sempropos.intelligence.config import (
    config_path,
    embedding_provider_from_text,
    load_settings,
    save_settings,
    synthesis_provider_from_text,
    update_provider_config,
    update_settings,
)
from sempropos.intelligence.providers import anthropic, gemini
from sempropos.synthesis import backend


def test_provider_parsing_supports_aliases() -> None:
    assert synthesis_provider_from_text("openai-compatible") == "openai_compatible"
    assert embedding_provider_from_text("fastembed_local") == "fastembed_local"
    assert synthesis_provider_from_text("unknown") is None


def test_update_settings_persists(monkeypatch, tmp_path) -> None:
    cfg = tmp_path / "intelligence.toml"
    monkeypatch.setenv("SEMPROPOS_INTELLIGENCE_CONFIG", str(cfg))

    settings = load_settings()
    save_settings(settings)

    updated = update_settings(
        synthesis_provider="tier0",
        embedding_provider="fastembed_local",
        embedding_model="BAAI/bge-small-en-v1.5",
    )

    reloaded = load_settings()
    assert updated.synthesis_provider == "tier0"
    assert reloaded.embedding_provider == "fastembed_local"
    assert reloaded.embedding_model == "BAAI/bge-small-en-v1.5"
    assert config_path() == cfg


def test_update_provider_config_persists(monkeypatch, tmp_path) -> None:
    cfg = tmp_path / "intelligence.toml"
    monkeypatch.setenv("SEMPROPOS_INTELLIGENCE_CONFIG", str(cfg))

    settings = load_settings()
    save_settings(settings)

    update_provider_config(
        "openai_compatible",
        model="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
    )

    reloaded = load_settings()
    openai_cfg = reloaded.providers["openai_compatible"]
    assert openai_cfg.model == "gpt-4o-mini"
    assert openai_cfg.base_url == "https://api.openai.com/v1"
    assert openai_cfg.api_key_env == "OPENAI_API_KEY"


def test_backend_run_tier0_compatibility() -> None:
    assert backend.run("Task: test\nCommand:", "tier0") == ""


def test_backend_run_rejects_unknown_provider_name() -> None:
    with pytest.raises(ValueError):
        backend.run("Task: test\nCommand:", "unknown")


def test_facade_provider_listing_contains_core_keys() -> None:
    synthesis = facade.list_synthesis_providers()
    embedding = facade.list_embedding_providers()
    assert "tier0" in synthesis
    assert "fastembed_local" in embedding


def test_detect_backend_falls_back_when_preferred_unavailable(monkeypatch) -> None:
    class _FakeProvider:
        def __init__(self, name: str, available: bool) -> None:
            self.info = SimpleNamespace(name=name)
            self._available = available

        def available(self) -> bool:
            return self._available

    def _fake_get_synthesis_provider(settings, *, forced_provider=None):
        selected = forced_provider or settings.synthesis_provider
        if selected == "llama_cpp":
            return _FakeProvider("llama_cpp", False)
        if selected == "ollama":
            return _FakeProvider("ollama", True)
        return _FakeProvider(str(selected), selected == "tier0")

    monkeypatch.setattr(
        registry, "get_synthesis_provider", _fake_get_synthesis_provider
    )

    settings = SimpleNamespace(synthesis_provider="llama_cpp")
    provider = registry.resolve_synthesis_provider(settings)
    assert provider.info.name == "ollama"


def test_gemini_extract_command_normalizes_prefix() -> None:
    raw = "Command: ls -la\nextra"
    assert gemini._extract_command(raw) == "ls -la"


def test_anthropic_extract_command_normalizes_prefix() -> None:
    raw = "command: grep foo file.txt"
    assert anthropic._extract_command(raw) == "grep foo file.txt"


def test_qwen_no_think_applied_for_remote_prompts() -> None:
    gemini_prompt = gemini._build_final_prompt("Task: demo", "qwen3:0.6b")
    anthropic_prompt = anthropic._build_final_prompt("Task: demo", "qwen3:1.7b")

    assert gemini_prompt.endswith("/no_think")
    assert anthropic_prompt.endswith("/no_think")
