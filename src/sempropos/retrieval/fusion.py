"""Confidence-gated BM25-primary hybrid ranking fusion.

BM25 over structured multi-field ToolSpecs is the primary signal. Semantic
vector similarity breaks lexical ties and provides conceptual recall when
lexical matches are weak or absent. When running cold (no warm daemon),
high-confidence lexical hits skip loading the ONNX embedding model entirely
to keep single-shot CLI queries instant.
"""

from __future__ import annotations

from sempropos.retrieval.query import AnalyzedQuery

# Thresholds for skipping cold embedding load when lexical ranking is already decisive.
_HIGH_CONFIDENCE_SCORE = 1.65
_HIGH_CONFIDENCE_MARGIN = 0.45


def should_run_semantic(
    bm25_results: list[tuple[int, float]],
    analyzed: AnalyzedQuery,
    *,
    lazy_fallback: bool = True,
    embeddings_warm: bool = False,
) -> bool:
    """Decide whether to invoke semantic retrieval for ``analyzed``.

    Always runs when vectors + model are already warm in memory (daemon mode) or
    when ``lazy_fallback`` is disabled. Otherwise skips cold ONNX startup when
    BM25 already found a decisive top match.
    """
    if embeddings_warm or not lazy_fallback:
        return True
    if not bm25_results:
        return True

    top_score = bm25_results[0][1]
    second_score = bm25_results[1][1] if len(bm25_results) > 1 else 0.0
    margin = top_score - second_score

    if analyzed.artifact_boosts and top_score >= 1.5:
        return False
    return not (top_score >= _HIGH_CONFIDENCE_SCORE and margin >= _HIGH_CONFIDENCE_MARGIN)


def hybrid_fusion(
    bm25_results: list[tuple[int, float]],
    semantic_results: list[tuple[int, float]],
    *,
    k: int = 60,
    semantic_recall_limit: int | None = None,
) -> list[tuple[int, float]]:
    """Combine lexical and semantic results while keeping BM25 primary.

    Lexical ordering dominates: semantic similarity breaks exact BM25 ties and
    appends conceptual recall hits that BM25 missed entirely. Returned scores
    are strictly non-increasing rank-based values.
    """
    if k <= 0:
        raise ValueError("k must be greater than zero")

    semantic_rank = {
        tool_id: rank for rank, (tool_id, _score) in enumerate(semantic_results, start=1)
    }
    missing_rank = len(semantic_rank) + 1

    ordered = sorted(
        bm25_results,
        key=lambda item: (-item[1], semantic_rank.get(item[0], missing_rank), item[0]),
    )

    fused = [(tool_id, 1.0 / (k + rank)) for rank, (tool_id, _score) in enumerate(ordered, start=1)]

    seen = {tool_id for tool_id, _score in ordered}
    recall = [(tool_id, score) for tool_id, score in semantic_results if tool_id not in seen]
    if semantic_recall_limit is not None:
        recall = recall[:semantic_recall_limit]

    # Semantic-only recall tail always ranks strictly below confident lexical hits.
    fused.extend((tool_id, 0.0) for tool_id, _score in recall)
    return fused
