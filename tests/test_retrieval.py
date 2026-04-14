from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from sempropos import config
from sempropos.index import embedder, schema
from sempropos.retrieval import bm25, fusion, semantic


def _seed_mock_index() -> None:
    schema.initialize()
    descriptions = [
        "archive files with compression",
        "find files by size and name",
        "monitor network packets and interfaces",
        "delete files and directories",
        "list directory contents",
        "copy files between paths",
        "move files between paths",
        "print file content",
        "search text in files",
        "stream network connections",
        "process monitoring and statistics",
        "compare files and directories",
        "compress and extract tar archives",
        "download files over http",
        "send files over ssh",
        "encrypt data with gpg",
        "convert image formats",
        "schedule recurring jobs",
        "manage user accounts",
        "inspect disk usage",
    ]

    with schema.get_connection() as conn:
        conn.execute("DELETE FROM examples")
        conn.execute("DELETE FROM flags")
        conn.execute("DELETE FROM tools")
        for idx, desc in enumerate(descriptions, start=1):
            conn.execute(
                """
                INSERT INTO tools(id, name, section, description, synopsis)
                VALUES (?, ?, 1, ?, ?)
                """,
                (idx, f"tool{idx}", desc, f"tool{idx} [opts]"),
            )
        conn.commit()

    model_name = config.EMBEDDING_FLOOR_MODEL
    dim = config.get_embedding_dim(model_name)
    vectors = np.zeros((20, dim), dtype=np.float32)
    vectors[:, 0] = np.linspace(0.1, 0.9, 20)
    vectors[0, 0] = 1.0
    vectors[2, 1] = 1.0

    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vectors = (vectors / norms).astype(np.float32)

    np.save(config.tool_embeddings_path(), vectors)
    config.write_index_meta(
        embedding_model=model_name,
        embedding_dim=dim,
        embedding_provider="fastembed_local",
        synthesis_model=config.synthesis_model_spec("primary")["filename"],
    )


def _fake_embed_texts(texts, *, force_floor=False, model_name=None, provider_name=None):
    dim = config.get_embedding_dim(model_name or config.EMBEDDING_FLOOR_MODEL)
    query = texts[0].lower()
    vec = np.zeros((1, dim), dtype=np.float32)
    if "archive" in query:
        vec[0, 0] = 1.0
    elif "network" in query:
        vec[0, 1] = 1.0
    else:
        vec[0, 0] = 0.5
    return vec


def test_bm25_semantic_and_rrf_rank_expected_tool(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SEMPROPOS_DATA_DIR", str(tmp_path))

    bm25._reset_cache()
    semantic._reset_cache()

    _seed_mock_index()

    monkeypatch.setattr(embedder, "embed_texts", _fake_embed_texts)

    tokens = ["archive", "files", "compress"]
    bm25_results = bm25.search(tokens, top_k=5)
    semantic_results = semantic.search("archive files", top_k=5)
    fused = fusion.reciprocal_rank_fusion([bm25_results, semantic_results])

    assert bm25_results
    assert semantic_results
    assert fused
    assert bm25_results[0][0] == 1
    assert semantic_results[0][0] == 1
    assert fused[0][0] == 1


def test_rrf_combines_lists_stably() -> None:
    merged = fusion.reciprocal_rank_fusion(
        [
            [(1, 0.9), (2, 0.8), (3, 0.7)],
            [(2, 0.95), (1, 0.8), (4, 0.7)],
        ]
    )
    assert merged[0][0] in {1, 2}
    assert {item[0] for item in merged} == {1, 2, 3, 4}


def test_rrf_validates_k_parameter() -> None:
    try:
        fusion.reciprocal_rank_fusion([[(1, 0.9)]], k=0)
    except ValueError as exc:
        assert "greater than zero" in str(exc)
    else:
        raise AssertionError("Expected ValueError for non-positive k")


def test_rrf_tie_breaks_by_tool_id() -> None:
    merged = fusion.reciprocal_rank_fusion(
        [
            [(2, 0.9), (1, 0.8)],
            [(1, 0.95), (2, 0.85)],
        ]
    )
    assert merged[0][0] == 1
    assert merged[1][0] == 2


def test_semantic_search_requires_index_meta(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SEMPROPOS_DATA_DIR", str(tmp_path))

    semantic._reset_cache()
    bm25._reset_cache()
    _seed_mock_index()

    config.index_meta_path().unlink(missing_ok=True)

    try:
        semantic.search("archive files", top_k=3)
    except RuntimeError as exc:
        assert "Run: sempropos --update" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError when index.meta is missing")


def test_semantic_search_passes_provider_name_to_embedder(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("SEMPROPOS_DATA_DIR", str(tmp_path))

    semantic._reset_cache()
    bm25._reset_cache()
    _seed_mock_index()

    monkeypatch.setattr(
        semantic,
        "load_settings",
        lambda: SimpleNamespace(embedding_provider="fastembed_local"),
    )

    captured: dict[str, object] = {}

    def _capture_embed_texts(
        texts,
        *,
        force_floor=False,
        model_name=None,
        provider_name=None,
    ):
        captured["provider_name"] = provider_name
        captured["model_name"] = model_name
        return _fake_embed_texts(
            texts,
            force_floor=force_floor,
            model_name=model_name,
            provider_name=provider_name,
        )

    monkeypatch.setattr(embedder, "embed_texts", _capture_embed_texts)

    result = semantic.search("archive files", top_k=1)
    assert result
    assert captured["provider_name"] == "fastembed_local"


def test_semantic_search_detects_provider_mismatch(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SEMPROPOS_DATA_DIR", str(tmp_path))

    semantic._reset_cache()
    bm25._reset_cache()
    _seed_mock_index()

    monkeypatch.setattr(
        semantic,
        "load_settings",
        lambda: SimpleNamespace(embedding_provider="openai_compatible"),
    )

    try:
        semantic.search("archive files", top_k=3)
    except RuntimeError as exc:
        assert "Run: sempropos --update" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError for embedding provider mismatch")
