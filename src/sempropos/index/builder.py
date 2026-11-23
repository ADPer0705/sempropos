"""Multi-source incremental indexing pipeline for sempropos.

Pipeline stages:
  1. Discover runnable ``$PATH`` binaries and symlink aliases.
  2. Extract structured documentation fragments in parallel across enabled sources:
     - Direct ``man(7)`` / ``mdoc(7)`` roff parser (with wide rendered fallback)
     - Offline ``tldr-pages`` cache / archive (root + hyphenated subcommand pages)
     - Static Fish / Zsh shell completions
     - Safe ``--help`` probing for binaries absent from man pages (e.g. ``7z``, ``uv``)
  3. Merge fragments deterministically into canonical :class:`ToolSpec` records.
  4. Incrementally upsert changed/new tools by ``content_hash`` and prune removed binaries.
  5. Sync L2-normalized tool-card embeddings incrementally by ``tool_id``.
"""

from __future__ import annotations

import contextlib
import gc
import os
import sqlite3
import tempfile
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from tqdm import tqdm  # type: ignore[import-untyped]

from sempropos import config
from sempropos.index import repository, schema
from sempropos.intelligence.facade import detect_embedding_provider, embed_texts
from sempropos.model.spec import ToolSpec
from sempropos.sources.completions import extract_completion_fragments
from sempropos.sources.discovery import DiscoveryContext, discover_path_binaries
from sempropos.sources.help_probe import HelpProbeFragment, is_safe_to_probe, probe_tool_help
from sempropos.sources.man import extract_man_bundles
from sempropos.sources.merger import (
    ToolSourceBundle,
    attach_symlink_aliases,
    merge_tool_bundle,
)
from sempropos.sources.tldr import (
    extract_tldr_fragments,
    find_local_tldr_sources,
    sync_tldr_archive,
)
from sempropos.utils import atomic_write_text

_DB_COMMIT_BATCH = 100
_EMBED_BATCH = 64
_PROBE_WORKERS = min(os.cpu_count() or 4, 8)
_PROBE_INFLIGHT = _PROBE_WORKERS * 2


@dataclass(frozen=True)
class IndexBuildReport:
    """Summary returned after an index build or incremental update."""

    total_tools: int
    updated_tools: int
    unchanged_tools: int
    removed_tools: int
    total_flags: int
    total_subcommands: int
    total_examples: int
    embedding_dim: int
    embedding_provider: str
    embedding_model: str
    source_counts: dict[str, int] = field(default_factory=dict)


# ============================================================================
# Safe Concurrent --help Probing
# ============================================================================


def _probe_missing_tools(
    context: DiscoveryContext,
    already_rich: set[str],
    *,
    timeout: float,
    progress: bool,
) -> dict[str, HelpProbeFragment]:
    """Probe ``--help`` only for safe installed binaries lacking rich man documentation."""
    candidates: list[tuple[str, Path]] = []
    seen_canonical: set[str] = set()
    for name, bin_path in sorted(context.binaries.items()):
        canonical = context.canonical_name(name)
        if canonical in already_rich or canonical in seen_canonical:
            continue
        if not is_safe_to_probe(canonical, bin_path):
            continue
        seen_canonical.add(canonical)
        candidates.append((canonical, bin_path))

    if not candidates:
        return {}

    pbar = tqdm(total=len(candidates), desc="Probing --help", unit="bin") if progress else None
    results: dict[str, HelpProbeFragment] = {}
    cand_iter = iter(candidates)
    in_flight: dict[Future[HelpProbeFragment | None], str] = {}

    with ThreadPoolExecutor(max_workers=_PROBE_WORKERS) as pool:

        def submit_next() -> bool:
            try:
                name, bpath = next(cand_iter)
            except StopIteration:
                return False
            in_flight[pool.submit(probe_tool_help, name, bpath, timeout=timeout)] = name
            return True

        for _ in range(_PROBE_INFLIGHT):
            if not submit_next():
                break

        while in_flight:
            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for fut in done:
                name = in_flight.pop(fut)
                try:
                    frag = fut.result()
                    if frag is not None:
                        results[name] = frag
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    if pbar is not None:
                        pbar.update(1)
                submit_next()

    if pbar is not None:
        pbar.close()
    return results


# ============================================================================
# Tool-ID Keyed Incremental Embedding Sync
# ============================================================================


