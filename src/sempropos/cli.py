"""Command-line interface for sempropos."""

from __future__ import annotations

import argparse
import sys

from sempropos.index import staleness
from sempropos import runtime_assets
from sempropos.retrieval import semantic
from sempropos.synthesis import prompt
from sempropos.index import builder, schema
from sempropos.retrieval import bm25, expansion, flag_retrieval, fusion
from sempropos.synthesis import backend


def _load_candidates(tool_ids: list[int], query: str) -> list[dict]:
    """Load top tool candidates with relevant flags and examples."""
    candidates: list[dict] = []
    with schema.get_connection() as conn:
        for tool_id in tool_ids:
            row = conn.execute(
                """
                SELECT id, name, synopsis, description
                FROM tools
                WHERE id = ?
                """,
                (tool_id,),
            ).fetchone()
            if row is None:
                continue

            examples = conn.execute(
                """
                SELECT command, context
                FROM examples
                WHERE tool_id = ?
                ORDER BY id
                """,
                (tool_id,),
            ).fetchall()

            candidates.append(
                {
                    "tool_id": int(row["id"]),
                    "tool": row["name"],
                    "synopsis": row["synopsis"] or "",
                    "description": row["description"],
                    "flags": flag_retrieval.get_relevant_flags(int(row["id"]), query, top_k=6),
                    "examples": [
                        {
                            "command": item["command"],
                            "context": item["context"],
                        }
                        for item in examples
                    ],
                }
            )

    return candidates


def _format_key_flags(flags: list[dict], max_items: int = 3) -> str:
    """Render a compact, human-readable summary of important flags."""
    if not flags:
        return "none"

    items: list[str] = []
    for flag in flags[:max_items]:
        label = flag.get("flag") or "*"
        description = (flag.get("description") or "").strip()
        if description:
            items.append(f"{label} ({description})")
        else:
            items.append(label)
    return ", ".join(items)


def _print_tier0(candidates: list[dict]) -> None:
    """Print structured fallback output when no local LLM is available."""
    print("No local LLM detected. Showing matched tools:\n")

    if not candidates:
        print("No tool matches found in the local index.")
        print("\nInstall llama.cpp or start Ollama for command synthesis.")
        return

    for candidate in candidates:
        print(f"[{candidate['tool']}] - {candidate['description']}")

        if candidate.get("synopsis"):
            print(f"  Synopsis: {candidate['synopsis']}")

        print(f"  Key flags: {_format_key_flags(candidate.get('flags', []))}")

        examples = candidate.get("examples") or []
        if examples:
            print("  Examples:")
            for example in examples[:3]:
                print(f"    {example['command']}")

        print()

    print("Install llama.cpp or start Ollama for command synthesis.")


def _query_mode(query: str) -> int:
    """Execute the query pipeline from retrieval through synthesis fallback."""
    schema.initialize()

    if staleness.is_stale():
        print(
            "[sempropos] Package database has changed. Run `sempropos --update` "
            "to refresh index."
        )

    tokens = expansion.expand(query)
    bm25_results = bm25.search(tokens, top_k=10)
    semantic_results = semantic.search(query, top_k=10)
    fused = fusion.reciprocal_rank_fusion([bm25_results, semantic_results], k=60)

    ranked_ids = [tool_id for tool_id, _score in fused[:3]]
    if not ranked_ids:
        ranked_ids = [tool_id for tool_id, _ in bm25_results[:3]]
    if not ranked_ids:
        ranked_ids = [tool_id for tool_id, _ in semantic_results[:3]]

    candidates = _load_candidates(ranked_ids, query)
    if not candidates:
        _print_tier0(candidates)
        return 0

    selected_backend = backend.detect()

    if selected_backend == "tier0":
        _print_tier0(candidates)
        return 0

    built_prompt = prompt.build_prompt(query, candidates)
    try:
        response = backend.run(built_prompt, selected_backend).strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[sempropos] Backend error: {exc}", file=sys.stderr)
        _print_tier0(candidates)
        return 0

    if response:
        print(response)
        return 0

    _print_tier0(candidates)
    return 0


def _check_mode() -> int:
    """Print index staleness diagnostics and return process exit code."""
    last = staleness.read_last_indexed()
    pkg_db = staleness.detect_package_db()
    stale = staleness.is_stale()

    print(f"Last indexed: {last.isoformat() if last else 'never'}")
    print(f"Package DB: {pkg_db or 'unknown'}")
    if pkg_db:
        from pathlib import Path
        from datetime import datetime

        mtime = datetime.fromtimestamp(Path(pkg_db).stat().st_mtime)
        print(f"Package DB mtime: {mtime.isoformat()}")
    print(f"Update needed: {'yes' if stale else 'no'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for install, update, check, and query flows."""
    parser = argparse.ArgumentParser(
        prog="sempropos",
        description="Semantic command proposer based on local man pages.",
    )
    parser.add_argument("query", nargs="?", help="Natural language task description")
    parser.add_argument("--install", action="store_true", help="Build index and embeddings")
    parser.add_argument("--update", action="store_true", help="Refresh index and embeddings")
    parser.add_argument("--check", action="store_true", help="Check index staleness")

    args = parser.parse_args(argv)

    # Ensuring only one of --install, --update, --check is used
    flags = [args.install, args.update, args.check]
    if sum(bool(flag) for flag in flags) > 1:
        parser.error("Use at most one of --install, --update, --check")

    if args.install:
        runtime_assets.ensure_runtime_assets(progress=True)
        builder.build_index(progress=True)
        print("[sempropos] Install completed (runtime assets + index).")
        return 0

    if args.update:
        runtime_assets.ensure_runtime_assets(progress=True)
        builder.build_index(progress=True)
        print("[sempropos] Update completed (runtime assets + index).")
        return 0

    if args.check:
        return _check_mode()

    if args.query:
        return _query_mode(args.query)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
