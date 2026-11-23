"""Single-connection subcommand, flag, and example retrieval for candidate tools."""

from __future__ import annotations

import json
import sqlite3
from typing import cast

from sempropos.index.schema import get_connection
from sempropos.model.spec import (
    CandidateContext,
    ExampleSpec,
    FlagSpec,
    SourceKind,
    SubcommandSpec,
)
from sempropos.retrieval.bm25 import build_fts_query
from sempropos.retrieval.expansion import expand_query


def _as_source(value: str | None) -> SourceKind:
    allowed = {
        "man_roff",
        "man_text",
        "tldr",
        "completion_fish",
        "completion_zsh",
        "help_probe",
    }
    if value in allowed:
        return cast(SourceKind, value)
    return "man_roff"


def _row_to_flag(row: sqlite3.Row) -> FlagSpec:
    return FlagSpec(
        short=row["short_flag"],
        long=row["long_flag"],
        takes_value=bool(row["takes_value"]),
        value_hint=row["value_hint"],
        repeatable=bool(row["repeatable"]),
        description=str(row["description"] or ""),
        section=row["section"],
        source=_as_source(row["source"]),
    )


def get_relevant_subcommands(
    conn: sqlite3.Connection,
    tool_id: int,
    query: str,
    *,
    top_k: int = 4,
) -> list[tuple[int, SubcommandSpec]]:
    """Return ``[(subcommand_id, SubcommandSpec)]`` most relevant to ``query``."""
    if top_k <= 0:
        return []

    fts_query = build_fts_query(expand_query(query))
    rows: list[sqlite3.Row] = []
    if fts_query:
        try:
            rows = conn.execute(
                """
                SELECT s.id, s.name, s.summary, s.synopsis_json, s.source
                FROM subcommands_fts
                JOIN subcommands s ON s.id = subcommands_fts.rowid
                WHERE subcommands_fts MATCH ? AND s.tool_id = ?
                ORDER BY bm25(subcommands_fts, 4.0, 1.0)
                LIMIT ?
                """,
                (fts_query, tool_id, top_k),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

    if not rows:
        rows = conn.execute(
            """
            SELECT id, name, summary, synopsis_json, source
            FROM subcommands
            WHERE tool_id = ?
            ORDER BY id
            LIMIT ?
            """,
            (tool_id, top_k),
        ).fetchall()

    result: list[tuple[int, SubcommandSpec]] = []
    for row in rows:
        try:
            syn = tuple(json.loads(row["synopsis_json"] or "[]"))
        except json.JSONDecodeError:
            syn = ()
        result.append(
            (
                int(row["id"]),
                SubcommandSpec(
                    name=str(row["name"]),
                    summary=str(row["summary"] or ""),
                    synopsis=syn,
                    source=_as_source(row["source"]),
                ),
            )
        )
    return result


def get_relevant_flags(
    tool_id: int,
    query: str,
    top_k: int = 8,
    *,
    conn: sqlite3.Connection | None = None,
    subcommand_ids: list[int] | None = None,
) -> list[FlagSpec]:
    """Return the flags for ``tool_id`` most relevant to ``query``."""
    if top_k <= 0:
        return []

    def _run(c: sqlite3.Connection) -> list[FlagSpec]:
        fts_query = build_fts_query(expand_query(query))
        rows: list[sqlite3.Row] = []
        if fts_query:
            try:
                rows = c.execute(
                    """
                    SELECT f.short_flag, f.long_flag, f.takes_value, f.value_hint,
                           f.repeatable, f.section, f.description, f.source, f.subcommand_id
                    FROM flags_fts
                    JOIN flags f ON f.id = flags_fts.rowid
                    WHERE flags_fts MATCH ? AND f.tool_id = ?
                    ORDER BY bm25(flags_fts, 3.0, 1.0)
                    LIMIT ?
                    """,
                    (fts_query, tool_id, top_k),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []

        if len(rows) < top_k and subcommand_ids:
            placeholders = ",".join("?" for _ in subcommand_ids)
            sub_rows = c.execute(
                f"""
                SELECT short_flag, long_flag, takes_value, value_hint,
                       repeatable, section, description, source, subcommand_id
                FROM flags
                WHERE tool_id = ? AND subcommand_id IN ({placeholders})
                ORDER BY id
                LIMIT ?
                """,
                (tool_id, *subcommand_ids, top_k - len(rows)),
            ).fetchall()
            rows = [*rows, *sub_rows]

        if not rows:
            rows = c.execute(
                """
                SELECT short_flag, long_flag, takes_value, value_hint,
                       repeatable, section, description, source, subcommand_id
                FROM flags
                WHERE tool_id = ? AND subcommand_id IS NULL
                ORDER BY id
                LIMIT ?
                """,
                (tool_id, top_k),
            ).fetchall()

        deduped: list[FlagSpec] = []
        seen: set[tuple[str | None, str | None]] = set()
        for row in rows:
            flag = _row_to_flag(row)
            if flag.canonical_key not in seen:
                seen.add(flag.canonical_key)
                deduped.append(flag)
        return deduped[:top_k]

    if conn is not None:
        return _run(conn)
    with get_connection(read_only=True) as managed:
        return _run(managed)


def get_relevant_examples(
    conn: sqlite3.Connection,
    tool_id: int,
    query: str,
    *,
    top_k: int = 4,
    subcommand_ids: list[int] | None = None,
) -> list[ExampleSpec]:
    """Return task-relevant examples for ``tool_id`` (prioritizing tldr + matched subcommands)."""
    if top_k <= 0:
        return []

    fts_query = build_fts_query(expand_query(query))
    rows: list[sqlite3.Row] = []
    if fts_query:
        try:
            rows = conn.execute(
                """
                SELECT e.command, e.description, e.source, s.name AS sub_name
                FROM examples_fts
                JOIN examples e ON e.id = examples_fts.rowid
                LEFT JOIN subcommands s ON s.id = e.subcommand_id
                WHERE examples_fts MATCH ? AND e.tool_id = ?
                ORDER BY CASE e.source WHEN 'tldr' THEN 0 ELSE 1 END,
                         bm25(examples_fts, 1.0, 2.5)
                LIMIT ?
                """,
                (fts_query, tool_id, top_k),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

    if len(rows) < top_k and subcommand_ids:
        placeholders = ",".join("?" for _ in subcommand_ids)
        extra = conn.execute(
            f"""
            SELECT e.command, e.description, e.source, s.name AS sub_name
            FROM examples e
            LEFT JOIN subcommands s ON s.id = e.subcommand_id
            WHERE e.tool_id = ? AND e.subcommand_id IN ({placeholders})
            ORDER BY CASE e.source WHEN 'tldr' THEN 0 ELSE 1 END, e.id
            LIMIT ?
            """,
            (tool_id, *subcommand_ids, top_k),
        ).fetchall()
        rows = [*rows, *extra]

    if not rows:
        rows = conn.execute(
            """
            SELECT e.command, e.description, e.source, s.name AS sub_name
            FROM examples e
            LEFT JOIN subcommands s ON s.id = e.subcommand_id
            WHERE e.tool_id = ?
            ORDER BY CASE e.source WHEN 'tldr' THEN 0 ELSE 1 END, e.id
            LIMIT ?
            """,
            (tool_id, top_k),
        ).fetchall()

    deduped: list[ExampleSpec] = []
    seen_cmds: set[str] = set()
    for row in rows:
        cmd = str(row["command"]).strip()
        if not cmd or cmd in seen_cmds:
            continue
        seen_cmds.add(cmd)
        deduped.append(
            ExampleSpec(
                command=cmd,
                description=str(row["description"] or ""),
                source=_as_source(row["source"]),
                subcommand=row["sub_name"],
            )
        )
        if len(deduped) >= top_k:
            break
    return deduped


def load_candidates(
    conn: sqlite3.Connection,
    ranked: list[tuple[int, float]],
    query: str,
    *,
    max_flags: int = 8,
    max_examples: int = 4,
    max_subcommands: int = 4,
) -> list[CandidateContext]:
    """Hydrate ranked ``(tool_id, score)`` pairs over a single shared SQLite connection."""
    candidates: list[CandidateContext] = []
    for tool_id, score in ranked:
        row = conn.execute(
            """
            SELECT id, name, summary, description, category, synopsis_json, aliases_text
            FROM tools
            WHERE id = ?
            """,
            (tool_id,),
        ).fetchone()
        if row is None:
            continue

        try:
            synopsis = tuple(json.loads(row["synopsis_json"] or "[]"))
        except json.JSONDecodeError:
            synopsis = ()

        aliases = tuple(str(row["aliases_text"] or "").split())
        source_rows = conn.execute(
            "SELECT DISTINCT source FROM tool_sources WHERE tool_id = ? ORDER BY source",
            (tool_id,),
        ).fetchall()
        sources = tuple(str(sr["source"]) for sr in source_rows)

        sub_pairs = get_relevant_subcommands(conn, tool_id, query, top_k=max_subcommands)
        sub_ids = [sid for sid, _ in sub_pairs]
        subcommands = tuple(spec for _, spec in sub_pairs)
        flags = tuple(
            get_relevant_flags(
                tool_id, query, top_k=max_flags, conn=conn, subcommand_ids=sub_ids or None
            )
        )
        examples = tuple(
            get_relevant_examples(
                conn, tool_id, query, top_k=max_examples, subcommand_ids=sub_ids or None
            )
        )

        candidates.append(
            CandidateContext(
                tool_id=int(row["id"]),
                name=str(row["name"]),
                summary=str(row["summary"]),
                description=str(row["description"] or ""),
                category=row["category"],
                aliases=aliases,
                synopsis=synopsis,
                subcommands=subcommands,
                flags=flags,
                examples=examples,
                sources=sources,
                score=float(score),
            )
        )
    return candidates
