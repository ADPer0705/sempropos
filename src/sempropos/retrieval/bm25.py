"""Weighted multi-column Porter-stemmed BM25 retrieval over ToolSpec FTS5."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence

from sempropos.index.schema import get_connection
from sempropos.retrieval.expansion import expand_weighted
from sempropos.retrieval.query import AnalyzedQuery, analyze_query

# Column weights for `tools_fts`:
#   (name, aliases_text, summary, tags_text, subcommands_text, examples_text, flags_text)
_BM25_WEIGHTS = "12.0, 10.0, 6.0, 4.0, 4.0, 3.0, 1.5"

_SEARCH_SQL = f"""
    SELECT tools_fts.rowid AS rowid,
           bm25(tools_fts, {_BM25_WEIGHTS}) AS bm25_rank,
           tools.name AS name,
           tools.aliases_text AS aliases_text
    FROM tools_fts
    JOIN tools ON tools.id = tools_fts.rowid
    WHERE tools_fts MATCH ?
    ORDER BY bm25_rank
    LIMIT ?
"""

_NAME_EXACT_BOOST = 1.6
_ALIAS_EXACT_BOOST = 1.4
_AND_MATCH_BONUS = 1.15
_SYNONYM_WEIGHT = 0.35
_RAW_BM25_SATURATION = 8.0


def _sanitize_token(token: str) -> str:
    return re.sub(r"[^\w]+", "", token).strip("_")


def build_fts_query(tokens: Sequence[str], *, operator: str = "OR") -> str:
    """Build a sanitized FTS5 query joining tokens with ``OR`` or ``AND``."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        san = _sanitize_token(token)
        if san and san.lower() not in seen:
            seen.add(san.lower())
            # Quote tokens to prevent FTS5 keyword collisions (e.g. NOT, NEAR, OR, AND).
            cleaned.append(f'"{san}"')
    if not cleaned:
        return ""
    joiner = " AND " if operator.upper() == "AND" else " OR "
    return joiner.join(cleaned)


def _squash_bm25(raw_magnitude: float) -> float:
    """Monotonically map non-negative BM25 magnitude into ``[0, 1)`` without min-max distortion."""
    mag = max(0.0, raw_magnitude)
    return mag / (mag + _RAW_BM25_SATURATION)


def _run_fts(
    conn: sqlite3.Connection,
    fts_expr: str,
    limit: int,
) -> dict[int, tuple[float, str, str]]:
    if not fts_expr:
        return {}
    try:
        rows = conn.execute(_SEARCH_SQL, (fts_expr, limit)).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {
        int(row["rowid"]): (
            _squash_bm25(-float(row["bm25_rank"])),
            str(row["name"]),
            str(row["aliases_text"] or ""),
        )
        for row in rows
    }


def _lookup_named_tools(
    conn: sqlite3.Connection, names: set[str]
) -> dict[int, tuple[str, str]]:
    """Fetch any indexed tool matching an explicit name, alias, artifact, or intent hint."""
    if not names:
        return {}
    lowered = sorted(n.lower() for n in names if n)
    placeholders = ",".join("?" for _ in lowered)
    rows = conn.execute(
        f"""
        SELECT id, name, aliases_text FROM tools
        WHERE lower(name) IN ({placeholders})
        UNION
        SELECT t.id, t.name, t.aliases_text FROM aliases a
        JOIN tools t ON t.id = a.tool_id
        WHERE lower(a.alias) IN ({placeholders})
        """,
        (*lowered, *lowered),
    ).fetchall()
    return {int(r["id"]): (str(r["name"]), str(r["aliases_text"] or "")) for r in rows}


