"""Ranking fusion for hybrid retrieval.

BM25 is deliberately the primary signal. It is the only ranker that can match a
tool by its exact name, which is what users ask for most of the time ("find",
"ss", "tar"). Embedding similarity is computed over one-line man-page
descriptions, so it is a weak and noisy signal and must not be allowed to
outrank a confident lexical hit.

Recall is the semantic list's job here, not re-ranking: tools it surfaces that
BM25 missed are appended after the lexical ranking. That tail is intentional —
do not "fix" this back into a symmetric score blend. Min-max normalising two
differently shaped score distributions stretches a tiny cosine gap into a full
0-1 range, which lets the embedding outvote an exact-name match.
"""

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


def hybrid_fusion(
    bm25_results: list[tuple[int, float]],
    semantic_results: list[tuple[int, float]],
    *,
    k: int = 60,
    semantic_recall_limit: int | None = None,
) -> list[tuple[int, float]]:
    """Combine lexical and semantic results, keeping BM25 in the driver's seat.

    The lexical order is preserved; the embedding may only break exact score ties
    (where two tools are indistinguishable to BM25, the semantically closer one
    wins) and append recall hits that BM25 missed entirely. The returned scores
    are rank-based and non-increasing, so downstream sorting cannot undo the
    lexical order.

    Args:
        bm25_results: Lexical results as ``(tool_id, score)``, best first.
        semantic_results: Semantic results as ``(tool_id, score)``, best first.
        k: RRF smoothing constant; only shapes the reported scores.
        semantic_recall_limit: Optional cap on appended semantic-only hits.
    """
    if k <= 0:
        raise ValueError("k must be greater than zero")

    semantic_rank = {
        tool_id: rank for rank, (tool_id, _score) in enumerate(semantic_results, start=1)
    }
    missing_rank = len(semantic_rank) + 1

    # Lexical order dominates. The semantic rank only matters when BM25 scores are
    # exactly equal, so an embedding can never leapfrog a stronger lexical match.
    ordered = sorted(
        bm25_results,
        key=lambda item: (-item[1], semantic_rank.get(item[0], missing_rank), item[0]),
    )

    fused = [(tool_id, 1.0 / (k + rank)) for rank, (tool_id, _score) in enumerate(ordered, start=1)]

    seen = {tool_id for tool_id, _score in ordered}
    recall = [(tool_id, score) for tool_id, score in semantic_results if tool_id not in seen]
    if semantic_recall_limit is not None:
        recall = recall[:semantic_recall_limit]

    # Intentional recall tail: semantic-only hits rank below every lexical match.
    fused.extend((tool_id, 0.0) for tool_id, _score in recall)
    return fused
