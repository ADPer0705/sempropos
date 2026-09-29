"""
SQLite schema and connection helpers.

This module defines the SQLite schema for storing tool information, flags and
examples, as well as a context manager for obtaining database connections.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from sempropos import config

SCHEMA_SQL = """
-- main tables
CREATE TABLE IF NOT EXISTS tools (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    section     INTEGER NOT NULL DEFAULT 1,
    description TEXT NOT NULL,
    synopsis    TEXT,
    UNIQUE(name, section)
);

CREATE TABLE IF NOT EXISTS flags (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id       INTEGER NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
    flag          TEXT NOT NULL,
    long_flag     TEXT,
    takes_value   INTEGER NOT NULL DEFAULT 0,
    value_hint    TEXT,
    description   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS examples (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id      INTEGER NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
    command      TEXT NOT NULL,
    context      TEXT
);

-- indices for faster lookups
CREATE INDEX IF NOT EXISTS idx_flags_tool ON flags(tool_id);
CREATE INDEX IF NOT EXISTS idx_examples_tool ON examples(tool_id);

-- FTS virtual tables for full-text search
CREATE VIRTUAL TABLE IF NOT EXISTS tools_fts USING fts5(
    name,
    description,
    content='tools',
    content_rowid='id'
);

CREATE VIRTUAL TABLE IF NOT EXISTS flags_fts USING fts5(
    flag,
    description,
    content='flags',
    content_rowid='id'
);

-- triggers to keep FTS tables in sync with main tables
CREATE TRIGGER IF NOT EXISTS tools_ai AFTER INSERT ON tools BEGIN
    INSERT INTO tools_fts(rowid, name, description)
    VALUES (new.id, new.name, new.description);
END;

CREATE TRIGGER IF NOT EXISTS tools_ad AFTER DELETE ON tools BEGIN
    INSERT INTO tools_fts(tools_fts, rowid, name, description)
    VALUES ('delete', old.id, old.name, old.description);
END;

CREATE TRIGGER IF NOT EXISTS tools_au AFTER UPDATE ON tools BEGIN
    INSERT INTO tools_fts(tools_fts, rowid, name, description)
    VALUES ('delete', old.id, old.name, old.description);
    INSERT INTO tools_fts(rowid, name, description)
    VALUES (new.id, new.name, new.description);
END;

CREATE TRIGGER IF NOT EXISTS flags_ai AFTER INSERT ON flags BEGIN
    INSERT INTO flags_fts(rowid, flag, description)
    VALUES (new.id, new.flag, new.description);
END;

CREATE TRIGGER IF NOT EXISTS flags_ad AFTER DELETE ON flags BEGIN
    INSERT INTO flags_fts(flags_fts, rowid, flag, description)
    VALUES ('delete', old.id, old.flag, old.description);
END;

CREATE TRIGGER IF NOT EXISTS flags_au AFTER UPDATE ON flags BEGIN
    INSERT INTO flags_fts(flags_fts, rowid, flag, description)
    VALUES ('delete', old.id, old.flag, old.description);
    INSERT INTO flags_fts(rowid, flag, description)
    VALUES (new.id, new.flag, new.description);
END;
"""


@contextmanager
def get_connection(path: Path | None = None):
    """Open a SQLite connection configured for sempropos schema usage."""
    db_file = path or config.db_path()
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_database(path: Path | None = None) -> None:
    """Create required tables, indices, and triggers if they do not exist."""
    with get_connection(path) as conn:
        conn.executescript(SCHEMA_SQL)
