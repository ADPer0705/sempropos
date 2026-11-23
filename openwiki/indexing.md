---
type: architecture
title: Indexing Pipeline
description: How sempropos discovers PATH binaries and man pages, parses roff/mdoc plus tldr, completions, and safe --help probes, merges them into ToolSpecs, and maintains SQLite and embeddings incrementally and atomically.
tags: [indexing, man-pages, embeddings, incremental]
verified:
  - by: openwiki/0.9.0
    at: 2026-11-23T21:48:04.805Z
sources:
  - id: openwiki-source-219b42e1aa58452b690c638d
    resource: repo://src/sempropos/config.py
  - id: openwiki-source-ea5077150cf30546a7983ff8
    resource: repo://src/sempropos/index/builder.py
  - id: openwiki-source-35f3c8803b8fa475b2d74e94
    resource: repo://src/sempropos/index/parser.py
  - id: openwiki-source-de7881d4d3ee12f130f2e109
    resource: repo://src/sempropos/sources/man.py
generated: { by: "opencode", at: "2026-11-21T06:12:23.249Z" }
---

# Indexing Pipeline

`build_index` in `src/sempropos/index/builder.py` assembles the SQLite index and tool embedding matrix from four documentation sources: local man pages, an offline tldr snapshot, Fish/Zsh completion scripts, and safe `--help` probes. It is designed for bounded memory, content-hash incrementality, and crash-safe metadata.

## Entry point

```python
build_index(progress=True, force=False, *, offline=False, enable_tldr=None,
            enable_completions=None, probe_help=None, skip_embeddings=False)
```

`sem install` calls it with `force=True`; `sem update` calls it with `force=False`. Source toggles default to the `[indexing]` configuration. The function:

1. calls `config.ensure_data_dirs()` and loads the configuration;
2. reads `index.meta` and compares `index_revision`;
3. treats the run as a full rebuild when `force` is set, the stored revision differs from `INDEX_REVISION`, or `PRAGMA user_version` in an existing database differs from `INDEX_REVISION`;
4. clears index data on a full rebuild and initializes the schema;
5. resolves the embedding strategy so configuration errors fail early;
6. discovers PATH binaries, then extracts man, tldr, completion, and probe fragments;
7. merges bundles into canonical `ToolSpec`s, upserts them, prunes removed tools, and synchronizes embeddings;
8. unloads the embedding model, writes `last_indexed`, and writes `index.meta`.

Two hard failures are possible: `RuntimeError("No executable binaries were discovered on PATH.")` when discovery is empty and `RuntimeError("No tool documentation could be extracted from any configured source.")` when merging produces no specs.

## Stage 1 — PATH discovery

`discover_path_binaries` in `src/sempropos/sources/discovery.py` scans each unique `$PATH` directory once with `os.scandir`, records executable non-directory files, and stops at the first occurrence of a name. It then resolves symlinks and records a `symlink_aliases` map for links whose target is another discovered binary, excluding multi-call dispatchers and interpreters such as `busybox`, `coreutils`, `git`, `systemd`, and Python/Perl/Ruby/Node binaries.

`DiscoveryContext.canonical_name` follows that alias map. `attach_symlink_aliases` copies each alias onto the canonical tool's `ToolSpec.aliases`, so `egrep` resolves to `grep` without becoming a separate index entry.

## Stage 2 — Source extraction

### Man pages

`src/sempropos/sources/man.py` no longer shells out to `man -k` for discovery. It:

- builds a man root list from `MANPATH`, then `manpath`, then defaults (`~/.local/share/man`, `/usr/local/share/man`, `/usr/share/man`, `/usr/local/man`, `/opt/homebrew/share/man`);
- scans `man1/` and `man8/` for files matching `name.<section>[suffix]`, including `.gz`, `.xz`, `.lzma`, `.bz2`, and `.zst`;
- keeps only names present in the PATH discovery context, preferring an exact filename over a symlink alias;
- reads and decompresses the file directly (`read_man_file`), following short `.so man1/other.1` redirect stubs up to four levels;
- parses roff/mdoc with `parse_roff_source`, detecting mdoc by `.Dd`/`.Dt`/`.Sh` markers and otherwise using the man(7) parser;
- falls back to a clean render (`MANWIDTH=10000`, `GROFF_NO_SGR=1`, `man -P cat`) only when a page longer than 40 lines yields neither flags nor a synopsis, and accepts the rendered result when it finds more flags or a missing synopsis.

Man extraction runs in a bounded `ThreadPoolExecutor` of `min(cpu_count, 8)` workers with twice as many in-flight futures. A failed page is skipped, and extraction returns `{}` when no man files are found.

### tldr

`src/sempropos/sources/tldr.py` looks for a local snapshot in `data_dir()/tldr`, `~/.cache/tldr`, tealdeer caches, and `~/.tldr/tldr`. When none exists and the build is not offline, it downloads the official English `tldr-pages.en.zip` release into the data directory. Pages are parsed for summary, description, examples, and subcommands, including hyphenated subcommand pages such as `git-commit.md`.

### Shell completions

