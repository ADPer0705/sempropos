"""Repository layer for reading and writing canonical ToolSpecs in SQLite v3."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import cast

from sempropos.model.spec import (
    ExampleSpec,
    FlagSpec,
    IndexStats,
    PositionalSpec,
    SourceKind,
    SourceProvenance,
    SubcommandSpec,
    ToolSpec,
)


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


def _loads_str_tuple(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item) for item in parsed if isinstance(item, str) and item.strip())


def upsert_tool(conn: sqlite3.Connection, spec: ToolSpec) -> tuple[int, bool]:
    """Insert or update a ``ToolSpec`` incrementally using its content hash.

    Returns:
        ``(tool_id, changed)`` where ``changed`` is False when an existing row
        already matches ``spec.content_hash()`` (skipping redundant writes and
        re-embedding).
    """
    digest = spec.content_hash()
    now = datetime.now(tz=UTC).isoformat()

    existing = conn.execute(
        "SELECT id, content_hash FROM tools WHERE name = ?",
        (spec.name,),
    ).fetchone()

    if existing is not None and str(existing["content_hash"]) == digest:
        return int(existing["id"]), False

    synopsis_json = json.dumps(list(spec.synopsis), ensure_ascii=False)
    aliases_text = spec.aliases_text()
    tags_text = spec.tags_text()
    subcommands_text = spec.subcommands_text()
    examples_text = spec.examples_text()
    flags_text = spec.flags_text()

    if existing is None:
        cursor = conn.execute(
            """
            INSERT INTO tools(
                name, binary_path, section, summary, description, category,
                tags_text, synopsis_json, aliases_text, subcommands_text,
                examples_text, flags_text, content_hash, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                spec.name,
                spec.binary_path,
                spec.section,
                spec.summary,
                spec.description,
                spec.category,
                tags_text,
                synopsis_json,
                aliases_text,
                subcommands_text,
                examples_text,
                flags_text,
                digest,
                now,
            ),
        )
        assert cursor.lastrowid is not None
        tool_id = int(cursor.lastrowid)
    else:
        tool_id = int(existing["id"])
        conn.execute(
            """
            UPDATE tools
            SET binary_path = ?, section = ?, summary = ?, description = ?,
                category = ?, tags_text = ?, synopsis_json = ?, aliases_text = ?,
                subcommands_text = ?, examples_text = ?, flags_text = ?,
                content_hash = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                spec.binary_path,
                spec.section,
                spec.summary,
                spec.description,
                spec.category,
                tags_text,
                synopsis_json,
                aliases_text,
                subcommands_text,
                examples_text,
                flags_text,
                digest,
                now,
                tool_id,
            ),
        )
        # Clear child rows before re-inserting updated structured children.
        for child_table in (
            "aliases",
            "flags",
            "examples",
            "subcommands",
            "positionals",
            "see_also",
            "tool_sources",
        ):
            conn.execute(f"DELETE FROM {child_table} WHERE tool_id = ?", (tool_id,))

    _insert_children(conn, tool_id, spec)
    return tool_id, True


def _insert_children(conn: sqlite3.Connection, tool_id: int, spec: ToolSpec) -> None:
    for alias in spec.aliases:
        if alias and alias != spec.name:
            conn.execute(
                "INSERT OR REPLACE INTO aliases(alias, tool_id) VALUES (?, ?)",
                (alias, tool_id),
            )

    sub_id_map: dict[str, int] = {}
    for sub in spec.subcommands:
        cur = conn.execute(
            """
            INSERT OR REPLACE INTO subcommands(tool_id, name, summary, synopsis_json, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                tool_id,
                sub.name,
                sub.summary,
                json.dumps(list(sub.synopsis), ensure_ascii=False),
                sub.source,
            ),
        )
        assert cur.lastrowid is not None
        sub_id = int(cur.lastrowid)
        sub_id_map[sub.name] = sub_id

        for flag in sub.flags:
            _insert_flag(conn, tool_id, sub_id, flag)
        for example in sub.examples:
            _insert_example(conn, tool_id, sub_id, example)

    for flag in spec.flags:
        _insert_flag(conn, tool_id, None, flag)

    for idx, pos in enumerate(spec.positionals):
        conn.execute(
            """
            INSERT INTO positionals(tool_id, position, name, required, variadic, description)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                tool_id,
                idx,
                pos.name,
                1 if pos.required else 0,
                1 if pos.variadic else 0,
                pos.description,
            ),
        )

    for example in spec.examples:
        linked_sub_id = sub_id_map.get(example.subcommand) if example.subcommand else None
        _insert_example(conn, tool_id, linked_sub_id, example)

    for target in spec.see_also:
        if target and target != spec.name:
            conn.execute(
                "INSERT OR IGNORE INTO see_also(tool_id, target_name) VALUES (?, ?)",
                (tool_id, target),
            )

    for src in spec.sources:
        conn.execute(
            """
            INSERT OR REPLACE INTO tool_sources(tool_id, source, location, mtime, content_hash)
            VALUES (?, ?, ?, ?, ?)
            """,
            (tool_id, src.source, src.location, src.mtime, src.content_hash),
        )


def _insert_flag(
    conn: sqlite3.Connection,
    tool_id: int,
    subcommand_id: int | None,
    flag: FlagSpec,
) -> None:
    conn.execute(
        """
        INSERT INTO flags(
            tool_id, subcommand_id, short_flag, long_flag, display_flag,
            takes_value, value_hint, repeatable, section, description, source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tool_id,
            subcommand_id,
            flag.short,
            flag.long,
            flag.display,
            1 if flag.takes_value else 0,
            flag.value_hint,
            1 if flag.repeatable else 0,
            flag.section,
            flag.description,
            flag.source,
        ),
    )


