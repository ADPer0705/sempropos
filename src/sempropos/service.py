"""UI-agnostic application service powering both the warm daemon and in-process CLI."""

from __future__ import annotations

import contextlib
import sqlite3
import time
from typing import Any

from sempropos import __version__, config
from sempropos.index import repository, schema, staleness
from sempropos.intelligence import (
    ProviderConfigurationError,
    ProviderUnavailableError,
    StructuredPrompt,
    build_prompt,
    detect_synthesis_provider,
    is_embedding_model_warm,
    list_embedding_providers,
    list_synthesis_providers,
    models,
    parse_synthesis_output,
    shutdown_providers,
    synthesize,
    verify_command,
)
from sempropos.model.spec import CandidateContext, ToolSpec
from sempropos.retrieval import (
    analyze_query,
    hybrid_fusion,
    load_candidates,
    query_bm25_ranking,
    query_semantic_matches,
    reload_embeddings,
    should_run_semantic,
)
from sempropos.sources.discovery import compute_missing_binaries, discover_path_binaries


def _candidate_to_dict(cand: CandidateContext) -> dict[str, Any]:
    return {
        "tool_id": cand.tool_id,
        "name": cand.name,
        "summary": cand.summary,
        "description": cand.description,
        "category": cand.category,
        "aliases": list(cand.aliases),
        "synopsis": list(cand.synopsis),
        "subcommands": [s.to_dict() for s in cand.subcommands],
        "flags": [f.to_dict() for f in cand.flags],
        "examples": [e.to_dict() for e in cand.examples],
        "sources": list(cand.sources),
        "score": round(cand.score, 4),
    }


