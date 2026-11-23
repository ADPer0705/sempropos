"""Retrieval modules for sempropos."""

from sempropos.retrieval.bm25 import build_fts_query, query_bm25_ranking
from sempropos.retrieval.expansion import expand_query, expand_weighted, primary_tokens, tokenize
from sempropos.retrieval.flag_retrieval import (
    get_relevant_examples,
    get_relevant_flags,
    get_relevant_subcommands,
    load_candidates,
)
from sempropos.retrieval.fusion import hybrid_fusion, should_run_semantic
from sempropos.retrieval.query import AnalyzedQuery, analyze_query
from sempropos.retrieval.semantic import (
    is_embeddings_loaded,
    query_semantic_matches,
    reload_embeddings,
)

__all__ = [
    "AnalyzedQuery",
    "analyze_query",
    "tokenize",
    "primary_tokens",
    "expand_weighted",
    "expand_query",
    "build_fts_query",
    "query_bm25_ranking",
    "query_semantic_matches",
    "reload_embeddings",
    "is_embeddings_loaded",
    "hybrid_fusion",
    "should_run_semantic",
    "get_relevant_subcommands",
    "get_relevant_flags",
    "get_relevant_examples",
    "load_candidates",
]
