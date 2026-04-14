"""Command-line interface for sempropos."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from sempropos import runtime_assets
from sempropos.index import builder, schema, staleness
from sempropos.intelligence import config as intelligence_config
from sempropos.intelligence import facade as intelligence_facade
from sempropos.intelligence.contracts import (
    IntelligenceError,
    ProviderExecutionError,
    ProviderUnavailableError,
)
from sempropos.retrieval import bm25, expansion, flag_retrieval, fusion, semantic
from sempropos.synthesis import backend, prompt


def _load_candidates(tool_ids: list[int], query: str) -> list[dict]:
    """Load top tool candidates with relevant flags and examples."""
    candidates: list[dict] = []
    missing_tool_ids: list[int] = []
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
                missing_tool_ids.append(tool_id)
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
                    "flags": flag_retrieval.get_relevant_flags(
                        int(row["id"]), query, top_k=6
                    ),
                    "examples": [
                        {
                            "command": item["command"],
                            "context": item["context"],
                        }
                        for item in examples
                    ],
                }
            )

    if missing_tool_ids:
        print(
            "[sempropos] Warning: some indexed tool references are missing from the "
            "database. Run `sempropos --update` to rebuild index.",
            file=sys.stderr,
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


def _print_tier0(candidates: list[dict], *, reason: str = "no_provider") -> None:
    """Print structured fallback output when no local LLM is available."""
    if reason == "no_candidates":
        print("No relevant tools were found in the local index.\n")
    elif reason == "empty_response":
        print("Synthesis backend returned empty output. Showing matched tools:\n")
    elif reason == "synthesis_unavailable":
        print("Synthesis backend unavailable. Showing matched tools:\n")
    else:
        print("No local LLM detected. Showing matched tools:\n")

    if not candidates:
        print("No tool matches found in the local index.")
        print(
            "\nRun `sempropos --install` to download llama.cpp and build the index for command synthesis."
        )
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

    print(
        "Run `sempropos --install` to download llama.cpp and build the index for command synthesis."
    )


def _set_synthesis_provider_mode(provider: str) -> int:
    mapped = intelligence_config.synthesis_provider_from_text(provider)
    if mapped is None:
        print(f"Unknown synthesis provider: {provider}", file=sys.stderr)
        return 1
    intelligence_config.update_settings(synthesis_provider=mapped)
    print(f"[sempropos] Synthesis provider set to {mapped}.")
    return 0


def _set_embedding_provider_mode(provider: str) -> int:
    mapped = intelligence_config.embedding_provider_from_text(provider)
    if mapped is None:
        print(f"Unknown embedding provider: {provider}", file=sys.stderr)
        return 1
    intelligence_config.update_settings(embedding_provider=mapped)
    print(f"[sempropos] Embedding provider set to {mapped}.")
    return 0


def _set_embedding_model_mode(model: str) -> int:
    trimmed = model.strip()
    if not trimmed:
        print("Embedding model cannot be empty.", file=sys.stderr)
        return 1
    intelligence_config.update_settings(embedding_model=trimmed)
    print(f"[sempropos] Embedding model set to {trimmed}.")
    return 0


def _set_provider_model_mode(provider: str, model: str) -> int:
    mapped = intelligence_config.synthesis_provider_from_text(provider)
    if mapped is None:
        print(f"Unknown provider: {provider}", file=sys.stderr)
        return 1
    intelligence_config.update_provider_config(mapped, model=model.strip())
    print(f"[sempropos] Model for {mapped} updated.")
    return 0


def _set_provider_base_url_mode(provider: str, base_url: str) -> int:
    mapped = intelligence_config.synthesis_provider_from_text(provider)
    if mapped is None:
        print(f"Unknown provider: {provider}", file=sys.stderr)
        return 1
    intelligence_config.update_provider_config(mapped, base_url=base_url.strip())
    print(f"[sempropos] Base URL for {mapped} updated.")
    return 0


def _set_provider_api_key_env_mode(provider: str, env_var: str) -> int:
    mapped = intelligence_config.synthesis_provider_from_text(provider)
    if mapped is None:
        print(f"Unknown provider: {provider}", file=sys.stderr)
        return 1
    intelligence_config.update_provider_config(mapped, api_key_env=env_var.strip())
    print(f"[sempropos] API key env var for {mapped} updated.")
    return 0


def _set_provider_api_key_mode(provider: str, api_key: str) -> int:
    mapped = intelligence_config.synthesis_provider_from_text(provider)
    if mapped is None:
        print(f"Unknown provider: {provider}", file=sys.stderr)
        return 1
    storage = intelligence_config.store_provider_api_key(mapped, api_key.strip())
    print(f"[sempropos] API key stored for {mapped} using {storage}.")
    return 0


def _list_providers_mode() -> int:
    synthesis = intelligence_facade.list_synthesis_providers()
    embedding = intelligence_facade.list_embedding_providers()

    print("Synthesis providers:")
    for name, available in synthesis.items():
        marker = "yes" if available else "no"
        print(f"  {name}: {marker}")

    print("\nEmbedding providers:")
    for name, available in embedding.items():
        marker = "yes" if available else "no"
        print(f"  {name}: {marker}")
    return 0


def _show_config_mode() -> int:
    settings = intelligence_config.load_settings()
    print(f"Config path: {intelligence_config.config_path()}")
    print(f"Synthesis provider: {settings.synthesis_provider}")
    print(f"Embedding provider: {settings.embedding_provider}")
    print(f"Embedding model: {settings.embedding_model or '<default>'}")
    print("Provider runtime settings:")
    for name, runtime in settings.providers.items():
        print(f"  [{name}]")
        if runtime.model:
            print(f"    model = {runtime.model}")
        if runtime.base_url:
            print(f"    base_url = {runtime.base_url}")
        if runtime.api_key_env:
            print(f"    api_key_env = {runtime.api_key_env}")
        if runtime.api_key_secret:
            print("    api_key_secret = <stored>")
        if runtime.api_key_plaintext:
            print("    api_key_plaintext = <stored>")
        print(f"    timeout_seconds = {runtime.timeout_seconds}")
    return 0


def _setup_mode() -> int:
    print("sempropos intelligence setup")
    print("Available synthesis providers:")
    print("  " + ", ".join(intelligence_config.supported_synthesis_providers()))
    synthesis_provider = input("Synthesis provider [tier0]: ").strip() or "tier0"

    print("Available embedding providers:")
    print("  " + ", ".join(intelligence_config.supported_embedding_providers()))
    embedding_provider = (
        input("Embedding provider [fastembed_local]: ").strip() or "fastembed_local"
    )
    embedding_model = input("Embedding model (leave empty for default): ").strip()

    result = _set_synthesis_provider_mode(synthesis_provider)
    if result != 0:
        return result
    result = _set_embedding_provider_mode(embedding_provider)
    if result != 0:
        return result
    if embedding_model:
        result = _set_embedding_model_mode(embedding_model)
        if result != 0:
            return result

    selected_synthesis = intelligence_config.synthesis_provider_from_text(
        synthesis_provider
    )
    if selected_synthesis in {
        "openai_compatible",
        "anthropic",
        "gemini",
        "mistral",
        "huggingface",
    }:
        api_env = input("API key env var name (leave empty to keep default): ").strip()
        if api_env:
            _set_provider_api_key_env_mode(str(selected_synthesis), api_env)
        key = input("API key (leave empty to skip): ").strip()
        if key:
            _set_provider_api_key_mode(str(selected_synthesis), key)

    model = input("Synthesis model (leave empty to keep current): ").strip()
    if model:
        _set_provider_model_mode(str(selected_synthesis), model)
    base_url = input("Provider base URL (leave empty to keep current): ").strip()
    if base_url:
        _set_provider_base_url_mode(str(selected_synthesis), base_url)

    print("[sempropos] Setup completed.")
    return 0


def _query_mode(
    query: str,
    *,
    backend_override: str | None = None,
) -> int:
    """Execute the query pipeline from retrieval through synthesis fallback."""
    schema.initialize()

    if staleness.is_stale():
        print(
            "[sempropos] Package database has changed. Run `sempropos --update` "
            "to refresh index."
        )

    try:
        tokens = expansion.expand(query)
        bm25_results = bm25.search(tokens, top_k=10)
        semantic_results = semantic.search(query, top_k=10)
    except RuntimeError as exc:
        print(str(exc))
        return 1

    fused = fusion.reciprocal_rank_fusion([bm25_results, semantic_results], k=60)

    ranked_ids = [tool_id for tool_id, _score in fused[:3]]
    if not ranked_ids:
        ranked_ids = [tool_id for tool_id, _ in bm25_results[:3]]
    if not ranked_ids:
        ranked_ids = [tool_id for tool_id, _ in semantic_results[:3]]

    candidates = _load_candidates(ranked_ids, query)
    if not candidates:
        _print_tier0(candidates, reason="no_candidates")
        return 0

    try:
        selected_backend = backend_override or backend.detect()
    except ProviderUnavailableError as exc:
        print(f"[sempropos] Backend unavailable: {exc}", file=sys.stderr)
        _print_tier0(candidates, reason="synthesis_unavailable")
        return 0

    if selected_backend == "tier0":
        _print_tier0(candidates, reason="no_provider")
        return 0

    built_prompt = prompt.build_prompt(query, candidates)
    try:
        response = backend.run(built_prompt, selected_backend).strip()
    except ProviderUnavailableError as exc:
        print(f"[sempropos] Backend unavailable: {exc}", file=sys.stderr)
        _print_tier0(candidates, reason="synthesis_unavailable")
        return 0
    except ProviderExecutionError as exc:
        print(f"[sempropos] Backend execution error: {exc}", file=sys.stderr)
        _print_tier0(candidates, reason="synthesis_unavailable")
        return 0
    except IntelligenceError as exc:
        print(f"[sempropos] Intelligence layer error: {exc}", file=sys.stderr)
        _print_tier0(candidates, reason="synthesis_unavailable")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[sempropos] Unexpected backend error: {exc}", file=sys.stderr)
        _print_tier0(candidates, reason="synthesis_unavailable")
        return 0

    if response:
        print(response)
        return 0

    print(
        f"[sempropos] Warning: backend '{selected_backend}' returned empty output.",
        file=sys.stderr,
    )
    _print_tier0(candidates, reason="empty_response")
    return 0


def _check_mode() -> int:
    """Print index staleness diagnostics and return process exit code."""
    last = staleness.read_last_indexed()
    pkg_db = staleness.detect_package_db()
    stale = staleness.is_stale()

    print(f"Last indexed: {last.isoformat() if last else 'never'}")
    print(f"Package DB: {pkg_db or 'unknown'}")
    if pkg_db:
        mtime = datetime.fromtimestamp(Path(pkg_db).stat().st_mtime)
        print(f"Package DB mtime: {mtime.isoformat()}")
    print(f"Update needed: {'yes' if stale else 'no'}")
    return 0


def _run_index_build(*, action: str, use_floor_model: bool) -> int:
    """Run runtime asset bootstrap and index build for install/update actions."""
    try:
        assets = runtime_assets.ensure_runtime_assets(
            progress=True,
            use_floor_model=use_floor_model,
        )
        builder.build_index(
            progress=True,
            force_floor_embedding=use_floor_model,
            synthesis_model=assets.get("synthesis_model") or "",
        )
    except RuntimeError as exc:
        print(f"[sempropos] {action} failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[sempropos] Unexpected {action} error: {exc}", file=sys.stderr)
        return 1

    print(f"[sempropos] {action.capitalize()} completed (runtime assets + index).")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for install, update, check, and query flows."""
    parser = argparse.ArgumentParser(
        prog="sempropos",
        description="Semantic command proposer based on local man pages.",
    )
    parser.add_argument("query", nargs="*", help="Natural language task description")
    parser.add_argument(
        "--install", action="store_true", help="Build index and embeddings"
    )
    parser.add_argument(
        "--update", action="store_true", help="Refresh index and embeddings"
    )
    parser.add_argument("--check", action="store_true", help="Check index staleness")
    parser.add_argument(
        "--use-floor-model",
        action="store_true",
        help="Use compact synthesis model during --install/--update",
    )
    parser.add_argument(
        "--backend",
        choices=[
            "llama_cpp",
            "ollama",
            "openai_compatible",
            "anthropic",
            "gemini",
            "mistral",
            "huggingface",
            "tier0",
        ],
        help="Force a specific synthesis provider for this invocation",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Interactive intelligence provider setup",
    )
    parser.add_argument(
        "--list-providers",
        action="store_true",
        help="List synthesis and embedding providers with availability",
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Print current intelligence configuration",
    )
    parser.add_argument(
        "--set-synthesis-provider",
        metavar="PROVIDER",
        help="Persist synthesis provider selection",
    )
    parser.add_argument(
        "--set-embedding-provider",
        metavar="PROVIDER",
        help="Persist embedding provider selection",
    )
    parser.add_argument(
        "--set-embedding-model",
        metavar="MODEL",
        help="Persist embedding model selection",
    )
    parser.add_argument(
        "--set-provider-model",
        nargs=2,
        metavar=("PROVIDER", "MODEL"),
        help="Persist synthesis provider model",
    )
    parser.add_argument(
        "--set-provider-base-url",
        nargs=2,
        metavar=("PROVIDER", "BASE_URL"),
        help="Persist synthesis provider base URL",
    )
    parser.add_argument(
        "--set-provider-api-key-env",
        nargs=2,
        metavar=("PROVIDER", "ENV_VAR"),
        help="Persist synthesis provider API key env var",
    )
    parser.add_argument(
        "--set-provider-api-key",
        nargs=2,
        metavar=("PROVIDER", "API_KEY"),
        help="Persist synthesis provider API key via keyring/config fallback",
    )

    args = parser.parse_args(argv)

    operation_flags = [
        args.install,
        args.update,
        args.check,
        args.setup,
        args.list_providers,
        args.show_config,
        bool(args.set_synthesis_provider),
        bool(args.set_embedding_provider),
        bool(args.set_embedding_model),
        bool(args.set_provider_model),
        bool(args.set_provider_base_url),
        bool(args.set_provider_api_key_env),
        bool(args.set_provider_api_key),
    ]

    if sum(bool(flag) for flag in operation_flags) > 1:
        parser.error(
            "Use at most one management mode per invocation (install/update/check/setup/list/show/set)."
        )

    if any(operation_flags) and args.query:
        parser.error("Query arguments cannot be combined with management modes.")

    if args.setup:
        return _setup_mode()

    if args.list_providers:
        return _list_providers_mode()

    if args.show_config:
        return _show_config_mode()

    if args.set_synthesis_provider:
        return _set_synthesis_provider_mode(args.set_synthesis_provider)

    if args.set_embedding_provider:
        return _set_embedding_provider_mode(args.set_embedding_provider)

    if args.set_embedding_model:
        return _set_embedding_model_mode(args.set_embedding_model)

    if args.set_provider_model:
        provider, model = args.set_provider_model
        return _set_provider_model_mode(provider, model)

    if args.set_provider_base_url:
        provider, base_url = args.set_provider_base_url
        return _set_provider_base_url_mode(provider, base_url)

    if args.set_provider_api_key_env:
        provider, env_var = args.set_provider_api_key_env
        return _set_provider_api_key_env_mode(provider, env_var)

    if args.set_provider_api_key:
        provider, api_key = args.set_provider_api_key
        return _set_provider_api_key_mode(provider, api_key)

    if args.install:
        return _run_index_build(action="install", use_floor_model=args.use_floor_model)

    if args.update:
        return _run_index_build(action="update", use_floor_model=args.use_floor_model)

    if args.check:
        return _check_mode()

    if args.query:
        full_query = " ".join(args.query)
        if not full_query.strip():
            print("Query cannot be empty.", file=sys.stderr)
            return 1
        return _query_mode(
            full_query.strip(),
            backend_override=args.backend,
        )

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
