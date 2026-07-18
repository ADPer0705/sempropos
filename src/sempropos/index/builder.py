"""Install-time indexing pipeline.

Memory-efficient design:
  - Generator-based man page processing with bounded thread concurrency
  - Batch DB inserts with periodic commits
  - Incremental embedding with per-batch disk flushes
  - Explicit model unloading and garbage collection
"""

from __future__ import annotations

import gc
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import numpy as np
from tqdm import tqdm

from sempropos import __version__, config
from sempropos.index import parser, schema
from sempropos.intelligence.facade import embed_texts, detect_embedding_provider
from sempropos.utils import atomic_write_text

MAN_K_PATTERN = re.compile(r"^([^\s,]+)(?:,\s*[^\s,]+)*\s+\(([^)]+)\)\s+-\s+(.*)$")

# --- Pipeline tuning knobs ---
# How many man page subprocesses to run concurrently
_MAN_READ_WORKERS = min(os.cpu_count() or 4, 8)
# How many parsed tools to accumulate before committing to DB
_DB_COMMIT_BATCH = 50
# How many text descriptions to embed in one model call
_EMBED_BATCH = 16
# How many DB rows to read at a time during the embedding phase
_EMBED_CURSOR_CHUNK = 256


def _run_man_k(section: str) -> list[tuple[str, int, str]]:
    try:
        result = subprocess.run(
            ["man", "-k", ".", "-s", section],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise RuntimeError("Failed to execute man -k") from exc

    if result.returncode != 0:
        return []

    rows: list[tuple[str, int, str]] = []
    for line in result.stdout.splitlines():
        match = MAN_K_PATTERN.match(line.strip())
        if not match:
            continue

        name, section_raw, description = match.groups()
        section_match = re.search(r"\d+", section_raw)
        if not section_match:
            continue

        section_num = int(section_match.group(0))
        if section_num not in (1, 8):
            continue

        rows.append((name, section_num, description.strip()))

    return rows


def _discover_tools() -> list[tuple[str, int, str]]:
    combined = _run_man_k("1")
    try:
        combined.extend(_run_man_k("8"))
    except RuntimeError:
        pass

    seen: set[tuple[str, int]] = set()
    deduped: list[tuple[str, int, str]] = []
    
    for name, section, description in combined:
        if (name, section) in seen:
            continue
        if not shutil.which(name):
            continue
        seen.add((name, section))
        deduped.append((name, section, description))

    return deduped


def _read_man_page(tool: str, section: int) -> str:
    result = subprocess.run(
        ["man", "-P", "cat", str(section), tool],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(stderr or f"man page lookup failed for {tool}({section})")
    return result.stdout


# ============================================================================
# Generator-based man page processing
# ============================================================================


def _iter_parsed_tools(
    pending: list[tuple[str, int, str]],
    progress: bool,
) -> Generator[tuple[str, int, str, parser.ParsedManPage], None, None]:
    """Yield (name, section, description, parsed) with bounded concurrency.

    Only `_MAN_READ_WORKERS` raw man page strings exist in memory at a time.
    Each is parsed and yielded immediately, then garbage-collected.
    """
    pbar = None
    if progress:
        pbar = tqdm(total=len(pending), desc="Indexing", unit="tool")

    with ThreadPoolExecutor(max_workers=_MAN_READ_WORKERS) as pool:
        futures = {
            pool.submit(_read_man_page, name, section): (name, section, description)
            for name, section, description in pending
        }

        for future in as_completed(futures):
            name, section, description = futures[future]
            if pbar is not None:
                pbar.set_postfix_str(name)

            try:
                raw_page = future.result()
                parsed = parser.parse_man_page(raw_page)
                yield name, section, description, parsed
            except Exception:  # noqa: BLE001
                pass  # skip tools whose man pages can't be read/parsed
            finally:
                if pbar is not None:
                    pbar.update(1)

    if pbar is not None:
        pbar.close()


# ============================================================================
# Batch DB insertion
# ============================================================================


def _insert_tool(
    conn,
    name: str,
    section: int,
    description: str,
    parsed: parser.ParsedManPage,
) -> None:
    cursor = conn.execute(
        """
        INSERT INTO tools(name, section, description, synopsis)
        VALUES (?, ?, ?, ?)
        """,
        (name, section, description, parsed.get("synopsis") or None),
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
    pending: list[tuple[str, int, str]],
    progress: bool,
) -> None:
    """Parse man pages via generator and batch-commit to DB."""
    batch_count = 0

    for name, section, description, parsed in _iter_parsed_tools(pending, progress):
        try:
            _insert_tool(conn, name, section, description, parsed)
            batch_count += 1
        except Exception:  # noqa: BLE001
            conn.rollback()
            continue

        if batch_count >= _DB_COMMIT_BATCH:
            conn.commit()
            batch_count = 0

    # Commit any remaining partial batch
    if batch_count > 0:
        conn.commit()


# ============================================================================
# Incremental embedding
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
    """Embed a single batch of texts (already capped to _EMBED_BATCH size)."""
    if not texts:
        return np.empty((0, 0), dtype=np.float32)

    safe_batch = [text[:2500] for text in texts]
    return embed_texts(safe_batch)  # provider already returns float32


def _stream_embed_to_disk(
    conn,
    dest_path: Path,
    sql: str,
    text_col: str,
    total: int,
    progress_label: str,
    progress: bool,
) -> tuple[int, np.ndarray | None]:
    """Stream rows from *sql*, embed in batches, write result to *dest_path*.

    Returns (embedding_dim, ids_array_or_None).
    The ids_array is only returned when the SQL selects an ``id`` column
    (used for flag embeddings).

    The key property is that only one cursor-chunk worth of vectors exists
    in RAM at any time — as soon as all chunks are gathered they are
    concatenated, saved to disk, and freed.
    """
    chunks: list[np.ndarray] = []
    id_chunks: list[np.ndarray] = []
    has_ids = False
    embedding_dim = 0

    pbar = tqdm(total=total, desc=progress_label, unit="text") if progress else None

    offset = 0
    while True:
        rows = conn.execute(sql, (_EMBED_CURSOR_CHUNK, offset)).fetchall()
        if not rows:
            break

        texts = [row[text_col] for row in rows]

        # Detect if the query includes an 'id' column (flag embeddings)
        if offset == 0:
            has_ids = "id" in rows[0].keys()

        if has_ids:
            ids = np.array([row["id"] for row in rows], dtype=np.int32)

        for i in range(0, len(texts), _EMBED_BATCH):
            batch = texts[i : i + _EMBED_BATCH]
            vectors = _embed_batch(batch)
            if vectors.size > 0:
                chunks.append(vectors)
                if has_ids:
                    id_chunks.append(ids[i : i + len(batch)])
                if embedding_dim == 0 and vectors.ndim == 2:
                    embedding_dim = vectors.shape[1]
            del vectors
            if pbar is not None:
                pbar.update(len(batch))

        offset += _EMBED_CURSOR_CHUNK
        del rows, texts
        gc.collect()

    if pbar is not None:
        pbar.close()

    # ---- Concatenate and flush to disk immediately ----
    merged = np.vstack(chunks) if chunks else np.empty((0, 0), dtype=np.float32)
    del chunks
    gc.collect()

    _atomic_save_npy(dest_path, merged)
    del merged
    gc.collect()

    merged_ids: np.ndarray | None = None
    if has_ids and id_chunks:
        merged_ids = np.concatenate(id_chunks)
        del id_chunks
        gc.collect()

    return embedding_dim, merged_ids


def _rebuild_embeddings_incremental(conn, progress: bool) -> int:
    """Build tool and flag embeddings with minimal peak memory.

    Each embedding type is computed, written to disk, and freed BEFORE
    the next type starts.  This means peak memory is roughly:
        ONNX model  +  one type's accumulated vectors

    instead of the old approach where ALL vectors lived in RAM
    simultaneously.
    """
    embedding_dim = 0

    # ---- Phase A: Tool embeddings → disk ----
    total_tools = conn.execute("SELECT COUNT(*) FROM tools").fetchone()[0]
    if total_tools > 0:
        dim, _ = _stream_embed_to_disk(
            conn,
            dest_path=config.tool_embeddings_path(),
            sql="SELECT description FROM tools ORDER BY id LIMIT ? OFFSET ?",
            text_col="description",
            total=total_tools,
            progress_label="Embedding tools",
            progress=progress,
        )
        if dim > 0:
            embedding_dim = dim
    else:
        _atomic_save_npy(
            config.tool_embeddings_path(), np.empty((0, 0), dtype=np.float32)
        )

    # ---- Phase B: Flag embeddings → disk ----
    total_flags = conn.execute("SELECT COUNT(*) FROM flags").fetchone()[0]
    if total_flags > 0:
        dim, flag_ids = _stream_embed_to_disk(
            conn,
            dest_path=config.flag_embeddings_path(),
            sql="SELECT id, description FROM flags ORDER BY id LIMIT ? OFFSET ?",
            text_col="description",
            total=total_flags,
            progress_label="Embedding flags",
            progress=progress,
        )
        if dim > 0 and embedding_dim == 0:
            embedding_dim = dim

        if flag_ids is not None:
            _atomic_save_npy(config.flag_embedding_ids_path(), flag_ids)
            del flag_ids
            gc.collect()
        else:
            _atomic_save_npy(
                config.flag_embedding_ids_path(), np.empty(0, dtype=np.int32)
            )
    else:
        _atomic_save_npy(
            config.flag_embeddings_path(), np.empty((0, 0), dtype=np.float32)
        )
        _atomic_save_npy(
            config.flag_embedding_ids_path(), np.empty(0, dtype=np.int32)
        )

    # ---- Validate ----
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
        config.flag_embeddings_path(),
        config.flag_embedding_ids_path(),
        config.index_meta_path(),
        config.last_indexed_path(),
    ]
    for path in paths:
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass


# ============================================================================
# Main entry point
# ============================================================================


def build_index(progress: bool = True, force: bool = False) -> None:
    config.ensure_data_dirs()

    if force:
        _clear_index_data()

    schema.initialize_database()

    embedding_provider, embedding_provider_config = detect_embedding_provider()

    tools = _discover_tools()
    if not tools:
        raise RuntimeError("No installed tools discovered via man -k")

    with schema.get_connection() as conn:
        meta = config.read_index_meta() or {}

        existing = {
            (row["name"], row["section"]) for row in conn.execute("SELECT name, section FROM tools").fetchall()
        }

        existing_version = str(meta.get("sempropos_version") or "")
        
        # Hard check for version shifts or missing metadata to trigger full rebuilds
        if existing and (not existing_version or existing_version != __version__):
            raise RuntimeError(
                config.VERSION_UPDATE_NOTICE_TEMPLATE.format(version=__version__)
            )

        pending = [row for row in tools if (row[0], row[1]) not in existing]

        # Phase 1: Parse man pages and insert into DB (generator + batch commits)
        _ingest_tools(conn, pending, progress)

        # Phase 2: Build embeddings incrementally (cursor-based streaming)
        embedding_dim = _rebuild_embeddings_incremental(conn, progress=progress)

    # Phase 3: Cleanup — release the embedding model from memory
    _unload_embedding_model()

    atomic_write_text(
        config.last_indexed_path(),
        datetime.now(tz=timezone.utc).isoformat(),
    )
    
    config.write_index_meta(
        embedding_dim=embedding_dim,
        embedding_model=(embedding_provider_config.model if embedding_provider_config else None) or "",
        embedding_provider=(embedding_provider if embedding_provider else None) or "",
    )