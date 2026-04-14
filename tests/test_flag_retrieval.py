from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from sempropos import config
from sempropos.index import embedder, schema
from sempropos.retrieval import flag_retrieval


def _seed_flags(with_embeddings: bool = True) -> None:
    schema.initialize()

    with schema.get_connection() as conn:
        conn.execute("DELETE FROM examples")
        conn.execute("DELETE FROM flags")
        conn.execute("DELETE FROM tools")
        conn.execute(
            "INSERT INTO tools(id, name, section, description, synopsis) VALUES (?, ?, ?, ?, ?)",
            (1, "demo", 1, "demo tool", "demo [opts]"),
        )
        conn.execute(
            "INSERT INTO flags(id, tool_id, flag, long_flag, takes_value, value_hint, description) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1, 1, "-a", "--all", 0, None, "show all"),
        )
        conn.execute(
            "INSERT INTO flags(id, tool_id, flag, long_flag, takes_value, value_hint, description) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (2, 1, "-n", "--network", 0, None, "network mode"),
        )
        conn.execute(
            "INSERT INTO flags(id, tool_id, flag, long_flag, takes_value, value_hint, description) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (3, 1, "-q", "--quiet", 0, None, "quiet mode"),
        )
        conn.commit()

    model = config.EMBEDDING_FLOOR_MODEL
    dim = config.get_embedding_dim(model)
    config.write_index_meta(
        embedding_model=model,
        embedding_dim=dim,
        embedding_provider="fastembed_local",
        synthesis_model=config.synthesis_model_spec("primary")["filename"],
    )

    if with_embeddings:
        vectors = np.zeros((3, dim), dtype=np.float32)
        vectors[0, 0] = 1.0
        vectors[1, 1] = 1.0
        vectors[2, 2] = 1.0
        np.save(config.flag_embeddings_path(), vectors)
        np.save(config.flag_embedding_ids_path(), np.array([1, 2, 3], dtype=np.int32))


def test_get_relevant_flags_orders_by_similarity(monkeypatch) -> None:
    _seed_flags(with_embeddings=True)

    monkeypatch.setattr(
        flag_retrieval,
        "load_settings",
        lambda: SimpleNamespace(embedding_provider="fastembed_local"),
    )

    def _fake_embed_texts(
        texts, *, force_floor=False, model_name=None, provider_name=None
    ):
        _ = texts, force_floor, provider_name
        dim = config.get_embedding_dim(model_name or config.EMBEDDING_FLOOR_MODEL)
        vec = np.zeros((1, dim), dtype=np.float32)
        vec[0, 1] = 1.0
        return vec

    monkeypatch.setattr(embedder, "embed_texts", _fake_embed_texts)

    results = flag_retrieval.get_relevant_flags(1, "network", top_k=2)

    assert results
    assert results[0]["flag"] == "-n"


def test_get_relevant_flags_returns_rows_when_embeddings_absent(monkeypatch) -> None:
    _seed_flags(with_embeddings=False)

    monkeypatch.setattr(
        flag_retrieval,
        "load_settings",
        lambda: SimpleNamespace(embedding_provider="fastembed_local"),
    )

    results = flag_retrieval.get_relevant_flags(1, "anything", top_k=2)

    assert len(results) == 2
    assert results[0]["flag"] == "-a"


def test_get_relevant_flags_rejects_provider_mismatch(monkeypatch) -> None:
    _seed_flags(with_embeddings=True)

    monkeypatch.setattr(
        flag_retrieval,
        "load_settings",
        lambda: SimpleNamespace(embedding_provider="openai_compatible"),
    )

    with pytest.raises(RuntimeError):
        flag_retrieval.get_relevant_flags(1, "network", top_k=2)


def test_get_relevant_flags_rejects_non_positive_top_k() -> None:
    assert flag_retrieval.get_relevant_flags(999, "q", top_k=0) == []
