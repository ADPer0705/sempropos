"""Ranking fusion for hybrid retrieval."""

from __future__ import annotations


def reciprocal_rank_fusion(
    results: list[list[tuple[int, float]]],
    k: int = 60,
) -> list[tuple[int, float]]:
    """Combine ranked lists using standard reciprocal rank fusion."""
    if k <= 0:
        raise ValueError("k must be greater than zero")

    scores: dict[int, float] = {}

    for result_list in results:
        for rank, (tool_id, _score) in enumerate(result_list, start=1):
            scores[tool_id] = scores.get(tool_id, 0.0) + (1.0 / (k + rank))

    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def _min_max(results: list[tuple[int, float]]) -> dict[int, float]:
    """Normalize a scored result list to ``[0, 1]`` (best score → 1.0)."""
    if not results:
        return {}

    scores = [score for _tool_id, score in results]
    low = min(scores)
    high = max(scores)
    span = high - low
    if span < 1e-12:
        return {tool_id: 1.0 for tool_id, _score in results}
    return {tool_id: (score - low) / span for tool_id, score in results}


def hybrid_fusion(
    bm25_results: list[tuple[int, float]],
    semantic_results: list[tuple[int, float]],
    bm25_weight: float = 0.7,
    semantic_weight: float = 0.3,
) -> list[tuple[int, float]]:
    """Fuse lexical and semantic scores with explicit weights.

    Rank-based fusion treats both signals as equally trustworthy, which lets a
    noisy embedding score outvote a strong lexical match. Weighting the
    normalized scores keeps lexical precision dominant while still letting the
    embedding break ties and catch synonyms.
    """
    lexical = _min_max(bm25_results)
    semantic = _min_max(semantic_results)

    tool_ids = set(lexical) | set(semantic)
    fused = {
        tool_id: bm25_weight * lexical.get(tool_id, 0.0)
        + semantic_weight * semantic.get(tool_id, 0.0)
        for tool_id in tool_ids
    }

    return sorted(fused.items(), key=lambda item: (-item[1], item[0]))
