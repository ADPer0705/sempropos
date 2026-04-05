"""Install-time indexing pipeline."""

from __future__ import annotations

import logging
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import numpy as np
from sentence_transformers import SentenceTransformer
from tqdm import tqdm

from sempropos import config
from sempropos.index import schema
from sempropos.index import parser


LOGGER = logging.getLogger(__name__)
MAN_K_PATTERN = re.compile(r"^([^\s,]+)(?:,\s*[^\s,]+)*\s+\(([^)]+)\)\s+-\s+(.*)$")

_MODEL: SentenceTransformer | None = None


def _get_embedder() -> SentenceTransformer:
    """Return a cached sentence-transformer model instance."""
    global _MODEL
    if _MODEL is None:
        _MODEL = SentenceTransformer(config.EMBEDDING_MODEL_NAME)
    return _MODEL


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


def _normalize(vectors: np.ndarray) -> np.ndarray:
    """L2-normalize embedding vectors while preserving float32 dtype."""
    if vectors.size == 0:
        return vectors.astype(np.float32)

    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (vectors / norms).astype(np.float32)


def _embed_texts(texts: list[str], progress: bool) -> np.ndarray:
    """Embed text rows in batches and return normalized vectors."""
    if not texts:
        return np.empty((0, config.EMBEDDING_DIM), dtype=np.float32)

    model = _get_embedder()
    all_batches: list[np.ndarray] = []

    iterator = range(0, len(texts), 256)
    if progress:
        iterator = tqdm(iterator, desc="Embedding", unit="batch")

    for start in iterator:
        end = start + 256
        batch = texts[start:end]
        encoded = model.encode(
            batch,
            batch_size=256,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        all_batches.append(encoded.astype(np.float32))

    vectors = np.vstack(all_batches) if all_batches else np.empty((0, config.EMBEDDING_DIM))
    return _normalize(vectors)


def _rebuild_embeddings(conn, progress: bool) -> None:
    """Recompute and persist tool and flag embedding arrays from SQLite."""
    tool_rows = conn.execute("SELECT id, description FROM tools ORDER BY id").fetchall()
    flag_rows = conn.execute("SELECT id, description FROM flags ORDER BY id").fetchall()

    tool_texts = [row["description"] for row in tool_rows]
    flag_texts = [row["description"] for row in flag_rows]

    tool_vectors = _embed_texts(tool_texts, progress=progress)
    flag_vectors = _embed_texts(flag_texts, progress=progress)
    flag_ids = np.array([row["id"] for row in flag_rows], dtype=np.int32)

    np.save(config.tool_embeddings_path(), tool_vectors)
    np.save(config.flag_embeddings_path(), flag_vectors)
    np.save(config.flag_embedding_ids_path(), flag_ids)


def build_index(progress: bool = True) -> None:
    """Build or refresh the local SQLite and embedding index artifacts."""
    config.ensure_data_dirs()
    schema.initialize()

    tools = _discover_tools()
    if not tools:
        raise RuntimeError("No man pages discovered from man -k")

    with schema.get_connection() as conn:
        existing = {
            row["name"] for row in conn.execute("SELECT name FROM tools").fetchall()
        }
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

        _rebuild_embeddings(conn, progress=progress)

    config.last_indexed_path().write_text(
        datetime.now(tz=timezone.utc).isoformat(),
        encoding="utf-8",
    )
