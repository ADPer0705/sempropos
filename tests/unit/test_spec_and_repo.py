"""Tests for canonical ToolSpec IR, AppConfig migration, SQLite v3 schema, and repository."""

from __future__ import annotations

from pathlib import Path

from sempropos import config
from sempropos.index import repository, schema
from sempropos.model import (
    SCHEMA_VERSION,
    ExampleSpec,
    FlagSpec,
    PositionalSpec,
    SourceProvenance,
    SubcommandSpec,
    ToolSpec,
)


def _sample_spec(name: str = "du", summary: str = "estimate file space usage") -> ToolSpec:
    return ToolSpec(
        name=name,
        binary_path=f"/usr/bin/{name}",
        section=1,
        aliases=("diskusage",),
        summary=summary,
        description="Summarize disk usage of files and directories recursively.",
        category="filesystem",
        tags=("disk", "size", "directory"),
        synopsis=(f"{name} [OPTION]... [FILE]...",),
        subcommands=(
            SubcommandSpec(
                name="check",
                summary="Inspect usage",
                synopsis=(f"{name} check [PATH]",),
                flags=(
                    FlagSpec(
                        short="-q",
                        long="--quiet",
                        description="Suppress progress output",
                        source="help_probe",
                    ),
                ),
                examples=(
                    ExampleSpec(
                        command=f"{name} check /var",
                        description="Check /var directory",
                        source="tldr",
                        subcommand="check",
                    ),
                ),
                source="help_probe",
            ),
        ),
        flags=(
            FlagSpec(
                short="-h",
                long="--human-readable",
                description="Print sizes in human readable format (e.g., 1K 234M 2G)",
                source="man_roff",
            ),
            FlagSpec(
                short="-d",
                long="--max-depth",
                takes_value=True,
                value_hint="N",
                description="Print the total for a directory only if N or fewer levels below",
                source="man_roff",
            ),
        ),
        positionals=(PositionalSpec(name="FILE", required=False, variadic=True),),
        examples=(
            ExampleSpec(
                command=f"{name} -sh *",
                description="Show summarized human-readable disk usage by folder",
                source="tldr",
            ),
        ),
        see_also=("df", "ls"),
        sources=(
            SourceProvenance(
                source="man_roff",
                location="/usr/share/man/man1/du.1.gz",
                mtime=1700000000.0,
                content_hash="abc123",
            ),
            SourceProvenance(
                source="tldr",
                location="/tmp/tldr/common/du.md",
                mtime=1700000001.0,
                content_hash="def456",
            ),
        ),
    )


def test_tool_spec_roundtrip_and_stable_hash() -> None:
    spec_a = _sample_spec()
    # Changing only mtime must NOT alter semantic content_hash.
    spec_b = ToolSpec(
        **{
            **spec_a.__dict__,
            "sources": (
                SourceProvenance(
                    source="man_roff",
                    location="/usr/share/man/man1/du.1.gz",
                    mtime=1999999999.0,
                    content_hash="abc123",
                ),
            ),
        }
    )
    assert spec_a.content_hash() == spec_b.content_hash()

    restored = ToolSpec.from_dict(spec_a.to_dict())
    assert restored == spec_a
    assert "human-readable" in spec_a.embedding_document()
    assert "disk usage by folder" in spec_a.embedding_document()


def test_repository_upsert_skip_update_prune_and_porter_fts(
    isolated_env: dict[str, Path],
) -> None:
    schema.initialize_database()
    spec = _sample_spec()

    with schema.get_connection() as conn:
        assert schema.read_schema_version(conn) == SCHEMA_VERSION

        tool_id, changed = repository.upsert_tool(conn, spec)
        assert changed is True

        # Unchanged content hash skips rewrite cleanly.
        tool_id_again, changed_again = repository.upsert_tool(conn, spec)
        assert tool_id_again == tool_id
        assert changed_again is False

        # Roundtrip lookup by both canonical name and alias.
        loaded = repository.get_tool(conn, "du")
        assert loaded is not None
        assert loaded.name == "du"
        assert len(loaded.flags) == 2
        assert len(loaded.subcommands) == 1
        assert len(loaded.examples) == 1
        assert loaded.aliases == ("diskusage",)

        by_alias = repository.get_tool(conn, "diskusage")
        assert by_alias is not None and by_alias.name == "du"

        # Porter stemming: "folders" matches "folder", "directories" matches "directory".
        fts_hits = conn.execute(
            "SELECT rowid FROM tools_fts WHERE tools_fts MATCH ?",
            ("folders",),
        ).fetchall()
        assert [int(r["rowid"]) for r in fts_hits] == [tool_id]

        # Updating summary triggers in-place update without changing tool_id.
        updated_spec = _sample_spec(summary="estimate directory and disk space usage")
        tool_id_u, changed_u = repository.upsert_tool(conn, updated_spec)
        assert tool_id_u == tool_id
        assert changed_u is True
        loaded_u = repository.get_tool(conn, "du")
        assert loaded_u is not None
        assert loaded_u.summary == "estimate directory and disk space usage"

        stats = repository.index_stats(conn)
        assert stats.tool_count == 1
        assert stats.flag_count == 3  # 2 top-level + 1 subcommand flag
        assert stats.example_count == 2  # 1 top-level + 1 subcommand example
        assert stats.subcommand_count == 1
        assert stats.source_counts == {"man_roff": 1, "tldr": 1}

        # Pruning uninstalled binaries deletes cascade cleanly.
        deleted = repository.delete_tools_not_in(conn, set())
        assert deleted == [tool_id]
        assert repository.get_tool(conn, "du") is None
        assert conn.execute("SELECT COUNT(*) FROM flags").fetchone()[0] == 0


def test_config_reads_legacy_and_separates_embedding_section(
    isolated_env: dict[str, Path],
) -> None:
    legacy = config.legacy_intelligence_config_path()
    legacy.write_text(
        """
[synthesis]
provider = "ollama"
model = "qwen2.5-coder:1.5b"
think = false
temperature = 0.0
max_tokens = 256

[embedding]
provider = "fastembed_local"
model = "nomic-ai/nomic-embed-text-v1.5-Q"
think = false
temperature = 0.0
max_tokens = 256
""".strip(),
        encoding="utf-8",
    )

    loaded = config.load_config()
    assert loaded.synthesis.provider == "ollama"
    assert loaded.synthesis.model == "qwen2.5-coder:1.5b"
    assert loaded.embedding.provider == "fastembed_local"
    assert loaded.embedding.model == "nomic-ai/nomic-embed-text-v1.5-Q"
    assert loaded.daemon.enabled is True
    assert loaded.indexing.enable_tldr is True

    saved_path = config.save_config(loaded)
    assert saved_path == config.config_path()
    raw_saved = saved_path.read_text(encoding="utf-8")
    embed_section = raw_saved.split("[embedding]")[1].split("[")[0]
    assert "think" not in embed_section
    assert "temperature" not in embed_section
    assert "max_tokens" not in embed_section
