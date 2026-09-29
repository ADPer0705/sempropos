"""Flag-level retrieval for candidate tools.

Flags are ranked with the FTS5 BM25 index (scoped to the candidate tool) rather
than a separate embedding matrix. This keeps indexing fast, removes a ~134 MB
in-memory vector file, and still surfaces the flags most relevant to the query.
"""

from __future__ import annotations

from sempropos.index import get_connection
from sempropos.retrieval.bm25 import build_fts_query
from sempropos.retrieval.expansion import expand_query


def _row_to_flag(row) -> dict:
    return {
        "flag": row["flag"],
        "long_flag": row["long_flag"],
        "takes_value": bool(row["takes_value"]),
        "value_hint": row["value_hint"],
        "description": row["description"],
    }


def get_relevant_flags(tool_id: int, query: str, top_k: int = 6) -> list[dict]:
    """Return the flags for *tool_id* most relevant to the natural-language query."""
    if top_k <= 0:
        return []

    fts_query = build_fts_query(expand_query(query))

    with get_connection() as conn:
        rows = []
        if fts_query:
            rows = conn.execute(
                """
                SELECT f.flag, f.long_flag, f.takes_value, f.value_hint, f.description
                FROM flags_fts
                JOIN flags f ON f.id = flags_fts.rowid
                WHERE flags_fts MATCH ? AND f.tool_id = ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, tool_id, top_k),
            ).fetchall()

        # Fall back to the tool's first flags when the query matches nothing.
        if not rows:
            rows = conn.execute(
                """
                SELECT flag, long_flag, takes_value, value_hint, description
                FROM flags
                WHERE tool_id = ?
                ORDER BY id
                LIMIT ?
                """,
                (tool_id, top_k),
            ).fetchall()

    return [_row_to_flag(row) for row in rows]
