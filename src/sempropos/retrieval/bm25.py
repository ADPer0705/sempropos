"""BM25 retrieval over tool descriptions."""

from __future__ import annotations

import re

from sempropos.index import get_connection


def _sanitize_and_build_query(tokens: list[str]) -> str:
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


# TODO: Consider merging expansion and BM25 Modules. Consider directly passing user query to BM25 and it handles query expansion internally.
def query_bm25_ranking(
    query_tokens: list[str], top_k: int = 10
) -> list[tuple[int, float]]:
    """
    Return top-k BM25 matches for the query token set.

    Args:
        query_tokens(list[str]): A list of token strings representing the user query, which should be preprocessed (e.g., lowercased and tokenized) before being passed to this function.
        top_k(int): The number of top matches to return.

    Returns:
        A list of tuples (tool_id, score) representing the top-k matches. Each tuple contains the tool ID and its corresponding BM25 score, sorted in descending order of relevance.
    """
    if top_k <= 0:
        return []

    # Build the FTS5 query string from the input tokens
    query_string = _sanitize_and_build_query(query_tokens)
    if not query_string:
        return []

    # Execute the BM25 query against the FTS5 index
    sqlquery = """
        SELECT rowid, rank
        FROM tools_fts
        WHERE tools_fts MATCH ?
        ORDER BY rank
        LIMIT ? 
    """

    result = []
    with get_connection() as conn:
        rows = conn.execute(sqlquery, (query_string, top_k)).fetchall()
        for row in rows:
            tool_id = int(row["rowid"])
            score = -float(row["rank"])
            result.append((tool_id, score))

    return result
