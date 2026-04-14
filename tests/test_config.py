from __future__ import annotations

from pathlib import Path

from sempropos import config


def test_write_index_meta_is_atomic_and_valid_json() -> None:
    config.write_index_meta(
        embedding_model=config.EMBEDDING_FLOOR_MODEL,
        embedding_dim=config.get_embedding_dim(config.EMBEDDING_FLOOR_MODEL),
        embedding_provider="fastembed_local",
        synthesis_model=config.synthesis_model_spec("primary")["filename"],
    )

    meta = config.read_index_meta()
    assert meta is not None
    assert meta.get("embedding_model") == config.EMBEDDING_FLOOR_MODEL
    assert not config.index_meta_path().with_suffix(".tmp").exists()


def test_ensure_data_dirs_checks_writability(monkeypatch) -> None:
    seen: list[Path] = []

    def _fake_assert(path: Path) -> None:
        seen.append(path)

    monkeypatch.setattr(config, "_assert_directory_writable", _fake_assert)

    config.ensure_data_dirs()

    assert config.data_dir() in seen
    assert config.models_dir() in seen
    assert config.bin_dir() in seen


def test_read_index_meta_invalid_json_returns_none() -> None:
    config.ensure_data_dirs()
    config.index_meta_path().write_text("{invalid json", encoding="utf-8")

    assert config.read_index_meta() is None


def test_select_ollama_model_uses_preferred_order() -> None:
    selected = config.select_ollama_model(["qwen3:0.6b", "gemma3:1b"])
    assert selected == "qwen3:0.6b"
