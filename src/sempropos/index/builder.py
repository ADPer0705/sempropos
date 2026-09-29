"""Indexing pipeline.

Design goals:
  - Bounded memory: never hold more than a small window of man page texts, and
    stream embeddings to disk batch-by-batch instead of building the whole
    matrix in RAM.
  - Incremental updates: only parse and embed rows that are missing.
  - Crash-atomic metadata: ``index.meta`` is written only after a successful run.
"""

from __future__ import annotations

import contextlib
import gc
import os
import tempfile
from collections.abc import Generator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from tqdm import tqdm

from sempropos import config
from sempropos.index import parser, schema
from sempropos.intelligence.facade import detect_embedding_provider, embed_texts
from sempropos.sources.base import ToolRecord
from sempropos.sources.man import ManSource
from sempropos.utils import atomic_write_text

# --- Pipeline tuning knobs ---
# How many man page subprocesses to run concurrently.
_MAN_READ_WORKERS = min(os.cpu_count() or 4, 8)
# Cap in-flight futures (results retained in memory) to a small multiple of workers.
_MAN_INFLIGHT_WINDOW = _MAN_READ_WORKERS * 2
# How many parsed tools to accumulate before committing to the DB.
_DB_COMMIT_BATCH = 50
# How many text descriptions to embed in one model call.
_EMBED_BATCH = 64
# How many existing embedding rows to copy to disk at a time when merging.
_EMBED_COPY_CHUNK = 2048


# ============================================================================
# Bounded, concurrent man page processing
# ============================================================================


def _iter_parsed_tools(
    source: ManSource,
    pending: list[ToolRecord],
    progress: bool,
) -> Generator[tuple[ToolRecord, parser.ParsedManPage], None, None]:
    """Yield ``(record, parsed)`` with a bounded number of live man page texts.

    Older revisions submitted every tool at once and stored the futures in a
    dict, which kept every completed man page string alive until the whole run
    finished. This slides a fixed-size window instead, so peak memory stays
    proportional to the worker count rather than the tool count.
    """
    if not pending:
        return

    pbar = tqdm(total=len(pending), desc="Indexing", unit="tool") if progress else None
    pending_iter = iter(pending)
    in_flight: dict[Future, ToolRecord] = {}

    with ThreadPoolExecutor(max_workers=_MAN_READ_WORKERS) as pool:

        def submit_next() -> bool:
            try:
                record = next(pending_iter)
            except StopIteration:
                return False
            in_flight[pool.submit(source.load, record)] = record
            return True

        for _ in range(_MAN_INFLIGHT_WINDOW):
            if not submit_next():
                break

        while in_flight:
            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in done:
                record = in_flight.pop(future)
                if pbar is not None:
                    pbar.set_postfix_str(record.name)
                try:
                    raw_page = future.result()
                    if raw_page is None:
                        raise RuntimeError(f"no man page for {record.name}")
                    parsed = parser.parse_man_page(raw_page)
                    yield record, parsed
                except Exception:  # noqa: BLE001 — skip unreadable/unparseable pages
                    pass
                finally:
                    if pbar is not None:
                        pbar.update(1)
                submit_next()

    if pbar is not None:
        pbar.close()


# ============================================================================
# Batch DB insertion
# ============================================================================