def _insert_example(
    conn: sqlite3.Connection,
    tool_id: int,
    subcommand_id: int | None,
    example: ExampleSpec,
) -> None:
    if not example.command.strip():
        return
    conn.execute(
        """
        INSERT INTO examples(tool_id, subcommand_id, command, description, source)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            tool_id,
            subcommand_id,
            example.command.strip(),
            example.description.strip(),
            example.source,
        ),
    )


def delete_tools_not_in(conn: sqlite3.Connection, active_names: set[str]) -> list[int]:
    """Prune tools that are no longer installed on the machine.

    Returns:
        List of deleted ``tool_id`` values.
    """
    rows = conn.execute("SELECT id, name FROM tools").fetchall()
    removed_ids = [int(row["id"]) for row in rows if str(row["name"]) not in active_names]
    if not removed_ids:
        return []

    placeholders = ",".join("?" for _ in removed_ids)
    conn.execute(f"DELETE FROM tools WHERE id IN ({placeholders})", removed_ids)
    return removed_ids


def resolve_tool_id(conn: sqlite3.Connection, name_or_alias: str) -> int | None:
    """Resolve a tool name or alias (case-insensitive fallback) to its ``tool_id``."""
    cleaned = name_or_alias.strip()
    if not cleaned:
        return None

    row = conn.execute("SELECT id FROM tools WHERE name = ?", (cleaned,)).fetchone()
    if row is not None:
        return int(row["id"])

    alias_row = conn.execute("SELECT tool_id FROM aliases WHERE alias = ?", (cleaned,)).fetchone()
    if alias_row is not None:
        return int(alias_row["tool_id"])

    row = conn.execute(
        "SELECT id FROM tools WHERE lower(name) = lower(?)",
        (cleaned,),
    ).fetchone()
    if row is not None:
        return int(row["id"])

    alias_row = conn.execute(
        "SELECT tool_id FROM aliases WHERE lower(alias) = lower(?)",
        (cleaned,),
    ).fetchone()
    if alias_row is not None:
        return int(alias_row["tool_id"])

    return None


def get_tool_by_id(conn: sqlite3.Connection, tool_id: int) -> ToolSpec | None:
    """Hydrate a complete ``ToolSpec`` by primary key over a single connection."""
    row = conn.execute("SELECT * FROM tools WHERE id = ?", (tool_id,)).fetchone()
    if row is None:
        return None

    aliases = tuple(
        str(r["alias"])
        for r in conn.execute(
            "SELECT alias FROM aliases WHERE tool_id = ? ORDER BY alias", (tool_id,)
        ).fetchall()
    )

    sub_rows = conn.execute(
        "SELECT id, name, summary, synopsis_json, source FROM subcommands WHERE tool_id = ? ORDER BY name",
        (tool_id,),
    ).fetchall()
    sub_name_by_id = {int(r["id"]): str(r["name"]) for r in sub_rows}

    all_flag_rows = conn.execute(
        """
        SELECT subcommand_id, short_flag, long_flag, takes_value, value_hint,
               repeatable, section, description, source
        FROM flags
        WHERE tool_id = ?
        ORDER BY id
        """,
        (tool_id,),
    ).fetchall()
    top_flags: list[FlagSpec] = []
    sub_flags: dict[int, list[FlagSpec]] = {}
    for fr in all_flag_rows:
        fspec = FlagSpec(
            short=fr["short_flag"],
            long=fr["long_flag"],
            takes_value=bool(fr["takes_value"]),
            value_hint=fr["value_hint"],
            repeatable=bool(fr["repeatable"]),
            description=str(fr["description"] or ""),
            section=fr["section"],
            source=_as_source(fr["source"]),
        )
        if fr["subcommand_id"] is None:
            top_flags.append(fspec)
        else:
            sub_flags.setdefault(int(fr["subcommand_id"]), []).append(fspec)

    all_example_rows = conn.execute(
        """
        SELECT subcommand_id, command, description, source
        FROM examples
        WHERE tool_id = ?
        ORDER BY CASE source WHEN 'tldr' THEN 0 WHEN 'man_roff' THEN 1 ELSE 2 END, id
        """,
        (tool_id,),
    ).fetchall()
    top_examples: list[ExampleSpec] = []
    sub_examples: dict[int, list[ExampleSpec]] = {}
    for er in all_example_rows:
        sub_id = int(er["subcommand_id"]) if er["subcommand_id"] is not None else None
        ex_spec = ExampleSpec(
            command=str(er["command"]),
            description=str(er["description"] or ""),
            source=_as_source(er["source"]),
            subcommand=sub_name_by_id.get(sub_id) if sub_id is not None else None,
        )
        if sub_id is None:
            top_examples.append(ex_spec)
        else:
            sub_examples.setdefault(sub_id, []).append(ex_spec)

    subcommands = tuple(
        SubcommandSpec(
            name=str(sr["name"]),
            summary=str(sr["summary"] or ""),
            synopsis=_loads_str_tuple(sr["synopsis_json"]),
            flags=tuple(sub_flags.get(int(sr["id"]), ())),
            examples=tuple(sub_examples.get(int(sr["id"]), ())),
            source=_as_source(sr["source"]),
        )
        for sr in sub_rows
    )

    positionals = tuple(
        PositionalSpec(
            name=str(pr["name"]),
            required=bool(pr["required"]),
            variadic=bool(pr["variadic"]),
            description=str(pr["description"] or ""),
        )
        for pr in conn.execute(
            "SELECT name, required, variadic, description FROM positionals WHERE tool_id = ? ORDER BY position",
            (tool_id,),
        ).fetchall()
    )

    see_also = tuple(
        str(r["target_name"])
        for r in conn.execute(
            "SELECT target_name FROM see_also WHERE tool_id = ? ORDER BY target_name", (tool_id,)
        ).fetchall()
    )

    sources = tuple(
        SourceProvenance(
            source=_as_source(sr["source"]),
            location=str(sr["location"]),
            mtime=float(sr["mtime"]) if sr["mtime"] is not None else None,
            content_hash=str(sr["content_hash"] or ""),
        )
        for sr in conn.execute(
            "SELECT source, location, mtime, content_hash FROM tool_sources WHERE tool_id = ? ORDER BY source, location",
            (tool_id,),
        ).fetchall()
    )

    tags_raw = str(row["tags_text"] or "").split()
    category = row["category"]
    tags = tuple(tag for tag in tags_raw if tag != category)

    return ToolSpec(
        name=str(row["name"]),
        summary=str(row["summary"]),
        binary_path=row["binary_path"],
        section=int(row["section"]) if row["section"] is not None else None,
        aliases=aliases,
        description=str(row["description"] or ""),
        category=category,
        tags=tags,
        synopsis=_loads_str_tuple(row["synopsis_json"]),
        subcommands=subcommands,
        flags=tuple(top_flags),
        positionals=positionals,
        examples=tuple(top_examples),
        see_also=see_also,
        sources=sources,
    )


def get_tool(conn: sqlite3.Connection, name_or_alias: str) -> ToolSpec | None:
    """Hydrate a complete ``ToolSpec`` by name or alias."""
    tool_id = resolve_tool_id(conn, name_or_alias)
    if tool_id is None:
        return None
    return get_tool_by_id(conn, tool_id)


def list_tool_names(conn: sqlite3.Connection) -> list[str]:
    """Return sorted tool names + aliases (for shell completions & lookup)."""
    names = {str(r[0]) for r in conn.execute("SELECT name FROM tools").fetchall()}
    names.update(str(r[0]) for r in conn.execute("SELECT alias FROM aliases").fetchall())
    return sorted(names)


def get_embedding_text_for_id(conn: sqlite3.Connection, tool_id: int) -> str:
    """Build the embedding card text for a stored tool row."""
    spec = get_tool_by_id(conn, tool_id)
    if spec is None:
        return ""
    return spec.embedding_document()


def index_stats(conn: sqlite3.Connection) -> IndexStats:
    """Collect index health and coverage metrics for ``sem doctor``."""
    tool_count = int(conn.execute("SELECT COUNT(*) FROM tools").fetchone()[0])
    alias_count = int(conn.execute("SELECT COUNT(*) FROM aliases").fetchone()[0])
    subcommand_count = int(conn.execute("SELECT COUNT(*) FROM subcommands").fetchone()[0])
    flag_count = int(conn.execute("SELECT COUNT(*) FROM flags").fetchone()[0])
    example_count = int(conn.execute("SELECT COUNT(*) FROM examples").fetchone()[0])
    tools_with_flags = int(conn.execute("SELECT COUNT(DISTINCT tool_id) FROM flags").fetchone()[0])
    tools_with_examples = int(
        conn.execute("SELECT COUNT(DISTINCT tool_id) FROM examples").fetchone()[0]
    )
    tools_with_subcommands = int(
        conn.execute("SELECT COUNT(DISTINCT tool_id) FROM subcommands").fetchone()[0]
    )
    source_rows = conn.execute(
        "SELECT source, COUNT(DISTINCT tool_id) AS cnt FROM tool_sources GROUP BY source"
    ).fetchall()
    source_counts = {str(r["source"]): int(r["cnt"]) for r in source_rows}

    return IndexStats(
        tool_count=tool_count,
        alias_count=alias_count,
        subcommand_count=subcommand_count,
        flag_count=flag_count,
        example_count=example_count,
        tools_with_flags=tools_with_flags,
        tools_with_examples=tools_with_examples,
        tools_with_subcommands=tools_with_subcommands,
        source_counts=source_counts,
    )
