---
type: reference
title: Index Database Schema
description: SQLite v3 tables, four Porter-stemmed FTS5 virtual tables, synchronization triggers, connection behavior, and the repository queries that depend on the schema.
tags: [database, sqlite, fts5, schema]
verified:
  - by: openwiki/0.9.0
    at: 2026-11-23T21:48:04.805Z
sources:
  - id: openwiki-source-72fe826953aaa47d17a811e4
    resource: repo://ROADMAP.md
  - id: openwiki-source-257365c350a958492518fcd4
    resource: repo://src/sempropos/index/schema.py
  - id: openwiki-source-2f4a1d1392b204104c5fce86
    resource: repo://src/sempropos/retrieval/bm25.py
  - id: openwiki-source-0032f2d73652a4f2b635b6f5
    resource: repo://src/sempropos/retrieval/flag_retrieval.py
generated: { by: "opencode", at: "2026-11-21T06:12:23.249Z" }
---

# Index Database Schema

The index is one SQLite database created by `src/sempropos/index/schema.py` and written through `src/sempropos/index/repository.py`. It stores a normalized `ToolSpec` — tools, aliases, subcommands, flags, positionals, examples, cross-references, and source provenance — and mirrors searchable text into four Porter-stemmed FTS5 virtual tables. `SCHEMA_VERSION` and `PRAGMA user_version` are both `3`.

## Core tables

### `tools`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER PRIMARY KEY AUTOINCREMENT` | Parent id for all child rows |
| `name` | `TEXT NOT NULL UNIQUE` | Executable name; one row per tool |
| `binary_path` | `TEXT` | Resolved executable path |
| `section` | `INTEGER` | Man section when known |
| `summary` | `TEXT NOT NULL` | One-line description |
| `description` | `TEXT NOT NULL DEFAULT ''` | Longer description |
| `category` | `TEXT` | Display and `search --category` filter |
| `tags_text` | `TEXT NOT NULL DEFAULT ''` | Space-joined category plus tags |
| `synopsis_json` | `TEXT NOT NULL DEFAULT '[]'` | JSON array of synopsis lines |
| `aliases_text` | `TEXT NOT NULL DEFAULT ''` | Space-joined aliases for FTS |
| `subcommands_text` | `TEXT NOT NULL DEFAULT ''` | Flattened subcommand names and summaries |
| `examples_text` | `TEXT NOT NULL DEFAULT ''` | Flattened example commands and descriptions |
| `flags_text` | `TEXT NOT NULL DEFAULT ''` | Flattened flag displays and descriptions |
| `content_hash` | `TEXT NOT NULL` | Stable hash used to skip unchanged tools |
| `updated_at` | `TEXT NOT NULL` | ISO timestamp |

### Child tables

| Table | Key columns | Relationship |
| --- | --- | --- |
| `aliases` | `alias` primary key, `tool_id` | `ON DELETE CASCADE`; an alias maps to one tool |
| `subcommands` | `tool_id`, `name`, `summary`, `synopsis_json`, `source` | `UNIQUE(tool_id, name)`, cascade delete |
| `flags` | `tool_id`, optional `subcommand_id`, short/long/display names, `takes_value`, `value_hint`, `repeatable`, `section`, `description`, `source` | Cascades from tool or subcommand |
| `positionals` | `tool_id`, `position`, `name`, `required`, `variadic`, `description` | Cascade delete |
| `examples` | `tool_id`, optional `subcommand_id`, `command`, `description`, `source` | Cascade delete |
| `see_also` | `tool_id`, `target_name` | Composite primary key, cascade delete |
| `tool_sources` | `tool_id`, `source`, `location`, `mtime`, `content_hash` | Composite primary key, cascade delete |

Secondary indexes cover aliases, subcommands, flags (by tool and subcommand), positionals, examples (by tool and subcommand), tool sources, and `tools(category)`.

## FTS5 virtual tables

All four tables use `tokenize='porter unicode61'` and are external-content tables keyed by `content_rowid='id'`:

