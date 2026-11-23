"""SQLite v3 schema and connection management for sempropos.

Stores canonical ToolSpec records across normalized tables with Porter-stemmed
FTS5 virtual tables (`tokenize='porter unicode61'`) kept in sync via triggers.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sempropos import config
from sempropos.model.spec import SCHEMA_VERSION

SCHEMA_SQL = f"""
PRAGMA user_version = {SCHEMA_VERSION};

-- =========================================================================
-- Core structured tables
-- =========================================================================

CREATE TABLE IF NOT EXISTS tools (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL UNIQUE,
    binary_path      TEXT,
    section          INTEGER,
    summary          TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    category         TEXT,
    tags_text        TEXT NOT NULL DEFAULT '',
    synopsis_json    TEXT NOT NULL DEFAULT '[]',
    aliases_text     TEXT NOT NULL DEFAULT '',
    subcommands_text TEXT NOT NULL DEFAULT '',
    examples_text    TEXT NOT NULL DEFAULT '',
    flags_text       TEXT NOT NULL DEFAULT '',
    content_hash     TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aliases (
    alias   TEXT PRIMARY KEY,
    tool_id INTEGER NOT NULL REFERENCES tools(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS subcommands (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id       INTEGER NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    summary       TEXT NOT NULL DEFAULT '',
    synopsis_json TEXT NOT NULL DEFAULT '[]',
    source        TEXT NOT NULL,
    UNIQUE(tool_id, name)
);

CREATE TABLE IF NOT EXISTS flags (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id       INTEGER NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
    subcommand_id INTEGER REFERENCES subcommands(id) ON DELETE CASCADE,
    short_flag    TEXT,
    long_flag     TEXT,
    display_flag  TEXT NOT NULL,
    takes_value   INTEGER NOT NULL DEFAULT 0,
    value_hint    TEXT,
    repeatable    INTEGER NOT NULL DEFAULT 0,
    section       TEXT,
    description   TEXT NOT NULL,
    source        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positionals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id     INTEGER NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    name        TEXT NOT NULL,
    required    INTEGER NOT NULL DEFAULT 0,
    variadic    INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS examples (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id       INTEGER NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
    subcommand_id INTEGER REFERENCES subcommands(id) ON DELETE CASCADE,
    command       TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS see_also (
    tool_id     INTEGER NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
    target_name TEXT NOT NULL,
    PRIMARY KEY (tool_id, target_name)
);

CREATE TABLE IF NOT EXISTS tool_sources (
    tool_id      INTEGER NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
    source       TEXT NOT NULL,
    location     TEXT NOT NULL,
    mtime        REAL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (tool_id, source, location)
);

-- =========================================================================
-- Secondary indices
-- =========================================================================

CREATE INDEX IF NOT EXISTS idx_aliases_tool ON aliases(tool_id);
CREATE INDEX IF NOT EXISTS idx_subcommands_tool ON subcommands(tool_id);
CREATE INDEX IF NOT EXISTS idx_flags_tool ON flags(tool_id);
CREATE INDEX IF NOT EXISTS idx_flags_subcommand ON flags(subcommand_id);
CREATE INDEX IF NOT EXISTS idx_positionals_tool ON positionals(tool_id);
CREATE INDEX IF NOT EXISTS idx_examples_tool ON examples(tool_id);
CREATE INDEX IF NOT EXISTS idx_examples_subcommand ON examples(subcommand_id);
CREATE INDEX IF NOT EXISTS idx_tool_sources_tool ON tool_sources(tool_id);
CREATE INDEX IF NOT EXISTS idx_tools_category ON tools(category);

-- =========================================================================
-- Porter-stemmed FTS5 virtual tables
-- =========================================================================

CREATE VIRTUAL TABLE IF NOT EXISTS tools_fts USING fts5(
    name,
    aliases_text,
    summary,
    tags_text,
    subcommands_text,
    examples_text,
    flags_text,
    content='tools',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS flags_fts USING fts5(
    display_flag,
    description,
    content='flags',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS subcommands_fts USING fts5(
    name,
    summary,
    content='subcommands',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS examples_fts USING fts5(
    command,
    description,
    content='examples',
    content_rowid='id',
    tokenize='porter unicode61'
);

-- =========================================================================
-- Triggers to keep FTS5 virtual tables synchronized
-- =========================================================================

CREATE TRIGGER IF NOT EXISTS tools_ai AFTER INSERT ON tools BEGIN
    INSERT INTO tools_fts(
        rowid, name, aliases_text, summary, tags_text,
        subcommands_text, examples_text, flags_text
    )
    VALUES (
        new.id, new.name, new.aliases_text, new.summary, new.tags_text,
        new.subcommands_text, new.examples_text, new.flags_text
    );
END;

CREATE TRIGGER IF NOT EXISTS tools_ad AFTER DELETE ON tools BEGIN
    INSERT INTO tools_fts(
        tools_fts, rowid, name, aliases_text, summary, tags_text,
        subcommands_text, examples_text, flags_text
    )
    VALUES (
        'delete', old.id, old.name, old.aliases_text, old.summary, old.tags_text,
        old.subcommands_text, old.examples_text, old.flags_text
    );
END;

CREATE TRIGGER IF NOT EXISTS tools_au AFTER UPDATE ON tools BEGIN
    INSERT INTO tools_fts(
        tools_fts, rowid, name, aliases_text, summary, tags_text,
        subcommands_text, examples_text, flags_text
    )
    VALUES (
        'delete', old.id, old.name, old.aliases_text, old.summary, old.tags_text,
        old.subcommands_text, old.examples_text, old.flags_text
    );
    INSERT INTO tools_fts(
        rowid, name, aliases_text, summary, tags_text,
        subcommands_text, examples_text, flags_text
    )
    VALUES (
        new.id, new.name, new.aliases_text, new.summary, new.tags_text,
        new.subcommands_text, new.examples_text, new.flags_text
    );
END;

CREATE TRIGGER IF NOT EXISTS flags_ai AFTER INSERT ON flags BEGIN
    INSERT INTO flags_fts(rowid, display_flag, description)
    VALUES (new.id, new.display_flag, new.description);
END;

CREATE TRIGGER IF NOT EXISTS flags_ad AFTER DELETE ON flags BEGIN
    INSERT INTO flags_fts(flags_fts, rowid, display_flag, description)
    VALUES ('delete', old.id, old.display_flag, old.description);
END;

CREATE TRIGGER IF NOT EXISTS flags_au AFTER UPDATE ON flags BEGIN
    INSERT INTO flags_fts(flags_fts, rowid, display_flag, description)
    VALUES ('delete', old.id, old.display_flag, old.description);
    INSERT INTO flags_fts(rowid, display_flag, description)
    VALUES (new.id, new.display_flag, new.description);
END;

CREATE TRIGGER IF NOT EXISTS subcommands_ai AFTER INSERT ON subcommands BEGIN
    INSERT INTO subcommands_fts(rowid, name, summary)
    VALUES (new.id, new.name, new.summary);
END;

CREATE TRIGGER IF NOT EXISTS subcommands_ad AFTER DELETE ON subcommands BEGIN
    INSERT INTO subcommands_fts(subcommands_fts, rowid, name, summary)
    VALUES ('delete', old.id, old.name, old.summary);
END;

CREATE TRIGGER IF NOT EXISTS subcommands_au AFTER UPDATE ON subcommands BEGIN
    INSERT INTO subcommands_fts(subcommands_fts, rowid, name, summary)
    VALUES ('delete', old.id, old.name, old.summary);
    INSERT INTO subcommands_fts(rowid, name, summary)
    VALUES (new.id, new.name, new.summary);
END;

CREATE TRIGGER IF NOT EXISTS examples_ai AFTER INSERT ON examples BEGIN
    INSERT INTO examples_fts(rowid, command, description)
    VALUES (new.id, new.command, new.description);
END;

CREATE TRIGGER IF NOT EXISTS examples_ad AFTER DELETE ON examples BEGIN
    INSERT INTO examples_fts(examples_fts, rowid, command, description)
    VALUES ('delete', old.id, old.command, old.description);
END;

CREATE TRIGGER IF NOT EXISTS examples_au AFTER UPDATE ON examples BEGIN
    INSERT INTO examples_fts(examples_fts, rowid, command, description)
    VALUES ('delete', old.id, old.command, old.description);
    INSERT INTO examples_fts(rowid, command, description)
    VALUES (new.id, new.command, new.description);
END;
"""


def open_connection(path: Path | None = None, *, read_only: bool = False) -> sqlite3.Connection:
    """Open a configured SQLite connection (caller manages lifetime)."""
    db_file = path or config.db_path()
    if read_only:
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, check_same_thread=False)
    else:
        db_file.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_file, check_same_thread=False)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def get_connection(
    path: Path | None = None, *, read_only: bool = False
) -> Iterator[sqlite3.Connection]:
    """Context manager for a configured SQLite connection."""
    conn = open_connection(path, read_only=read_only)
    try:
        yield conn
        if not read_only:
            conn.commit()
    except Exception:
        if not read_only:
            conn.rollback()
        raise
    finally:
        conn.close()


def read_schema_version(conn: sqlite3.Connection) -> int:
    """Return SQLite ``PRAGMA user_version`` for the connected database."""
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row is not None else 0


def initialize_database(path: Path | None = None) -> None:
    """Create all v3 tables, indices, FTS5 tables, and triggers."""
    with get_connection(path) as conn:
        conn.executescript(SCHEMA_SQL)