def _atomic_save_npy(path: Path, values: np.ndarray[Any, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            np.save(handle, values)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _l2_normalize(matrix: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
    if matrix.size == 0:
        return matrix.astype(np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return np.asarray(matrix / norms, dtype=np.float32)


def _load_existing_vectors(
    emb_path: Path, ids_path: Path
) -> dict[int, np.ndarray[Any, Any]] | None:
    """Load existing ``{tool_id: unit_vector}`` map if aligned and readable."""
    if not (emb_path.exists() and ids_path.exists()):
        return None
    try:
        matrix = np.load(emb_path)
        ids = np.load(ids_path)
    except Exception:  # noqa: BLE001
        return None

    if matrix.ndim != 2 or ids.ndim != 1 or len(ids) != matrix.shape[0]:
        return None

    return {int(tid): matrix[idx].astype(np.float32) for idx, tid in enumerate(ids)}


def _sync_embeddings(
    conn: sqlite3.Connection,
    *,
    changed_ids: set[int],
    progress: bool,
    force: bool,
) -> int:
    """Synchronize L2-normalized tool-card embeddings incrementally by ``tool_id``.

    Unchanged rows retain their existing vectors; only newly inserted or
    content-modified ``tool_id``s are re-embedded, and deleted ``tool_id``s are
    dropped cleanly.
    """
    emb_path = config.tool_embeddings_path()
    ids_path = config.tool_embedding_ids_path()

    db_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM tools ORDER BY id").fetchall()]
    if not db_ids:
        _atomic_save_npy(emb_path, np.empty((0, 0), dtype=np.float32))
        _atomic_save_npy(ids_path, np.empty(0, dtype=np.int32))
        return 0

    existing_map = None if force else _load_existing_vectors(emb_path, ids_path)
    reusable: dict[int, np.ndarray[Any, Any]] = {}
    to_embed: list[int] = []

    for tid in db_ids:
        if existing_map is not None and tid in existing_map and tid not in changed_ids:
            reusable[tid] = existing_map[tid]
        else:
            to_embed.append(tid)

    # Fast no-op when every active row already has a valid vector and count matches.
    if (
        not to_embed
        and existing_map is not None
        and len(existing_map) == len(db_ids)
        and emb_path.exists()
        and ids_path.exists()
    ):
        first_vec = next(iter(reusable.values()))
        return int(first_vec.shape[0])

    new_vectors: dict[int, np.ndarray[Any, Any]] = {}
    dim = int(next(iter(reusable.values())).shape[0]) if reusable else 0

    pbar = tqdm(total=len(to_embed), desc="Embedding tools", unit="tool") if (progress and to_embed) else None
    try:
        for start in range(0, len(to_embed), _EMBED_BATCH):
            batch_ids = to_embed[start : start + _EMBED_BATCH]
            texts = [repository.get_embedding_text_for_id(conn, tid)[:2200] for tid in batch_ids]
            raw_vecs = embed_texts(texts, kind="document")
            if raw_vecs.ndim != 2 or raw_vecs.shape[0] != len(batch_ids):
                raise RuntimeError("Embedding provider returned unexpected vector shape.")
            norm_vecs = _l2_normalize(raw_vecs)
            dim = int(norm_vecs.shape[1])
            for idx, tid in enumerate(batch_ids):
                new_vectors[tid] = norm_vecs[idx]
            if pbar is not None:
                pbar.update(len(batch_ids))
    finally:
        if pbar is not None:
            pbar.close()

    if dim <= 0:
        raise RuntimeError("Unable to determine embedding dimension from indexed data.")

    merged = np.empty((len(db_ids), dim), dtype=np.float32)
    for idx, tid in enumerate(db_ids):
        vec = new_vectors.get(tid)
        if vec is None:
            vec = reusable[tid]
        merged[idx] = vec

    _atomic_save_npy(emb_path, merged)
    _atomic_save_npy(ids_path, np.asarray(db_ids, dtype=np.int32))
    del merged, reusable, new_vectors, existing_map
    gc.collect()
    return dim


def _unload_embedding_model() -> None:
    try:
        from sempropos.intelligence.providers import _fastembed as fe

        fe.unload_model()
    except Exception:  # noqa: BLE001
        pass
    gc.collect()


def _clear_index_data() -> None:
    """Wipe existing database and embedding files for a clean rebuild."""
    for path in (
        config.db_path(),
        Path(f"{config.db_path()}-wal"),
        Path(f"{config.db_path()}-shm"),
        config.tool_embeddings_path(),
        config.tool_embedding_ids_path(),
        config.index_meta_path(),
        config.last_indexed_path(),
    ):
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)


# ============================================================================
# Main Entry Point
# ============================================================================


def build_index(  # noqa: C901
    progress: bool = True,
    force: bool = False,
    *,
    offline: bool = False,
    enable_tldr: bool | None = None,
    enable_completions: bool | None = None,
    probe_help: bool | None = None,
    skip_embeddings: bool = False,
) -> IndexBuildReport:
    """Build or incrementally update the local structured ToolSpec index."""
    config.ensure_data_dirs()
    app_cfg = config.load_config()

    use_tldr = app_cfg.indexing.enable_tldr if enable_tldr is None else enable_tldr
    use_completions = (
        app_cfg.indexing.enable_completions if enable_completions is None else enable_completions
    )
    use_probe = app_cfg.indexing.probe_help if probe_help is None else probe_help

    meta = config.read_index_meta() or {}
    try:
        stored_index_rev = int(meta.get("index_revision") or 0)
    except (TypeError, ValueError):
        stored_index_rev = 0

    full_rebuild = force or stored_index_rev != config.INDEX_REVISION
    if not full_rebuild and config.db_path().exists():
        try:
            with schema.get_connection(read_only=True) as check_conn:
                if schema.read_schema_version(check_conn) != config.INDEX_REVISION:
                    full_rebuild = True
        except sqlite3.Error:
            full_rebuild = True

    if full_rebuild:
        _clear_index_data()

    schema.initialize_database()

    # Determine embedding strategy early so configuration issues fail fast.
    if skip_embeddings or app_cfg.embedding.provider == "none":
        current_embed_provider = "none"
        current_embed_model = ""
    else:
        embed_provider, embed_cfg = detect_embedding_provider()
        current_embed_provider = embed_provider
        current_embed_model = embed_cfg.model or ""

    # Stage 1: Discover installed runnable binaries and symlink aliases.
    discovery = discover_path_binaries()
    if not discovery.binaries:
        raise RuntimeError("No executable binaries were discovered on PATH.")

    # Stage 2: Extract multi-source structured fragments.
    man_bundles = extract_man_bundles(discovery)

    tldr_frags = {}
    if use_tldr:
        if not offline and not find_local_tldr_sources():
            with contextlib.suppress(Exception):
                sync_tldr_archive()
        tldr_frags = extract_tldr_fragments(discovery)

    comp_frags = extract_completion_fragments(discovery) if use_completions else {}

    probe_frags: dict[str, HelpProbeFragment] = {}
    if use_probe:
        # Probe binaries absent from man OR whose man page yielded neither flags nor subcommands.
        already_rich = {
            name
            for name, mb in man_bundles.items()
            if mb.doc.flags or mb.doc.subcommands
        }
        probe_frags = _probe_missing_tools(
            discovery,
            already_rich,
            timeout=app_cfg.indexing.help_probe_timeout,
            progress=progress,
        )

    # Stage 3: Assemble bundles and merge into canonical ToolSpec objects.
    all_names = (
        set(man_bundles) | set(tldr_frags) | set(comp_frags) | set(probe_frags)
    )
    bundles: dict[str, ToolSourceBundle] = {}
    for name in sorted(all_names):
        bpath = discovery.binaries.get(name)
        bundles[name] = ToolSourceBundle(
            name=name,
            binary_path=str(bpath) if bpath else None,
            man=man_bundles.get(name),
            tldr=tldr_frags.get(name),
            completion=comp_frags.get(name),
            help_probe=probe_frags.get(name),
        )
    attach_symlink_aliases(bundles, discovery)

    specs: list[ToolSpec] = []
    for name in sorted(bundles):
        merged = merge_tool_bundle(bundles[name])
        if merged is not None:
            specs.append(merged)

    if not specs:
        raise RuntimeError("No tool documentation could be extracted from any configured source.")

    # Stage 4: Incremental DB upsert + pruning of uninstalled tools.
    changed_ids: set[int] = set()
    updated_count = 0
    unchanged_count = 0
    active_names = {spec.name for spec in specs}

    with schema.get_connection() as conn:
        removed_ids = repository.delete_tools_not_in(conn, active_names)
        pbar = tqdm(total=len(specs), desc="Indexing tools", unit="tool") if progress else None
        batch_count = 0
        try:
            for spec in specs:
                tool_id, changed = repository.upsert_tool(conn, spec)
                if changed:
                    changed_ids.add(tool_id)
                    updated_count += 1
                else:
                    unchanged_count += 1
                batch_count += 1
                if batch_count >= _DB_COMMIT_BATCH:
                    conn.commit()
                    batch_count = 0
                if pbar is not None:
                    pbar.update(1)
            if batch_count > 0:
                conn.commit()
        finally:
            if pbar is not None:
                pbar.close()

        # Stage 5: Sync embeddings incrementally by tool_id.
        if current_embed_provider == "none":
            embedding_dim = 0
        else:
            stored_provider = str(meta.get("embedding_provider") or "")
            stored_model = str(meta.get("embedding_model") or "")
            try:
                stored_embed_rev = int(meta.get("embedding_revision") or 0)
            except (TypeError, ValueError):
                stored_embed_rev = 0
            force_embeddings = (
                full_rebuild
                or stored_provider != current_embed_provider
                or stored_model != current_embed_model
                or stored_embed_rev != config.EMBEDDING_REVISION
            )
            embedding_dim = _sync_embeddings(
                conn,
                changed_ids=changed_ids,
                progress=progress,
                force=force_embeddings,
            )

        stats = repository.index_stats(conn)

    _unload_embedding_model()

    atomic_write_text(
        config.last_indexed_path(),
        datetime.now(tz=UTC).isoformat(),
    )
    config.write_index_meta(
        embedding_dim=embedding_dim,
        embedding_model=current_embed_model,
        embedding_provider=current_embed_provider,
        tool_count=stats.tool_count,
        flag_count=stats.flag_count,
        subcommand_count=stats.subcommand_count,
        example_count=stats.example_count,
    )

    return IndexBuildReport(
        total_tools=stats.tool_count,
        updated_tools=updated_count,
        unchanged_tools=unchanged_count,
        removed_tools=len(removed_ids),
        total_flags=stats.flag_count,
        total_subcommands=stats.subcommand_count,
        total_examples=stats.example_count,
        embedding_dim=embedding_dim,
        embedding_provider=current_embed_provider,
        embedding_model=current_embed_model,
        source_counts=stats.source_counts,
    )