`src/sempropos/sources/completions.py` scans standard Fish and Zsh completion directories, parses flag descriptions, value-taking flags, and subcommands statically, and tags fragments as `completion_fish` or `completion_zsh`. No shell is executed.

### `--help` probing

`_probe_missing_tools` probes only binaries that are absent from man documentation or whose man page produced neither flags nor subcommands. `is_safe_to_probe` rejects denylisted and setuid/setgid binaries; `run_help_probe` runs the binary with `--help` in a sanitized headless environment, a detached process group, and the configured timeout (default 1.5 s). Probes also use the bounded worker window.

## Stage 3 — Parsing and merging

### Roff parsing

`src/sempropos/sources/roff_parser.py` provides both man(7) and mdoc(7) parsers. They:

- strip font escapes and macro artifacts from inline text;
- treat uppercase lines as section headings;
- collect the synopsis (at most six unique lines after merging with probe synopsis);
- collect flags from option-bearing sections — names containing `OPTION`, `FLAG`, `SWITCH`, `EXPRESSION`, `PREDICATE`, `TEST`, `ACTION`, `MODIFIER`, `ARGUMENT`, `PRIMARY`, `OPERATOR`, `PARAMETER`, or `KEY`, plus `DESCRIPTION` — while excluding `NAME`, `SYNOPSIS`, `EXAMPLES`, `SEE ALSO`, `AUTHOR`, `COPYRIGHT`, `LICENSE`, `BUGS`, `HISTORY`, `EXIT STATUS`, `ENVIRONMENT`, and `FILES`;
- recognize short and long forms, `=VALUE` and `[=VALUE]` hints, and space-separated value hints;
- cap each flag description at 420 characters and keep at most 400 flags per tool;
- collect at most 40 examples, filtering English prose by starter words and length and stripping leading `$`, `#`, or `>`;
- extract aliases and `SEE ALSO` references.

### Merge rules

`src/sempropos/sources/merger.py` merges fragments deterministically:

- field priority is `man_roff`, `completion_fish`, `help_probe`, `completion_zsh`, `man_text`, then `tldr`;
- flags merge by canonical short/long key, keeping the highest-priority metadata and sorting by source priority;
- subcommands merge by name, preferring tldr summaries;
- examples are ordered tldr first, then `man_roff`, then `man_text`, then `help_probe`, and deduplicated by command;
- the summary prefers tldr, then the man summary, then the probe summary, then the first sentence of the man description;
- a lightweight keyword classifier assigns one category (`archive`, `network`, `filesystem`, `vcs`, `container`, `process`, `text`, `package`, `media`, `security`) plus tags;
- every contributing source becomes a `SourceProvenance` with location, mtime, and content hash.

## Stage 4 — Database ingestion and pruning

`build_index` builds every spec on every run, then lets the repository decide what changed:

- `repository.delete_tools_not_in(conn, active_names)` removes tools whose names are no longer installed and returns their ids;
- `repository.upsert_tool` hashes the spec; an unchanged hash returns immediately, while a changed spec replaces all child rows (flags, examples, subcommands, positionals, aliases, sources) and reports the tool as changed;
- inserts commit every `_DB_COMMIT_BATCH = 100` tools.

Because parsing is unconditional but writes are hash-gated, edits to a man page are reflected on the next `sem update` without a forced rebuild, and removed tools are pruned each run.

## Stage 5 — Embedding synchronization

Only tool cards are embedded; flags, subcommands, and examples are ranked lexically at query time. `_sync_embeddings` keeps an explicit `{tool_id: vector}` map:

- `tool_embeddings.npy` and `tool_embedding_ids.npy` are loaded when both exist and their row counts agree;
- a vector is reused when its `tool_id` is still present and is not in `changed_ids`;
- only new or content-modified ids are embedded;
- vectors are L2-normalized.

A full rebuild, a provider change, a model change, or an `EMBEDDING_REVISION` mismatch forces every vector to be regenerated. Embedding runs in batches of `_EMBED_BATCH = 64`, with each embedding text truncated to 2,200 characters. The merged matrix and id vector are written to temporary `.npy` files, fsynced, and moved into place with `os.replace` by `_atomic_save_npy`. If every active row already has a valid vector, the sync returns the existing dimension without rewriting files.

When `embedding.provider = "none"` or `--skip-embeddings` is used, the dimension is recorded as 0 and no vector files are written.

## Finalization

After ingestion and embedding:

1. `index_stats` reads tool, flag, subcommand, example, and source counts from SQLite;
2. the FastEmbed model is unloaded to release memory;
3. `last_indexed` is written atomically with the current UTC timestamp;
4. `index.meta` is written atomically with status `complete`, both revisions, counts, provider, model, and dimension.

Because metadata is written last, an interrupted run is not advertised as complete; query-time validation then tells the user to run `sem update`.

A full rebuild deletes `index.db`, its `-wal` and `-shm` files, `tool_embeddings.npy`, `tool_embedding_ids.npy`, `index.meta`, and `last_indexed` before rebuilding.

## Related pages

- [Index Database Schema](index-schema.md)
- [Configuration and Paths](configuration.md)
- [Operations and Troubleshooting](operations.md)
- [Architecture Overview](architecture.md)
