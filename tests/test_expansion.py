from __future__ import annotations

from sempropos.retrieval import expansion


def test_expand_adds_synonyms_and_preserves_order() -> None:
    tokens = expansion.expand("find files")

    assert tokens[:2] == ["find", "search"]
    assert "files" in tokens
    assert "contents" in tokens


def test_expand_deduplicates_terms() -> None:
    tokens = expansion.expand("find find search")

    assert tokens.count("find") == 1
    assert tokens.count("search") == 1


def test_expand_empty_query_returns_empty_list() -> None:
    assert expansion.expand("") == []
