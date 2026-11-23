"""Unit tests for query analysis, directional expansion, BM25, detail retrieval, and fusion."""

from __future__ import annotations

from pathlib import Path

from sempropos.index import repository, schema
from sempropos.model import ExampleSpec, FlagSpec, SubcommandSpec, ToolSpec
from sempropos.retrieval import (
    analyze_query,
    expand_weighted,
    hybrid_fusion,
    load_candidates,
    query_bm25_ranking,
    should_run_semantic,
)


def test_query_analyzer_stopwords_extensions_and_intents() -> None:
    q1 = analyze_query("list files in archive.7z")
    assert "in" not in q1.primary_tokens
    assert "7z" in q1.artifact_boosts
    assert q1.artifact_boosts["7z"] >= 1.5

    q2 = analyze_query("delete files older than 30 days")
    assert "than" not in q2.primary_tokens
    assert "find" in q2.intent_tool_boosts

    q3 = analyze_query("show disk usage by folder with --max-depth")
    assert "--max-depth" in q3.flag_tokens
    assert "du" in q3.intent_tool_boosts

    q4 = analyze_query("extract backup.tar.gz to /tmp")
    assert "tar" in q4.artifact_boosts


def test_directional_expansion_avoids_clique_drift() -> None:
    exp = expand_weighted(["port"])
    assert "socket" in exp and exp["socket"] > 0.5
    # Must NOT transitively drag unrelated terms from other groups
    assert "rsync" not in exp
    assert "userdel" not in exp


def test_hybrid_fusion_invariants() -> None:
    bm25 = [(1, 2.0), (2, 1.0), (3, 1.0)]
    semantic = [(99, 0.99), (3, 0.85), (2, 0.40)]

    fused = hybrid_fusion(bm25, semantic)
    ids = [tid for tid, _ in fused]

    # 1. Semantic-only hit (99) NEVER outranks any lexical hit (1, 2, 3).
    assert ids.index(99) > ids.index(2)
    assert ids.index(99) > ids.index(3)
    # 2. Exact lexical tie between 2 and 3 broken by semantic similarity (3 beats 2).
    assert ids[:3] == [1, 3, 2]
    # 3. Order of top lexical items is independent of semantic fetch depth.
    fused_shallow = hybrid_fusion(bm25, semantic[:2])
    assert [tid for tid, _ in fused_shallow[:3]] == [1, 3, 2]


def test_confidence_gated_semantic_decision() -> None:
    q = analyze_query("list files in archive.7z")
    decisive = [(1, 2.8), (2, 0.6)]
    ambiguous = [(1, 0.7), (2, 0.65)]

    assert should_run_semantic(decisive, q, lazy_fallback=True, embeddings_warm=False) is False
    assert should_run_semantic(ambiguous, q, lazy_fallback=True, embeddings_warm=False) is True
    assert should_run_semantic(decisive, q, lazy_fallback=True, embeddings_warm=True) is True
    assert should_run_semantic([], q, lazy_fallback=True, embeddings_warm=False) is True


def test_single_connection_candidate_loading_with_subcommands_and_flags(
    isolated_env: dict[str, Path],
) -> None:
    schema.initialize_database()
    git = ToolSpec(
        name="git",
        summary="Distributed version control system",
        category="vcs",
        subcommands=(
            SubcommandSpec(
                name="reset",
                summary="Undo commits and reset current HEAD",
                examples=(
                    ExampleSpec(
                        command="git reset --soft HEAD~1",
                        description="Undo the last commit keeping changes staged",
                        source="tldr",
                        subcommand="reset",
                    ),
                ),
                source="tldr",
            ),
            SubcommandSpec(
                name="clone",
                summary="Clone a repository into a new directory",
                source="tldr",
            ),
        ),
        flags=(
            FlagSpec(
                short="-C",
                takes_value=True,
                value_hint="PATH",
                description="Run as if started in PATH",
            ),
            FlagSpec(long="--version", description="Print git version"),
        ),
    )

    with schema.get_connection() as conn:
        tid, _ = repository.upsert_tool(conn, git)
        ranked = query_bm25_ranking("undo last git commit", top_k=5, conn=conn)
        assert ranked and ranked[0][0] == tid

        candidates = load_candidates(conn, ranked, "undo last git commit")
        assert len(candidates) == 1
        assert candidates[0].name == "git"
        assert candidates[0].subcommands[0].name == "reset"
        assert any("HEAD~1" in ex.command for ex in candidates[0].examples)
