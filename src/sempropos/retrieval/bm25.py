"""BM25 retrieval over tool descriptions."""

from __future__ import annotations

import re

from sempropos.index import get_connection

# Weight applied to synonym-derived tokens relative to the user's own words.
_SYNONYM_WEIGHT = 0.3
# Score added (in normalized space) when a query word exactly matches a tool name.
NAME_MATCH_BOOST = 0.5

_SEARCH_SQL = """
    SELECT tools_fts.rowid AS rowid, rank, tools.name AS name
    FROM tools_fts
    JOIN tools ON tools.id = tools_fts.rowid
    WHERE tools_fts MATCH ?
    ORDER BY rank
    LIMIT ?
"""


def build_fts_query(tokens: list[str]) -> str:
    """
    Sanitize query tokens and build an FTS5 query string.

    Args:
        tokens(list[str]): A list of raw query tokens.

    Returns:
        A sanitized FTS5 query string that can be used in a MATCH clause.
    """
    sanitized_tokens = []
    for token in tokens:
        # Remove special characters that could interfere with FTS5 syntax
        sanitized = re.sub(r"[^\w]+", "", token)
        if sanitized:
            sanitized_tokens.append(sanitized)
    # Combine tokens with OR for broader matching in FTS5
    return " OR ".join(sanitized_tokens)


def _raw_scores(conn, query_string: str, limit: int) -> dict[int, tuple[float, str]]:
    """Run one FTS query and return ``{tool_id: (score, name)}``."""
    if not query_string:
        return {}

    rows = conn.execute(_SEARCH_SQL, (query_string, limit)).fetchall()
    return {
        int(row["rowid"]): (-float(row["rank"]), str(row["name"])) for row in rows
    }


def _normalize(raw: dict[int, tuple[float, str]]) -> dict[int, float]:
    """Min-max normalize raw scores to ``[0, 1]`` (best → 1.0)."""
    if not raw:
        return {}
    values = [score for score, _name in raw.values()]
    low = min(values)
    high = max(values)
    span = high - low
    if span < 1e-12:
        return {tool_id: 1.0 for tool_id in raw}
    return {tool_id: (score - low) / span for tool_id, (score, _name) in raw.items()}


def query_bm25_ranking(
    query_tokens: list[str],
    top_k: int = 10,
    name_tokens: list[str] | None = None,
    primary_tokens: list[str] | None = None,
) -> list[tuple[int, float]]:
    """
    Return top-k BM25 matches for the query token set.

    Args:
        query_tokens: Expanded token list (primary words plus synonyms).
        top_k: The number of top matches to return.
        name_tokens: Original query tokens used for the exact-name boost.
        primary_tokens: The user's strong tokens. Synonyms not present here are
            down-weighted so they cannot dominate the ranking. When omitted,
            all tokens are treated as primary.

    Returns:
        A list of tuples (tool_id, score), sorted by descending relevance.
    """
    if top_k <= 0:
        return []

    primary = list(primary_tokens) if primary_tokens else list(query_tokens)
    primary_set = {token.lower() for token in primary}
    synonym_only = [token for token in query_tokens if token.lower() not in primary_set]

    name_token_set = {token.lower() for token in (name_tokens or primary)}
    limit = max(top_k * 3, top_k)

    with get_connection() as conn:
        primary_raw = _raw_scores(conn, build_fts_query(primary), limit)
        synonym_raw = _raw_scores(conn, build_fts_query(synonym_only), limit)

    primary_norm = _normalize(primary_raw)
    synonym_norm = _normalize(synonym_raw)

    merged: dict[int, float] = {}
    for tool_id in set(primary_norm) | set(synonym_norm):
        score = primary_norm.get(tool_id, 0.0) + _SYNONYM_WEIGHT * synonym_norm.get(tool_id, 0.0)
        name = (primary_raw.get(tool_id) or synonym_raw.get(tool_id))[1]
        if name.lower() in name_token_set:
            score += NAME_MATCH_BOOST
        merged[tool_id] = score

    return sorted(merged.items(), key=lambda item: (-item[1], item[0]))[:top_k]
