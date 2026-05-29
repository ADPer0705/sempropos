"""Reciprocal Rank Fusion for merged retrieval ranking."""

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
