---
okf_version: "0.2"
---

# Files

- [Architecture Overview](architecture.md)
- [CLI Surface and Query Dispatch](cli.md) - Commands, bare-query argv normalization, --backend and --porcelain behavior, output rendering, fallbacks, and exit codes for the sem CLI.
- [Configuration and Paths](configuration.md) - Data and config directory resolution, intelligence.toml schema, role options, index metadata validation, revision constants, and API key resolution.
- [Development and Quality](development.md) - Local development setup, Makefile targets, ruff and mypy configuration, CI import smoke checks, release publishing, testing policy, and roadmap themes.
- [Index Database Schema](index-schema.md) - SQLite tables, FTS5 virtual tables, synchronization triggers, connection behavior, and the retrieval queries that depend on the schema.
- [Indexing Pipeline](indexing.md) - How sempropos discovers man pages, parses flags and examples, builds the SQLite index, and maintains the tool embedding matrix incrementally and atomically.
- [Operations and Troubleshooting](operations.md) - Install, update, check, configure, and provider workflows, index staleness detection, and remedies for common index, provider, and environment failures.
- [Quickstart](quickstart.md) - Install sem, run first-time setup, issue a natural-language query, enable Ctrl+S shell integration, and find the right page for deeper detail.
- [Retrieval and Ranking](retrieval.md) - Query tokenization and synonym expansion, BM25-primary lexical ranking with name boost, cosine semantic recall, hybrid fusion semantics, and per-tool flag selection.
- [Shell Integration](shell-integration.md) - The zsh, bash, and fish Ctrl+S widgets, the stty requirement, and the --porcelain contract that makes in-place command replacement safe.
- [Prompting and Synthesis Providers](synthesis.md) - Provider contracts and dispatch, Ollama and OpenAI-compatible backends, FastEmbed embeddings, tier0 fallback, prompt budgeting, and command extraction and repair.