class SemService:
    """Stateful read-only query engine (reused across requests in daemon mode)."""

    def __init__(self) -> None:
        self._conn: sqlite3.Connection | None = None
        self._loaded_meta_mtime: float | None = None
        self._loaded_db_path: str | None = None

    def _current_meta_mtime(self) -> float | None:
        p = config.index_meta_path()
        try:
            return p.stat().st_mtime if p.exists() else None
        except OSError:
            return None

    def _get_conn(self) -> sqlite3.Connection:
        db_str = str(config.db_path())
        meta_mtime = self._current_meta_mtime()
        if (
            self._conn is not None
            and db_str == self._loaded_db_path
            and meta_mtime == self._loaded_meta_mtime
        ):
            return self._conn

        self.reload()
        self._conn = schema.open_connection(read_only=True)
        self._loaded_db_path = db_str
        self._loaded_meta_mtime = meta_mtime
        return self._conn

    def reload(self) -> None:
        """Close cached SQLite reader and drop mmap embeddings so next call reads fresh disk state."""
        if self._conn is not None:
            with contextlib.suppress(sqlite3.Error):
                self._conn.close()
        self._conn = None
        self._loaded_db_path = None
        self._loaded_meta_mtime = None
        reload_embeddings()

    def close(self) -> None:
        self.reload()
        shutdown_providers()

    # =========================================================================
    # Tri-Modal Operations: search (apropos), show (man/--help), ask (synthesis)
    # =========================================================================

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        category: str | None = None,
    ) -> dict[str, Any]:
        """Instant 0ms-LLM tool discovery replacing ``apropos``."""
        t0 = time.perf_counter()
        try:
            config.read_validated_index_meta()
        except RuntimeError as exc:
            return {
                "status": "index_error",
                "error": str(exc),
                "query": query,
                "results": [],
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }

        conn = self._get_conn()
        analyzed = analyze_query(query)
        bm25 = query_bm25_ranking(
            query,
            top_k=max(limit * 2, 20),
            conn=conn,
            analyzed=analyzed,
            category=category,
        )

        # In search mode, only invoke semantic recall if embeddings are ALREADY warm
        # or lexical returned nothing (keeps `sem search` instantaneous).
        semantic: list[tuple[int, float]] = []
        if is_embedding_model_warm() or not bm25:
            with contextlib.suppress(Exception):
                semantic = query_semantic_matches(query, top_k=max(limit * 2, 20), conn=conn)

        fused = hybrid_fusion(bm25, semantic)[:limit]
        candidates = load_candidates(
            conn,
            fused,
            query,
            max_flags=3,
            max_examples=2,
            max_subcommands=3,
        )
        return {
            "status": "ok",
            "query": query,
            "results": [_candidate_to_dict(c) for c in candidates],
            "stale": staleness.is_index_stale(),
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    def show(
        self,
        tool_name: str,
        *,
        subcommand: str | None = None,
        flag_filter: str | None = None,
    ) -> dict[str, Any]:
        """Instant 0ms-LLM structured tool reference card replacing ``man`` and ``--help``."""
        t0 = time.perf_counter()
        try:
            config.read_validated_index_meta()
        except RuntimeError as exc:
            return {
                "status": "index_error",
                "error": str(exc),
                "tool": None,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }

        conn = self._get_conn()
        spec = repository.get_tool(conn, tool_name)
        if spec is None:
            return {
                "status": "not_found",
                "error": f"Tool '{tool_name}' is not in the local index.",
                "tool": None,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }

        payload = spec.to_dict()

        if subcommand:
            matched_sub = next(
                (s for s in spec.subcommands if s.name.lower() == subcommand.strip().lower()),
                None,
            )
            if matched_sub is None:
                return {
                    "status": "not_found",
                    "error": f"Subcommand '{subcommand}' not found on '{spec.name}'.",
                    "tool": payload,
                    "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                }
            payload["focused_subcommand"] = matched_sub.to_dict()

        if flag_filter:
            needle = flag_filter.strip().lower()
            payload["flags"] = [
                f
                for f in payload["flags"]
                if needle in str(f.get("display") or "").lower()
                or needle in str(f.get("description") or "").lower()
                or needle in str(f.get("section") or "").lower()
            ]

        return {
            "status": "ok",
            "tool": payload,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    def list_names(self) -> list[str]:
        """Return all indexed tool names and aliases (for shell completions)."""
        if not config.db_path().exists():
            return []
        try:
            conn = self._get_conn()
            return repository.list_tool_names(conn)
        except Exception:  # noqa: BLE001
            return []

    def _retrieve_candidates_for_ask(
        self,
        conn: sqlite3.Connection,
        query: str,
        *,
        tool_filter: str | None,
    ) -> list[CandidateContext]:
        if tool_filter:
            tool_id = repository.resolve_tool_id(conn, tool_filter)
            if tool_id is None:
                return []
            return load_candidates(
                conn, [(tool_id, 1.0)], query, max_flags=12, max_examples=6, max_subcommands=6
            )

        analyzed = analyze_query(query)
        bm25 = query_bm25_ranking(query, top_k=20, conn=conn, analyzed=analyzed)

        settings = config.load_config()
        semantic: list[tuple[int, float]] = []
        if settings.embedding.provider != "none" and should_run_semantic(
            bm25,
            analyzed,
            lazy_fallback=settings.embedding.lazy_fallback,
            embeddings_warm=is_embedding_model_warm(),
        ):
            with contextlib.suppress(Exception):
                semantic = query_semantic_matches(query, top_k=20, conn=conn)

        fused = hybrid_fusion(bm25, semantic)
        ranked = fused[:5] or bm25[:5] or semantic[:5]
        return load_candidates(conn, ranked, query, max_flags=8, max_examples=4, max_subcommands=4)

    def _repair_once(
        self,
        prompt: StructuredPrompt,
        raw: str,
        backend: str,
    ) -> tuple[str | None, str, tuple[str, ...]]:
        cleaned = (raw or "").strip()
        if not cleaned or cleaned.upper().strip(".!") == "NONE":
            return None, "", ()

        repair_prompt = StructuredPrompt(
            system=prompt.system,
            context=prompt.context,
            query=(
                f"{prompt.query}\n"
                f"Your previous reply {cleaned!r} was not a valid runnable command. "
                "Return valid JSON with one concrete shell command, or none=true."
            ),
            json_schema=prompt.json_schema,
        )
        try:
            retry = synthesize(repair_prompt, backend=backend)
        except Exception:  # noqa: BLE001
            return None, "", ()
        parsed = parse_synthesis_output(retry.command or retry.raw_output)
        return parsed.command, parsed.explanation, parsed.alternatives

    def ask(  # noqa: C901
        self,
        query: str,
        *,
        tool_filter: str | None = None,
        backend: str | None = None,
        alternatives: int = 1,
    ) -> dict[str, Any]:
        """Retrieve grounded ToolSpecs, synthesize a command, and verify safety/flags."""
        t0 = time.perf_counter()
        stale = staleness.is_index_stale()

        try:
            config.read_validated_index_meta()
        except RuntimeError as exc:
            return {
                "status": "index_error",
                "error": str(exc),
                "query": query,
                "command": None,
                "candidates": [],
                "stale": stale,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }

        conn = self._get_conn()
        candidates = self._retrieve_candidates_for_ask(conn, query, tool_filter=tool_filter)
        cand_dicts = [_candidate_to_dict(c) for c in candidates]

        if not candidates:
            msg = (
                f"Tool '{tool_filter}' was not found in the local index."
                if tool_filter
                else "No relevant tools were found in the local index."
            )
            return {
                "status": "no_candidates",
                "error": msg,
                "query": query,
                "command": None,
                "candidates": [],
                "stale": stale,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }

        try:
            selected_backend = backend or detect_synthesis_provider()[0]
        except ProviderUnavailableError as exc:
            return {
                "status": "fallback",
                "error": f"Backend unavailable: {exc}",
                "query": query,
                "command": None,
                "provider": "tier0",
                "candidates": cand_dicts,
                "stale": stale,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }

        prompt = build_prompt(
            query,
            [c.to_prompt_dict() for c in candidates],
            alternatives=max(1, alternatives),
        )

        try:
            result = synthesize(prompt, backend=selected_backend)
        except ProviderConfigurationError as exc:
            return {
                "status": "fallback",
                "error": f"Configuration error: {exc}",
                "query": query,
                "command": None,
                "provider": selected_backend,
                "candidates": cand_dicts,
                "stale": stale,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "fallback",
                "error": f"Backend error: {exc}",
                "query": query,
                "command": None,
                "provider": selected_backend,
                "candidates": cand_dicts,
                "stale": stale,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }

        parsed = parse_synthesis_output(result.raw_output or result.command)
        command = result.command or parsed.command
        explanation = result.explanation or parsed.explanation
        alt_cmds = list(result.alternatives or parsed.alternatives)

        if not command and selected_backend != "tier0" and not parsed.explicit_none:
            repaired_cmd, repaired_exp, repaired_alts = self._repair_once(
                prompt, result.raw_output or result.command, selected_backend
            )
            command = repaired_cmd
            explanation = explanation or repaired_exp
            if not alt_cmds and repaired_alts:
                alt_cmds = list(repaired_alts)

        if not command:
            return {
                "status": "fallback",
                "error": (
                    "No synthesis LLM configured; showing structured local ToolSpecs."
                    if selected_backend == "tier0"
                    else "Model did not return a usable command; showing retrieved ToolSpecs."
                ),
                "query": query,
                "command": None,
                "provider": selected_backend,
                "model": result.metadata.get("model"),
                "candidates": cand_dicts,
                "stale": stale,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }

        # Verify command executable, cross-check flags against ToolSpec, classify safety.
        matched_spec: ToolSpec | None = None
        if result.tool:
            matched_spec = repository.get_tool(conn, result.tool)
        if matched_spec is None:
            for cand in candidates:
                if command.split()[0] in (cand.name, *cand.aliases):
                    matched_spec = repository.get_tool_by_id(conn, cand.tool_id)
                    break
        if matched_spec is None and candidates:
            matched_spec = repository.get_tool_by_id(conn, candidates[0].tool_id)

        verification = verify_command(command, matched_spec)
        return {
            "status": "ok",
            "query": query,
            "command": command,
            "tool": matched_spec.name if matched_spec else result.tool,
            "explanation": explanation,
            "alternatives": alt_cmds[: max(0, alternatives - 1)],
            "provider": selected_backend,
            "model": result.metadata.get("model"),
            "verification": verification.to_dict(),
            "candidates": cand_dicts,
            "stale": stale,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    def doctor(self) -> dict[str, Any]:
        """Collect comprehensive diagnostics for ``sem doctor``."""
        cfg = config.load_config()
        meta = config.read_index_meta() or {}
        complete = config.is_index_complete()
        last = staleness.read_last_indexed()
        pkg_db = staleness.detect_package_db()
        is_stale = staleness.is_index_stale()

        stats_dict: dict[str, Any] = {}
        missing_count = 0
        missing_sample: list[str] = []

        if config.db_path().exists():
            try:
                conn = self._get_conn()
                st = repository.index_stats(conn)
                stats_dict = {
                    "tool_count": st.tool_count,
                    "alias_count": st.alias_count,
                    "subcommand_count": st.subcommand_count,
                    "flag_count": st.flag_count,
                    "example_count": st.example_count,
                    "tools_with_flags": st.tools_with_flags,
                    "tools_with_examples": st.tools_with_examples,
                    "tools_with_subcommands": st.tools_with_subcommands,
                    "flag_coverage_pct": round(
                        (st.tools_with_flags / st.tool_count * 100.0) if st.tool_count else 0.0, 1
                    ),
                    "example_coverage_pct": round(
                        (st.tools_with_examples / st.tool_count * 100.0) if st.tool_count else 0.0, 1
                    ),
                    "source_counts": st.source_counts,
                }
                indexed = set(repository.list_tool_names(conn))
                discovery = discover_path_binaries()
                all_missing = compute_missing_binaries(indexed, discovery)
                missing_count = len(all_missing)
                missing_sample = all_missing[:15]
            except Exception:  # noqa: BLE001
                pass

        return {
            "version": __version__,
            "index_complete": complete,
            "index_revision": meta.get("index_revision"),
            "expected_index_revision": config.INDEX_REVISION,
            "last_indexed": last.isoformat() if last else None,
            "package_db": pkg_db,
            "stale": is_stale,
            "stats": stats_dict,
            "unindexed_binaries_count": missing_count,
            "unindexed_binaries_sample": missing_sample,
            "synthesis_config": {
                "provider": cfg.synthesis.provider,
                "model": cfg.synthesis.model,
            },
            "embedding_config": {
                "provider": cfg.embedding.provider,
                "model": cfg.embedding.model,
                "lazy_fallback": cfg.embedding.lazy_fallback,
            },
            "daemon_config": {
                "enabled": cfg.daemon.enabled,
                "idle_timeout_seconds": cfg.daemon.idle_timeout_seconds,
            },
            "providers": {
                "synthesis": list_synthesis_providers(),
                "embedding": list_embedding_providers(),
            },
            "local_gguf": {
                "runtime": models.local_runtime_kind(),
                "installed_models": models.installed_model_names(),
            },
        }
