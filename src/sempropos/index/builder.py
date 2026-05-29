"""Install-time indexing pipeline."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from tqdm import tqdm

from sempropos import __version__, config
from sempropos.index import parser, schema
from sempropos.intelligence.facade import embed_texts, detect_embedding_provider
from sempropos.utils import atomic_write_text

MAN_K_PATTERN = re.compile(r"^([^\s,]+)(?:,\s*[^\s,]+)*\s+\(([^)]+)\)\s+-\s+(.*)$")


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


def _embed_texts(texts: list[str], progress: bool) -> np.ndarray:
    if not texts:
        return np.empty((0, 0), dtype=np.float32)

    all_batches: list[np.ndarray] = []
    
    BATCH_SIZE = 32
    iterator = range(0, len(texts), BATCH_SIZE)
    
    if progress:
        iterator = tqdm(iterator, desc="Embedding", unit="batch")

    for start in iterator:
        batch = texts[start : start + BATCH_SIZE]
        safe_batch = [text[:2500] for text in batch]

        encoded = embed_texts(safe_batch)  # Calls the facade
        all_batches.append(encoded)

    vectors = (
        np.vstack(all_batches) if all_batches else np.empty((0, 0), dtype=np.float32)
    )
    return vectors.astype(np.float32)


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


def _rebuild_embeddings(conn, progress: bool) -> int:
    tool_rows = conn.execute("SELECT id, description FROM tools ORDER BY id").fetchall()
    flag_rows = conn.execute("SELECT id, description FROM flags ORDER BY id").fetchall()

    tool_texts = [row["description"] for row in tool_rows]
    flag_texts = [row["description"] for row in flag_rows]

    tool_vectors = _embed_texts(tool_texts, progress=progress)
    flag_vectors = _embed_texts(flag_texts, progress=progress)
    flag_ids = np.array([row["id"] for row in flag_rows], dtype=np.int32)

    embedding_dim = 0
    if tool_vectors.ndim == 2 and tool_vectors.shape[1] > 0:
        embedding_dim = int(tool_vectors.shape[1])
    elif flag_vectors.ndim == 2 and flag_vectors.shape[1] > 0:
        embedding_dim = int(flag_vectors.shape[1])

    if embedding_dim <= 0:
        raise RuntimeError("Unable to determine embedding dimension from indexed data.")

    _atomic_save_npy(config.tool_embeddings_path(), tool_vectors)
    _atomic_save_npy(config.flag_embeddings_path(), flag_vectors)
    _atomic_save_npy(config.flag_embedding_ids_path(), flag_ids)
    return embedding_dim

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

        pbar = None
        if progress:
            pbar = tqdm(total=len(pending), desc="Indexing", unit="tool")

        workers = os.cpu_count() or 4
        with ThreadPoolExecutor(max_workers=workers) as pool:
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
                    _insert_tool(conn, name, section, description, parsed)
                    conn.commit()
                except Exception:  # noqa: BLE001
                    conn.rollback()
                finally:
                    if pbar is not None:
                        pbar.update(1)

        if pbar is not None:
            pbar.close()

        embedding_dim = _rebuild_embeddings(conn, progress=progress)

    atomic_write_text(
        config.last_indexed_path(),
        datetime.now(tz=timezone.utc).isoformat(),
    )
    
    config.write_index_meta(
        embedding_dim=embedding_dim,
        embedding_model=(embedding_provider_config.model if embedding_provider_config else None) or "",
        embedding_provider=(embedding_provider if embedding_provider else None) or "",
    )