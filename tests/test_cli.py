from __future__ import annotations

import pytest

from sempropos import cli
from sempropos.index import schema
from sempropos.intelligence.contracts import ProviderUnavailableError


def test_main_rejects_empty_query(capsys) -> None:
    rc = cli.main(["   "])
    captured = capsys.readouterr()

    assert rc == 1
    assert "Query cannot be empty" in captured.err


def test_main_rejects_management_mode_with_query() -> None:
    with pytest.raises(SystemExit):
        cli.main(["--check", "list", "files"])


def test_main_rejects_multiple_management_modes() -> None:
    with pytest.raises(SystemExit):
        cli.main(["--check", "--setup"])


def test_install_mode_handles_build_errors(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli.runtime_assets,
        "ensure_runtime_assets",
        lambda progress=True, use_floor_model=False: {"synthesis_model": "demo.gguf"},
    )
    monkeypatch.setattr(
        cli.builder,
        "build_index",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("index failed")),
    )

    rc = cli.main(["--install"])
    captured = capsys.readouterr()

    assert rc == 1
    assert "install failed" in captured.err.lower()


def test_load_candidates_warns_on_missing_tool(monkeypatch, capsys) -> None:
    schema.initialize()
    with schema.get_connection() as conn:
        conn.execute(
            "INSERT INTO tools(id, name, section, description, synopsis) VALUES (?, ?, ?, ?, ?)",
            (1, "demo", 1, "demo description", "demo [opts]"),
        )
        conn.commit()

    monkeypatch.setattr(
        cli.flag_retrieval, "get_relevant_flags", lambda *args, **kwargs: []
    )

    candidates = cli._load_candidates([1, 999], "demo query")
    captured = capsys.readouterr()

    assert len(candidates) == 1
    assert "warning" in captured.err.lower()


def test_query_mode_warns_when_backend_returns_empty(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.staleness, "is_stale", lambda: False)
    monkeypatch.setattr(cli.expansion, "expand", lambda query: [query])
    monkeypatch.setattr(cli.bm25, "search", lambda tokens, top_k=10: [(1, 1.0)])
    monkeypatch.setattr(cli.semantic, "search", lambda query, top_k=10: [])
    monkeypatch.setattr(
        cli,
        "_load_candidates",
        lambda tool_ids, query: [
            {
                "tool": "demo",
                "description": "demo tool",
                "synopsis": "demo [opts]",
                "flags": [],
                "examples": [],
            }
        ],
    )
    monkeypatch.setattr(cli.backend, "detect", lambda: "ollama")
    monkeypatch.setattr(cli.prompt, "build_prompt", lambda query, candidates: "prompt")
    monkeypatch.setattr(cli.backend, "run", lambda prompt, selected_backend: "")

    rc = cli._query_mode("demo")
    captured = capsys.readouterr()

    assert rc == 0
    assert "returned empty output" in captured.err
    assert "Synthesis backend returned empty output" in captured.out


def test_query_mode_handles_backend_unavailable(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.staleness, "is_stale", lambda: False)
    monkeypatch.setattr(cli.expansion, "expand", lambda query: [query])
    monkeypatch.setattr(cli.bm25, "search", lambda tokens, top_k=10: [(1, 1.0)])
    monkeypatch.setattr(cli.semantic, "search", lambda query, top_k=10: [])
    monkeypatch.setattr(
        cli,
        "_load_candidates",
        lambda tool_ids, query: [
            {
                "tool": "demo",
                "description": "demo tool",
                "synopsis": "demo [opts]",
                "flags": [],
                "examples": [],
            }
        ],
    )

    def _raise_unavailable() -> str:
        raise ProviderUnavailableError("unavailable")

    monkeypatch.setattr(cli.backend, "detect", _raise_unavailable)

    rc = cli._query_mode("demo")
    captured = capsys.readouterr()

    assert rc == 0
    assert "backend unavailable" in captured.err.lower()
    assert "Synthesis backend unavailable" in captured.out
