from __future__ import annotations

from sempropos.index import schema


def test_initialize_creates_tables_and_indexes() -> None:
    schema.initialize()

    with schema.get_connection() as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
            ).fetchall()
        }

    assert "tools" in names
    assert "flags" in names
    assert "examples" in names
    assert "idx_flags_tool" in names
    assert "idx_examples_tool" in names


def test_foreign_key_cascade_removes_tool_children() -> None:
    schema.initialize()

    with schema.get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO tools(name, section, description, synopsis) VALUES (?, ?, ?, ?)",
            ("demo", 1, "demo tool", "demo [opts]"),
        )
        tool_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO flags(tool_id, flag, long_flag, takes_value, value_hint, description) VALUES (?, ?, ?, ?, ?, ?)",
            (tool_id, "-f", "--force", 0, None, "force mode"),
        )
        conn.execute(
            "INSERT INTO examples(tool_id, command, context) VALUES (?, ?, ?)",
            (tool_id, "demo -f", "simple"),
        )
        conn.commit()

        conn.execute("DELETE FROM tools WHERE id = ?", (tool_id,))
        conn.commit()

        flags_count = conn.execute("SELECT COUNT(*) FROM flags").fetchone()[0]
        examples_count = conn.execute("SELECT COUNT(*) FROM examples").fetchone()[0]

    assert flags_count == 0
    assert examples_count == 0


def test_get_connection_enables_foreign_keys() -> None:
    schema.initialize()

    with schema.get_connection() as conn:
        foreign_keys_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]

    assert foreign_keys_on == 1
