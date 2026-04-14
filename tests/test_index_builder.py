from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np

from sempropos import config
from sempropos.index import builder, schema


def test_embed_texts_empty_shape_matches_model_dim(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SEMPROPOS_DATA_DIR", str(tmp_path))

    model_name = config.EMBEDDING_FLOOR_MODEL
    vectors = builder._embed_texts(
        [],
        model_name=model_name,
        progress=False,
        provider_name="fastembed_local",
    )

    assert vectors.shape == (0, config.get_embedding_dim(model_name))


def test_atomic_save_npy_overwrites_file_atomically(tmp_path) -> None:
    target = tmp_path / "vectors.npy"
    before = np.array([[1.0, 2.0]], dtype=np.float32)
    after = np.array([[3.0, 4.0]], dtype=np.float32)

    builder._atomic_save_npy(target, before)
    builder._atomic_save_npy(target, after)

    loaded = np.load(target)
    assert loaded.shape == (1, 2)
    assert np.array_equal(loaded, after)


def test_build_index_reuses_existing_meta_model_for_resume(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("SEMPROPOS_DATA_DIR", str(tmp_path))

    config.ensure_data_dirs()
    schema.initialize()

    with schema.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO tools(name, section, description, synopsis)
            VALUES (?, ?, ?, ?)
            """,
            ("ls", 1, "list files", "ls [OPTION]... [FILE]..."),
        )
        conn.commit()

    config.write_index_meta(
        embedding_model=config.EMBEDDING_FLOOR_MODEL,
        embedding_dim=config.get_embedding_dim(config.EMBEDDING_FLOOR_MODEL),
        embedding_provider="fastembed_local",
        synthesis_model=config.synthesis_model_spec("primary")["filename"],
    )

    monkeypatch.setattr(
        builder,
        "load_settings",
        lambda: SimpleNamespace(
            embedding_provider="fastembed_local",
            embedding_model=None,
        ),
    )
    monkeypatch.setattr(builder, "_discover_tools", lambda: [("ls", 1, "list files")])
    monkeypatch.setattr(builder, "_read_man_page", lambda _tool: "")

    captured: dict[str, str] = {}

    def _fake_rebuild_embeddings(_conn, model_name: str, progress: bool, provider_name):
        captured["model_name"] = model_name
        return config.get_embedding_dim(model_name)

    monkeypatch.setattr(builder, "_rebuild_embeddings", _fake_rebuild_embeddings)

    selected_model = builder.build_index(progress=False)

    assert selected_model == config.EMBEDDING_FLOOR_MODEL
    assert captured["model_name"] == config.EMBEDDING_FLOOR_MODEL

    marker = config.last_indexed_path().read_text(encoding="utf-8").strip()
    assert datetime.fromisoformat(marker).tzinfo == timezone.utc

    meta = config.read_index_meta()
    assert meta is not None
    assert meta.get("embedding_provider") == "fastembed_local"


def test_build_index_blocks_resume_when_sempropos_version_mismatches(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("SEMPROPOS_DATA_DIR", str(tmp_path))

    config.ensure_data_dirs()
    schema.initialize()

    with schema.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO tools(name, section, description, synopsis)
            VALUES (?, ?, ?, ?)
            """,
            ("ls", 1, "list files", "ls [OPTION]... [FILE]..."),
        )
        conn.commit()

    config.write_index_meta(
        embedding_model=config.EMBEDDING_FLOOR_MODEL,
        embedding_dim=config.get_embedding_dim(config.EMBEDDING_FLOOR_MODEL),
        embedding_provider="fastembed_local",
        synthesis_model=config.synthesis_model_spec("primary")["filename"],
    )
    meta = config.read_index_meta() or {}
    meta["sempropos_version"] = "9.9.9"
    config.index_meta_path().write_text(json.dumps(meta), encoding="utf-8")

    monkeypatch.setattr(builder, "_discover_tools", lambda: [("ls", 1, "list files")])

    try:
        builder.build_index(progress=False)
    except RuntimeError as exc:
        assert "Run: sempropos --update" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError for sempropos version mismatch")


def test_discover_tools_deduplicates_by_name(monkeypatch) -> None:
    def _fake_run_man_k(section: str):
        if section == "1":
            return [("ls", 1, "list files"), ("grep", 1, "search text")]
        return [("ls", 8, "list files admin"), ("tar", 8, "archive")]

    monkeypatch.setattr(builder, "_run_man_k", _fake_run_man_k)

    discovered = builder._discover_tools()
    names = [name for name, _section, _description in discovered]

    assert names.count("ls") == 1
    assert "grep" in names
    assert "tar" in names