def _compute_tool_score(
    tool_id: int,
    name: str,
    aliases_text: str,
    *,
    or_hits: dict[int, tuple[float, str, str]],
    and_hits: dict[int, tuple[float, str, str]],
    syn_hits: dict[int, tuple[float, str, str]],
    raw_token_set: set[str],
    analyzed: AnalyzedQuery,
) -> float:
    score = 0.0
    if tool_id in or_hits:
        score += or_hits[tool_id][0]
    if tool_id in and_hits:
        score += _AND_MATCH_BONUS + 0.35 * and_hits[tool_id][0]
    if tool_id in syn_hits:
        score += _SYNONYM_WEIGHT * syn_hits[tool_id][0]

    name_lower = name.lower()
    alias_set = {a.lower() for a in aliases_text.split() if a}
    if name_lower in raw_token_set:
        score += _NAME_EXACT_BOOST
    elif alias_set & raw_token_set:
        score += _ALIAS_EXACT_BOOST

    score += analyzed.artifact_boosts.get(name_lower, 0.0)
    score += analyzed.intent_tool_boosts.get(name_lower, 0.0)
    return score


def _score_with_conn(
    conn: sqlite3.Connection,
    analyzed: AnalyzedQuery,
    *,
    top_k: int,
    category: str | None = None,
) -> list[tuple[int, float]]:
    if top_k <= 0:
        return []

    limit = max(top_k * 4, 40)
    primary = analyzed.primary_tokens
    expansions = expand_weighted(primary)
    syn_terms = [term for term, _w in sorted(expansions.items(), key=lambda kv: -kv[1])[:12]]

    or_hits = _run_fts(conn, build_fts_query(primary, operator="OR"), limit)
    distinct_primary = list(dict.fromkeys(t for t in analyzed.raw_tokens if t in set(primary)))
    and_hits = (
        _run_fts(conn, build_fts_query(distinct_primary, operator="AND"), limit)
        if 2 <= len(distinct_primary) <= 5
        else {}
    )
    syn_hits = _run_fts(conn, build_fts_query(syn_terms, operator="OR"), limit)

    hint_names = (
        set(analyzed.raw_tokens)
        | set(analyzed.artifact_boosts)
        | set(analyzed.intent_tool_boosts)
    )
    direct_tools = _lookup_named_tools(conn, hint_names)
    all_ids = set(or_hits) | set(and_hits) | set(syn_hits) | set(direct_tools)
    if not all_ids:
        return []

    allowed_ids: set[int] | None = None
    if category:
        cat_rows = conn.execute(
            "SELECT id FROM tools WHERE lower(category) = lower(?)",
            (category,),
        ).fetchall()
        allowed_ids = {int(r["id"]) for r in cat_rows}

    raw_token_set = {t.lower() for t in analyzed.raw_tokens}
    scores: dict[int, float] = {}
    for tool_id in all_ids:
        if allowed_ids is not None and tool_id not in allowed_ids:
            continue
        meta = or_hits.get(tool_id) or and_hits.get(tool_id) or syn_hits.get(tool_id)
        name, aliases_text = (meta[1], meta[2]) if meta is not None else direct_tools[tool_id]
        score = _compute_tool_score(
            tool_id,
            name,
            aliases_text,
            or_hits=or_hits,
            and_hits=and_hits,
            syn_hits=syn_hits,
            raw_token_set=raw_token_set,
            analyzed=analyzed,
        )
        if score > 0.0:
            scores[tool_id] = score

    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:top_k]


def query_bm25_ranking(
    query_or_tokens: str | Sequence[str],
    top_k: int = 10,
    *,
    conn: sqlite3.Connection | None = None,
    analyzed: AnalyzedQuery | None = None,
    category: str | None = None,
) -> list[tuple[int, float]]:
    """Return top-k lexical BM25+intent matches as ``[(tool_id, score), ...]``."""
    if analyzed is None:
        raw_query = (
            query_or_tokens
            if isinstance(query_or_tokens, str)
            else " ".join(query_or_tokens)
        )
        analyzed = analyze_query(raw_query)

    if conn is not None:
        return _score_with_conn(conn, analyzed, top_k=top_k, category=category)

    with get_connection(read_only=True) as managed_conn:
        return _score_with_conn(managed_conn, analyzed, top_k=top_k, category=category)
