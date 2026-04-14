"""Install-time indexing pipeline."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from shutil import disk_usage

import numpy as np
from tqdm import tqdm

from sempropos import __version__, config
from sempropos.index import embedder, parser, schema
from sempropos.intelligence.config import load_settings
from sempropos.intelligence.contracts import EmbeddingProviderName

LOGGER = logging.getLogger(__name__)
MAN_K_PATTERN = re.compile(r"^([^\s,]+)(?:,\s*[^\s,]+)*\s+\(([^)]+)\)\s+-\s+(.*)$")


def _run_man_k(section: str) -> list[tuple[str, int, str]]:
    """Run man -k for a section and parse rows into tool metadata tuples."""
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
        stderr = result.stderr.strip()
        raise RuntimeError(f"man -k failed for section {section}: {stderr}")

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
    """Discover section 1 and 8 tools and deduplicate by command name."""
    combined = _run_man_k("1")
    try:
        combined.extend(_run_man_k("8"))
    except RuntimeError:
        # Section 8 may not exist on all systems.
        pass

    seen: set[str] = set()
    deduped: list[tuple[str, int, str]] = []
    for name, section, description in combined:
        if name in seen:
            continue
        seen.add(name)
        deduped.append((name, section, description))

    return deduped


def _read_man_page(tool: str) -> str:
    """Read a tool's man page as plain text using man -P cat."""
    result = subprocess.run(
        ["man", "-P", "cat", tool],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(stderr or f"man page lookup failed for {tool}")
    return result.stdout


def _insert_tool(conn, name: str, section: int, description: str, parsed: dict) -> None:
    """Insert one tool and its parsed flags/examples into SQLite."""
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


def _embed_texts(
    texts: list[str],
    model_name: str,
    progress: bool,
    provider_name: EmbeddingProviderName | None,
) -> np.ndarray:
    """Embed text rows in batches and return normalized vectors."""
    try:
        expected_dim = config.get_embedding_dim(model_name)
    except KeyError as exc:
        raise RuntimeError(config.MISMATCH_UPDATE_NOTICE) from exc
    if not texts:
        return np.empty((0, expected_dim), dtype=np.float32)

    all_batches: list[np.ndarray] = []

    iterator = range(0, len(texts), 256)
    if progress:
        iterator = tqdm(iterator, desc="Embedding", unit="batch")

    for start in iterator:
        end = start + 256
        batch = texts[start:end]
        encoded = embedder.embed_texts(
            batch,
            model_name=model_name,
            provider_name=provider_name,
        )
        all_batches.append(encoded)

    vectors = (
        np.vstack(all_batches)
        if all_batches
        else np.empty((0, expected_dim), dtype=np.float32)
    )
    return vectors.astype(np.float32)


def _atomic_save_npy(path: Path, values: np.ndarray) -> None:
    """Persist an ndarray atomically to avoid partial artifact writes."""
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


def _rebuild_embeddings(
    conn,
    model_name: str,
    progress: bool,
    provider_name: EmbeddingProviderName | None,
) -> int:
    """Recompute and persist tool and flag embedding arrays from SQLite."""
    tool_rows = conn.execute("SELECT id, description FROM tools ORDER BY id").fetchall()
    flag_rows = conn.execute("SELECT id, description FROM flags ORDER BY id").fetchall()

    tool_texts = [row["description"] for row in tool_rows]
    flag_texts = [row["description"] for row in flag_rows]

    tool_vectors = _embed_texts(
        tool_texts,
        model_name=model_name,
        progress=progress,
        provider_name=provider_name,
    )
    flag_vectors = _embed_texts(
        flag_texts,
        model_name=model_name,
        progress=progress,
        provider_name=provider_name,
    )
    flag_ids = np.array([row["id"] for row in flag_rows], dtype=np.int32)

    embedding_dim = 0
    if tool_vectors.ndim == 2 and tool_vectors.shape[1] > 0:
        embedding_dim = int(tool_vectors.shape[1])
    elif flag_vectors.ndim == 2 and flag_vectors.shape[1] > 0:
        embedding_dim = int(flag_vectors.shape[1])

    _atomic_save_npy(config.tool_embeddings_path(), tool_vectors)
    _atomic_save_npy(config.flag_embeddings_path(), flag_vectors)
    _atomic_save_npy(config.flag_embedding_ids_path(), flag_ids)
    return embedding_dim


def _select_embedding_model(force_floor_embedding: bool) -> str:
    """Select embedding model according to install policy."""
    if force_floor_embedding:
        print(config.EMBEDDING_FLOOR_NOTICE)
        return config.EMBEDDING_FLOOR_MODEL

    available = disk_usage(config.data_dir()).free
    if available < config.EMBEDDING_LOW_DISK_THRESHOLD_BYTES:
        print(config.EMBEDDING_FLOOR_NOTICE)
        return config.EMBEDDING_FLOOR_MODEL

    return config.EMBEDDING_PRIMARY_MODEL


def build_index(
    progress: bool = True,
    *,
    force_floor_embedding: bool = False,
    synthesis_model: str | None = None,
    embedding_provider: EmbeddingProviderName | None = None,
    embedding_model: str | None = None,
) -> str:
    """Build or refresh the local SQLite and embedding index artifacts."""
    config.ensure_data_dirs()
    schema.initialize()
    settings = load_settings()
    selected_embedding_provider = embedding_provider or settings.embedding_provider
    preferred_embedding_model_name = (
        embedding_model
        or settings.embedding_model
        or _select_embedding_model(force_floor_embedding=force_floor_embedding)
    )

    tools = _discover_tools()
    if not tools:
        raise RuntimeError("No man pages discovered from man -k")

    with schema.get_connection() as conn:
        embedding_model_name = preferred_embedding_model_name
        meta = config.read_index_meta() or {}

        existing = {
            row["name"] for row in conn.execute("SELECT name FROM tools").fetchall()
        }

        existing_version = str(meta.get("sempropos_version") or "")
        if existing and existing_version and existing_version != __version__:
            raise RuntimeError(
                config.VERSION_UPDATE_NOTICE_TEMPLATE.format(version=__version__)
            )

        # Keep resumable rebuilds deterministic when no explicit model is configured.
        existing_model_name = str(meta.get("embedding_model") or "")
        if (
            existing
            and not embedding_model
            and not settings.embedding_model
            and existing_model_name
        ):
            try:
                config.get_embedding_dim(existing_model_name)
            except KeyError as exc:
                raise RuntimeError(config.MISMATCH_UPDATE_NOTICE) from exc
            embedding_model_name = existing_model_name

        pending = [row for row in tools if row[0] not in existing]

        iterator = pending
        pbar = None
        if progress:
            pbar = tqdm(total=len(pending), desc="Indexing", unit="tool")

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(_read_man_page, name): (name, section, description)
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
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    LOGGER.warning("Skipping %s: %s", name, exc)
                finally:
                    if pbar is not None:
                        pbar.update(1)

        if pbar is not None:
            pbar.close()

        embedding_dim = _rebuild_embeddings(
            conn,
            model_name=embedding_model_name,
            progress=progress,
            provider_name=selected_embedding_provider,
        )

    config.last_indexed_path().write_text(
        datetime.now(tz=timezone.utc).isoformat(),
        encoding="utf-8",
    )
    config.write_index_meta(
        embedding_model=embedding_model_name,
        embedding_dim=embedding_dim,
        embedding_provider=selected_embedding_provider,
        synthesis_model=synthesis_model
        or config.synthesis_model_spec("primary")["filename"],
    )
    return embedding_model_name