```sql
CREATE VIRTUAL TABLE tools_fts USING fts5(
    name, aliases_text, summary, tags_text,
    subcommands_text, examples_text, flags_text,
    content='tools', content_rowid='id', tokenize='porter unicode61'
);
CREATE VIRTUAL TABLE flags_fts USING fts5(
    display_flag, description,
    content='flags', content_rowid='id', tokenize='porter unicode61'
);
CREATE VIRTUAL TABLE subcommands_fts USING fts5(
    name, summary,
    content='subcommands', content_rowid='id', tokenize='porter unicode61'
);
CREATE VIRTUAL TABLE examples_fts USING fts5(
    command, description,
    content='examples', content_rowid='id', tokenize='porter unicode61'
);
```

Porter stemming lets `port` match `ports` and similar inflections. The raw `synopsis_json` column is stored on `tools` but is not part of `tools_fts`; lexical search uses `summary`, aliases, tags, subcommands, examples, and flags instead.

## Synchronization triggers

Each base table has three triggers (`*_ai`, `*_ad`, `*_au`) that insert, delete, and update the corresponding FTS row. Because the FTS tables are external-content, these triggers are the only thing keeping the inverted index consistent. Direct writes to the base tables outside the repository layer would desynchronize it.

## Connections and initialization

`open_connection(path=None, read_only=False)`:

- defaults to `config.db_path()` and creates the parent directory for write mode;
- opens read-only connections with `file:<path>?mode=ro` and `uri=True`;
- sets `PRAGMA journal_mode = WAL` and `PRAGMA synchronous = NORMAL` for write connections;
- uses `sqlite3.Row` rows, `PRAGMA foreign_keys = ON`, `PRAGMA busy_timeout = 5000`, and `check_same_thread=False`;
- is used directly by the daemon for a long-lived read connection.

`get_connection()` is the context-manager wrapper: commit on success, rollback on exception, always close. Read-only connections never commit. `initialize_database()` executes `SCHEMA_SQL`; all statements use `IF NOT EXISTS`, so it is idempotent and safe on every build. `read_schema_version()` reads `PRAGMA user_version`.

## Repository behavior

`upsert_tool` is content-hash driven:

1. compute `spec.content_hash()`;
2. if the existing row has the same hash, return `(tool_id, False)` without touching children or embeddings;
3. otherwise insert or update the `tools` row;
4. on update, delete all child rows (aliases, flags, examples, subcommands, positionals, see-also, sources) and reinsert them;
5. return `(tool_id, True)` so the builder knows the tool needs re-embedding.

Other repository functions:

- `resolve_tool_id` tries exact name, exact alias, then case-insensitive name and alias.
- `get_tool_by_id` hydrates a complete `ToolSpec` over one connection, grouping subcommand flags and examples and ordering examples with `tldr` first, then `man_roff`, then other sources.
- `get_tool` resolves a name or alias first.
- `list_tool_names` returns sorted names plus aliases for completion and lookup.
- `delete_tools_not_in` prunes tools whose names are no longer installed and returns their ids so the builder can drop embeddings.
- `index_stats` reports tool, alias, subcommand, flag, and example counts, plus coverage counts and `source_counts` for `sem doctor`.

## How retrieval uses the schema

- **Tools.** `query_bm25_ranking` in `retrieval/bm25.py` selects `tools_fts.rowid`, weighted `bm25(tools_fts, 12.0, 10.0, 6.0, 4.0, 4.0, 3.0, 1.5)`, and the tool name/aliases. It joins `tools` on rowid, applies exact-name and alias boosts, and merges OR, AND, synonym, and direct-name lookups.
- **Subcommands.** `get_relevant_subcommands` queries `subcommands_fts` scoped by `tool_id` with `bm25(subcommands_fts, 4.0, 1.0)`, then falls back to the first subcommands by id.
- **Flags.** `get_relevant_flags` queries `flags_fts` scoped by `tool_id` with `bm25(flags_fts, 3.0, 1.0)`, fills remaining slots from matching subcommands, then falls back to the tool's top-level flags ordered by id. Results are deduplicated by canonical short/long key.
- **Examples.** `get_relevant_examples` queries `examples_fts` scoped by `tool_id`, prioritizing `tldr` examples and weighting descriptions at `2.5` versus commands at `1.0`, then fills from matched subcommands and finally from all examples. Commands are deduplicated.

Flags, subcommands, and examples are not embedded; they are ranked lexically against the query on demand. Only the tool card is embedded, which keeps the vector matrix small and indexing fast.

## Related pages

- [Indexing Pipeline](indexing.md)
- [Retrieval and Ranking](retrieval.md)
- [Configuration and Paths](configuration.md)