def _insert_tool(conn, record: ToolRecord, parsed: parser.ParsedManPage) -> None:
    cursor = conn.execute(
        """
        INSERT INTO tools(name, section, description, synopsis)
        VALUES (?, ?, ?, ?)
        """,
        (record.name, record.section, record.description, parsed.get("synopsis") or None),
    )
    tool_id = int(cursor.lastrowid)

    for flag in parsed.get("flags", []):
        conn.execute(
            """
            INSERT INTO flags(tool_id, flag, long_flag, takes_value, value_hint, description)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                tool_id,
                flag.get("flag") or "*",
                flag.get("long_flag"),
                1 if flag.get("takes_value") else 0,
                flag.get("value_hint"),
                flag.get("description") or "",
            ),
        )

    for example in parsed.get("examples", []):
        conn.execute(
            """
            INSERT INTO examples(tool_id, command, context)
            VALUES (?, ?, ?)
            """,
            (tool_id, example.get("command") or "", example.get("context")),
        )


def _ingest_tools(
    conn,
    source: ManSource,
    pending: list[ToolRecord],
    progress: bool,
) -> None:
    """Parse man pages via a bounded generator and batch-commit to the DB."""
    batch_count = 0

    for record, parsed in _iter_parsed_tools(source, pending, progress):
        try:
            _insert_tool(conn, record, parsed)
            batch_count += 1
        except Exception:  # noqa: BLE001
            conn.rollback()
            continue

        if batch_count >= _DB_COMMIT_BATCH:
            conn.commit()
            batch_count = 0

    if batch_count > 0:
        conn.commit()


# ============================================================================
# Embedding persistence
# ============================================================================


def _atomic_save_npy(path: Path, values: np.ndarray) -> None:
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
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _embed_batch(texts: list[str]) -> np.ndarray:
    """Embed a single batch of texts (already capped to ``_EMBED_BATCH`` size)."""
    if not texts:
        return np.empty((0, 0), dtype=np.float32)

    safe_batch = [text[:2500] for text in texts]
    return embed_texts(safe_batch)


def _write_npy_header(handle, total: int, dim: int) -> None:
    np.lib.format.write_array_header_2_0(
        handle,
        {
            "descr": np.lib.format.dtype_to_descr(np.dtype(np.float32)),
            "fortran_order": False,
            "shape": (int(total), int(dim)),
        },
    )


def _load_embedding_state(
    emb_path: Path, ids_path: Path
) -> tuple[np.ndarray, np.ndarray] | None:
    """Load a persisted embedding matrix and its id vector, if aligned."""
    if not (emb_path.exists() and ids_path.exists()):
        return None

    try:
        embeddings = np.load(emb_path, mmap_mode="r")
        ids = np.load(ids_path)
    except Exception:  # noqa: BLE001 — corrupt files are treated as absent
        return None

    if embeddings.ndim != 2 or ids.ndim != 1 or len(ids) != embeddings.shape[0]:
        return None

    return embeddings, ids


def _infer_ids_from_matrix(
    emb_path: Path, db_ids: np.ndarray
) -> tuple[np.ndarray, np.ndarray] | None:
    """Recover an id vector for legacy installs that lack ``*_embedding_ids.npy``.

    Older revisions stored tool embeddings without a companion id file and
    relied on row order matching ``SELECT id ORDER BY id``. This reconstructs
    that mapping so an update stays incremental instead of re-embedding
    everything.
    """
    if not emb_path.exists():
        return None

    try:
        embeddings = np.load(emb_path, mmap_mode="r")
    except Exception:  # noqa: BLE001
        return None

    if embeddings.ndim != 2 or embeddings.shape[0] == 0 or embeddings.shape[0] > len(db_ids):
        return None

    return embeddings, db_ids[: embeddings.shape[0]]


def _write_embeddings(
    *,
    dest_emb_path: Path,
    dest_ids_path: Path,
    conn,
    table: str,
    existing: tuple[np.ndarray, np.ndarray] | None,
    new_ids: np.ndarray,
    label: str,
    progress: bool,
) -> int:
    """Write a complete embedding matrix plus id vector to disk in a streaming way.

    Only one batch of vectors is ever resident, so indexing stays well within
    modest memory budgets even for tens of thousands of rows.
    """
    existing_embeddings = existing[0] if existing is not None else None
    existing_ids = existing[1] if existing is not None else None
    n_existing = 0 if existing_ids is None else int(len(existing_ids))
    total = n_existing + int(len(new_ids))

    dest_emb_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    pbar = tqdm(total=total, desc=label, unit="text") if progress else None
    dim = 0
    written = 0

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=dest_emb_path.parent,
            prefix=f"{dest_emb_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)

            if total == 0:
                np.save(handle, np.empty((0, 0), dtype=np.float32))
            else:
                # 1. Copy already-indexed rows straight through (mmap → disk).
                if n_existing and existing_embeddings is not None:
                    dim = int(existing_embeddings.shape[1])
                    _write_npy_header(handle, total, dim)
                    for start in range(0, n_existing, _EMBED_COPY_CHUNK):
                        block = np.ascontiguousarray(
                            existing_embeddings[start : start + _EMBED_COPY_CHUNK],
                            dtype=np.float32,
                        )
                        handle.write(block.tobytes())
                        written += int(len(block))
                        if pbar is not None:
                            pbar.update(int(len(block)))
                        del block

                # 2. Embed genuinely new rows in batches.
                for start in range(0, len(new_ids), _EMBED_BATCH):
                    batch = [int(value) for value in new_ids[start : start + _EMBED_BATCH]]
                    placeholders = ",".join("?" for _ in batch)
                    rows = conn.execute(
                        f"SELECT description FROM {table} "
                        f"WHERE id IN ({placeholders}) ORDER BY id",
                        batch,
                    ).fetchall()
                    vectors = _embed_batch([row["description"] or "" for row in rows])
                    if vectors.size == 0:
                        continue
                    if dim == 0:
                        dim = int(vectors.shape[1]) if vectors.ndim == 2 else 0
                        _write_npy_header(handle, total, dim)
                    block = np.ascontiguousarray(vectors, dtype=np.float32)
                    handle.write(block.tobytes())
                    written += int(len(block))
                    if pbar is not None:
                        pbar.update(len(batch))
                    del block, vectors, rows

            handle.flush()
            os.fsync(handle.fileno())

        if written != total:
            raise RuntimeError(
                f"Embedding write for '{table}' is incomplete ({written}/{total})."
            )

        os.replace(temp_path, dest_emb_path)
        temp_path = None
    finally:
        if pbar is not None:
            pbar.close()
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    merged_ids = new_ids if existing_ids is None else np.concatenate([existing_ids, new_ids])
    _atomic_save_npy(dest_ids_path, np.ascontiguousarray(merged_ids, dtype=np.int32))
    del merged_ids
    gc.collect()

    return dim


def _sync_embeddings(conn, progress: bool, force: bool = False) -> int:
    """Bring the tool embedding file in sync with the database.

    Existing vectors are reused when they are a valid prefix of the current id
    sequence; only genuinely new rows are embedded. When ``force`` is set (or the
    stored state is unusable) the whole matrix is rebuilt.

    Flags are intentionally *not* embedded: they are ranked with FTS5 at query
    time, which keeps indexing fast and avoids a large in-memory vector file.
    """
    embedding_dim = 0

    targets = (
        ("tools", config.tool_embeddings_path(), config.tool_embedding_ids_path(), "Embedding tools"),
    )

    for table, emb_path, ids_path, label in targets:
        db_ids = np.array(
            [int(row["id"]) for row in conn.execute(f"SELECT id FROM {table} ORDER BY id")],
            dtype=np.int32,
        )

        if len(db_ids) == 0:
            _atomic_save_npy(emb_path, np.empty((0, 0), dtype=np.float32))
            _atomic_save_npy(ids_path, np.empty(0, dtype=np.int32))
            continue

        state = None if force else _load_embedding_state(emb_path, ids_path)
        if state is None and not force:
            state = _infer_ids_from_matrix(emb_path, db_ids)

        usable = (
            state is not None
            and len(state[1]) <= len(db_ids)
            and len(state[1]) == state[0].shape[0]
            and np.array_equal(state[1], db_ids[: len(state[1])])
        )

        if usable:
            existing_embeddings, existing_ids = state  # type: ignore[misc]
            new_ids = db_ids[len(existing_ids) :]

            if len(new_ids) == 0:
                # Nothing new: keep the files as-is, but migrate missing id files.
                dim = int(existing_embeddings.shape[1]) if existing_embeddings.ndim == 2 else 0
                if not ids_path.exists():
                    _atomic_save_npy(
                        ids_path, np.ascontiguousarray(existing_ids, dtype=np.int32)
                    )
            else:
                dim = _write_embeddings(
                    dest_emb_path=emb_path,
                    dest_ids_path=ids_path,
                    conn=conn,
                    table=table,
                    existing=(existing_embeddings, existing_ids),
                    new_ids=new_ids,
                    label=label,
                    progress=progress,
                )
        else:
            dim = _write_embeddings(
                dest_emb_path=emb_path,
                dest_ids_path=ids_path,
                conn=conn,
                table=table,
                existing=None,
                new_ids=db_ids,
                label=label,
                progress=progress,
            )

        del state
        gc.collect()

        if dim > 0:
            embedding_dim = dim

    if embedding_dim <= 0:
        raise RuntimeError("Unable to determine embedding dimension from indexed data.")

    return embedding_dim


def _unload_embedding_model() -> None:
    """Release the cached embedding model to free memory."""
    try:
        from sempropos.intelligence.providers import _fastembed as fe

        fe.unload_model()
    except Exception:  # noqa: BLE001
        pass
    gc.collect()


# ============================================================================
# Index cleanup
# ============================================================================


def _clear_index_data() -> None:
    """Wipe existing database and embedding files for a clean rebuild."""
    paths = [
        config.db_path(),
        config.tool_embeddings_path(),
        config.tool_embedding_ids_path(),
        config.flag_embeddings_path(),
        config.flag_embedding_ids_path(),
        config.index_meta_path(),
        config.last_indexed_path(),
    ]
    for path in paths:
        if path.exists():
            with contextlib.suppress(OSError):
                path.unlink()


# ============================================================================
# Main entry point
# ============================================================================


def build_index(progress: bool = True, force: bool = False) -> None:
    """Build or incrementally update the local index.

    Args:
        progress: Show progress bars for parsing and embedding.
        force: Delete the existing index first and rebuild from scratch.
    """
    config.ensure_data_dirs()

    # Read metadata before any wipe so a parser/format change can trigger a full
    # rebuild instead of leaving stale rows in place.
    meta = config.read_index_meta() or {}
    try:
        stored_index_revision = int(meta.get("index_revision") or 0)
    except (TypeError, ValueError):
        stored_index_revision = 0

    full_rebuild = force or stored_index_revision != config.INDEX_REVISION
    if full_rebuild:
        _clear_index_data()

    schema.initialize_database()

    embedding_provider, embedding_provider_config = detect_embedding_provider()
    current_provider = embedding_provider or ""
    current_model = (embedding_provider_config.model if embedding_provider_config else None) or ""

    source = ManSource()
    tools = list(source.discover())
    if not tools:
        raise RuntimeError("No installed tools discovered via man -k")

    with schema.get_connection() as conn:
        if full_rebuild:
            pending = tools
            force_embeddings = True
        else:
            existing = {
                (row["name"], row["section"])
                for row in conn.execute("SELECT name, section FROM tools").fetchall()
            }
            pending = [
                record for record in tools if (record.name, record.section) not in existing
            ]

            # If the embedding backend or representation changed since the last
            # run, existing vectors are meaningless: re-embed even if the parsed
            # rows are still valid.
            stored_provider = str(meta.get("embedding_provider") or "")
            stored_model = str(meta.get("embedding_model") or "")
            try:
                stored_revision = int(meta.get("embedding_revision") or 0)
            except (TypeError, ValueError):
                stored_revision = 0
            force_embeddings = (
                stored_provider != current_provider
                or stored_model != current_model
                or stored_revision != config.EMBEDDING_REVISION
            )

        # Phase 1: parse man pages and insert new tools.
        _ingest_tools(conn, source, pending, progress)

        # Phase 2: bring embeddings in sync (incremental unless forced).
        embedding_dim = _sync_embeddings(conn, progress=progress, force=force_embeddings)

        tool_count = int(conn.execute("SELECT COUNT(*) FROM tools").fetchone()[0])
        flag_count = int(conn.execute("SELECT COUNT(*) FROM flags").fetchone()[0])

    # Phase 3: release the embedding model before writing metadata.
    _unload_embedding_model()

    atomic_write_text(
        config.last_indexed_path(),
        datetime.now(tz=UTC).isoformat(),
    )

    config.write_index_meta(
        embedding_dim=embedding_dim,
        embedding_model=current_model,
        embedding_provider=current_provider,
        tool_count=tool_count,
        flag_count=flag_count,
    )
