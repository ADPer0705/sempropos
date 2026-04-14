"""SQLite schema and connection helpers."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from sempropos import config

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tools (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    section     INTEGER NOT NULL DEFAULT 1,
    description TEXT NOT NULL,
    synopsis    TEXT
);

CREATE TABLE IF NOT EXISTS flags (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id      INTEGER NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
    flag         TEXT NOT NULL,
    long_flag    TEXT,
    takes_value  BOOLEAN DEFAULT 0,
    value_hint   TEXT,
    description  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS examples (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id      INTEGER NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
    command      TEXT NOT NULL,
    context      TEXT
);

CREATE INDEX IF NOT EXISTS idx_flags_tool ON flags(tool_id);
CREATE INDEX IF NOT EXISTS idx_examples_tool ON examples(tool_id);
"""


@contextmanager
def get_connection(path: Path | None = None):
    """Open a SQLite connection configured for sempropos schema usage."""
    config.ensure_data_dirs()
    db_file = path or config.db_path()
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def initialize(path: Path | None = None) -> None:
    """Create required tables and indices if they do not exist."""
    with get_connection(path) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
