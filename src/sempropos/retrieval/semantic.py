"""Embedding-based semantic retrieval with automatic reload on index updates."""

from __future__ import annotations

import sqlite3
from typing import Any

import numpy as np

from sempropos import config
from sempropos.index.schema import get_connection
from sempropos.intelligence.facade import embed_texts

_TOOL_IDS: list[int] | None = None
_TOOL_EMBEDDINGS: np.ndarray[Any, Any] | None = None
_LOADED_META_MTIME: float | None = None
_LOADED_DATA_DIR: str | None = None


def reload_embeddings() -> None:
    """Drop cached mmap embeddings so next query reloads from disk."""
    global _TOOL_IDS, _TOOL_EMBEDDINGS, _LOADED_META_MTIME, _LOADED_DATA_DIR
    _TOOL_IDS = None
    _TOOL_EMBEDDINGS = None
    _LOADED_META_MTIME = None
    _LOADED_DATA_DIR = None


def is_embeddings_loaded() -> bool:
    """Return True when tool vectors are already mapped in memory."""
    return _TOOL_IDS is not None and _TOOL_EMBEDDINGS is not None


def _current_meta_mtime() -> float | None:
    meta_path = config.index_meta_path()
    try:
        return meta_path.stat().st_mtime if meta_path.exists() else None
    except OSError:
        return None


def _db_tool_ids(conn: sqlite3.Connection | None = None) -> list[int]:
    if conn is not None:
        rows = conn.execute("SELECT id FROM tools ORDER BY id").fetchall()
        return [int(row["id"]) for row in rows]
    with get_connection(read_only=True) as managed:
        rows = managed.execute("SELECT id FROM tools ORDER BY id").fetchall()
    return [int(row["id"]) for row in rows]


def _load_embeddings(
    conn: sqlite3.Connection | None = None,
) -> tuple[list[int], np.ndarray[Any, Any]]:
    """Load and cache tool ids + memory-mapped embedding matrix, auto-reloading on update."""
    global _TOOL_IDS, _TOOL_EMBEDDINGS, _LOADED_META_MTIME, _LOADED_DATA_DIR

    current_dir = str(config.data_dir())
    current_mtime = _current_meta_mtime()

    if (
        _TOOL_IDS is not None
        and _TOOL_EMBEDDINGS is not None
        and current_dir == _LOADED_DATA_DIR
        and current_mtime == _LOADED_META_MTIME
    ):
        return _TOOL_IDS, _TOOL_EMBEDDINGS

    emb_path = config.tool_embeddings_path()
    ids_path = config.tool_embedding_ids_path()
    if not emb_path.exists() or not ids_path.exists():
        _TOOL_IDS = []
        _TOOL_EMBEDDINGS = np.empty((0, 0), dtype=np.float32)
        _LOADED_META_MTIME = current_mtime
        _LOADED_DATA_DIR = current_dir
        return _TOOL_IDS, _TOOL_EMBEDDINGS

    embeddings = np.load(emb_path, mmap_mode="r")
    if embeddings.ndim != 2 or embeddings.dtype != np.float32:
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE)

    ids = [int(value) for value in np.load(ids_path).tolist()]
    if len(ids) != embeddings.shape[0]:
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE)

    if ids != _db_tool_ids(conn):
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE)

    _TOOL_IDS = ids
    _TOOL_EMBEDDINGS = embeddings
    _LOADED_META_MTIME = current_mtime
    _LOADED_DATA_DIR = current_dir
    return _TOOL_IDS, _TOOL_EMBEDDINGS


def query_semantic_matches(
    query: str,
    top_k: int = 10,
    *,
    conn: sqlite3.Connection | None = None,
) -> list[tuple[int, float]]:
    """Return top-k semantic matches based on cosine similarity."""
    if top_k <= 0:
        return []

    settings = config.load_config()
    if settings.embedding.provider == "none":
        return []

    tool_ids, embeddings = _load_embeddings(conn=conn)
    if not tool_ids or embeddings.size == 0:
        return []

    vector = embed_texts([query], kind="query")[0].astype(np.float32)
    if vector.shape[0] != embeddings.shape[1]:
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE)

    norm_embeddings = np.linalg.norm(embeddings, axis=1)
    norm_embeddings = np.where(norm_embeddings == 0, 1e-10, norm_embeddings)
    norm_vector = float(np.linalg.norm(vector)) or 1e-10

    scores = (embeddings @ vector) / (norm_embeddings * norm_vector)
    k = min(top_k, len(tool_ids))
    ranked_indices = np.argsort(-scores)[:k]
    return [(tool_ids[int(idx)], float(scores[int(idx)])) for idx in ranked_indices]
