---
okf_version: "0.2"
---

# Files

- [Architecture Overview](architecture.md)
- [CLI Surface and Query Dispatch](cli.md) - Commands, bare-query argv normalization, ask/show/search output modes, daemon/config/model commands, flags, exit codes, and dispatch behavior for the sem CLI.
- [Configuration and Paths](configuration.md) - Data, config, and runtime directory resolution, config.toml schema, provider and model settings, index metadata validation, revision constants, and API key resolution.
- [Warm Daemon & Service Layer](daemon.md) - Auto-managed Unix-socket warm daemon (SemDaemonServer, stdlib-only DaemonClient, --porcelain fast path, flock single-instance lock, idle auto-shutdown) and the decoupled SemService application layer.
- [Development and Quality](development.md) - Local development setup, Makefile targets, ruff and mypy configuration, CI quality gates, test layout and offline policy, release publishing, and roadmap themes.
- [Index Database Schema](index-schema.md) - SQLite v3 tables, four Porter-stemmed FTS5 virtual tables, synchronization triggers, connection behavior, and the repository queries that depend on the schema.
- [Indexing Pipeline](indexing.md) - How sempropos discovers PATH binaries and man pages, parses roff/mdoc plus tldr, completions, and safe --help probes, merges them into ToolSpecs, and maintains SQLite and embeddings incrementally and atomically.
- [Operations and Troubleshooting](operations.md) - Install, update, doctor, config, daemon, model, and provider workflows, index staleness detection, and remedies for common index, provider, daemon, and embedding failures.
- [Quickstart](quickstart.md) - Install sem v1.0.0, build the multi-source ToolSpec index, use ask/show/search modes, optionally enable the built-in local GGUF model, and set up Ctrl+S shell integration.
- [Retrieval and Ranking](retrieval.md) - Stopword filtering, singular forms, weighted directional synonym expansion, multi-column BM25 with artifact and intent boosts, confidence-gated semantic recall, hybrid fusion semantics, and per-tool flag selection.
- [Shell Integration](shell-integration.md) - The zsh, bash, and fish Ctrl+S widgets, tab completions, the stty requirement, and the --porcelain contract that makes in-place command replacement safe.
- [Command Synthesis, Providers & Safety Verification](synthesis.md) - Provider contracts, lazy facade dispatch, managed local GGUF models, pure-HTTP Ollama and OpenAI-compatible backends, FastEmbed embeddings, Tier-0 deterministic fallback, JSON-schema prompt budgeting, and command safety verification.
