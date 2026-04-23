"""Flag-level retrieval for candidate tools."""

from __future__ import annotations

import numpy as np

from sempropos import config
from sempropos.index import schema
from sempropos.intelligence import facade

_FLAG_EMBEDDINGS: np.ndarray | None = None
_FLAG_IDS: np.ndarray | None = None
_FLAG_INDEX: dict[int, int] | None = None


def _ensure_embeddings() -> None:
    """Load flag embeddings and id mappings into in-process caches."""
    global _FLAG_EMBEDDINGS, _FLAG_IDS, _FLAG_INDEX
    if (
        _FLAG_EMBEDDINGS is not None
        and _FLAG_IDS is not None
        and _FLAG_INDEX is not None
    ):
        return

    emb_path = config.flag_embeddings_path()
    ids_path = config.flag_embedding_ids_path()

    if not emb_path.exists() or not ids_path.exists():
        _FLAG_EMBEDDINGS = np.empty((0, 0), dtype=np.float32)
        _FLAG_IDS = np.empty((0,), dtype=np.int32)
        _FLAG_INDEX = {}
        return

    _FLAG_EMBEDDINGS = np.load(emb_path).astype(np.float32)
    if _FLAG_EMBEDDINGS.ndim != 2:
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE)

    _FLAG_IDS = np.load(ids_path).astype(np.int32)
    assert _FLAG_IDS is not None
    if len(_FLAG_IDS) != len(_FLAG_EMBEDDINGS):
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE)

    with schema.get_connection() as conn:
        db_rows = conn.execute("SELECT id FROM flags ORDER BY id").fetchall()
    db_ids = np.array([int(row["id"]) for row in db_rows], dtype=np.int32)
    if len(db_ids) != len(_FLAG_IDS) or not np.array_equal(db_ids, _FLAG_IDS):
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE)

    _FLAG_INDEX = {int(flag_id): idx for idx, flag_id in enumerate(_FLAG_IDS.tolist())}


def get_relevant_flags(tool_id: int, query: str, top_k: int = 6) -> list[dict]:
    """Return semantically relevant flags for a specific tool."""
    if top_k <= 0:
        return []

    with schema.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, flag, long_flag, takes_value, value_hint, description
            FROM flags
            WHERE tool_id = ?
            ORDER BY id
            """,
            (tool_id,),
        ).fetchall()

    if not rows:
        return []

    _ensure_embeddings()
    assert _FLAG_EMBEDDINGS is not None
    assert _FLAG_INDEX is not None

    indices: list[int] = []
    mapped_rows: list = []
    for row in rows:
        idx = _FLAG_INDEX.get(int(row["id"]))
        if idx is None:
            continue
        indices.append(idx)
        mapped_rows.append(row)

    if not indices:
        return [
            {
                "flag": row["flag"],
                "long_flag": row["long_flag"],
                "takes_value": bool(row["takes_value"]),
                "value_hint": row["value_hint"],
                "description": row["description"],
            }
            for row in rows[:top_k]
        ]

    query_vector = facade.embed_texts([query])[0].astype(np.float32)

    tool_vectors = _FLAG_EMBEDDINGS[np.array(indices)]
    scores = tool_vectors @ query_vector
    ranked = np.argsort(-scores)[:top_k]

    output: list[dict] = []
    for idx in ranked:
        row = mapped_rows[int(idx)]
        output.append(
            {
                "flag": row["flag"],
                "long_flag": row["long_flag"],
                "takes_value": bool(row["takes_value"]),
                "value_hint": row["value_hint"],
                "description": row["description"],
            }
        )

    return output


def _reset_cache() -> None:
    """Reset flag retrieval caches for tests or reinitialization."""
    global _FLAG_EMBEDDINGS, _FLAG_IDS, _FLAG_INDEX
    _FLAG_EMBEDDINGS = None
    _FLAG_IDS = None
    _FLAG_INDEX = None
