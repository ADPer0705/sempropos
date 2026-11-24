---
type: guide
title: Quickstart
description: Install sem v1.0.0, build the multi-source ToolSpec index, use ask/show/search modes, optionally enable the built-in local GGUF model, and set up Ctrl+S shell integration.
tags: [quickstart, installation, usage, offline, tri-modal]
verified:
  - by: openwiki/0.9.0
    at: 2026-11-23T21:48:04.805Z
sources:
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
  - id: openwiki-source-401c3a8b8dfa9b92b6d17737
    resource: repo://src/sempropos/cli.py
  - id: openwiki-source-219b42e1aa58452b690c638d
    resource: repo://src/sempropos/config.py
  - id: openwiki-source-6c668f3544f6961d7a48ad3c
    resource: repo://src/sempropos/intelligence/providers/_openai_compatible.py
  - id: openwiki-source-f1189b24619594b1a36787c8
    resource: repo://src/sempropos/retrieval/fusion.py
  - id: openwiki-source-ff870d0a5a99aff8f66b5e2b
    resource: repo://src/sempropos/service.py
  - id: openwiki-source-8f55faa7a495acc24641ff9a
    resource: repo://src/sempropos/shell.py
generated: { by: "antigravity", at: "2026-11-23T21:48:04.805Z" }
---

# Quickstart

`sem` (distributed as the `sempropos` package) is an **instant, offline-first CLI replacement for `apropos`, `--help`, and `man`**. It indexes every command installed on your machine from local `man(7)`/`mdoc(7)` pages, `tldr-pages`, Fish/Zsh completions, and safe `--help` probes into a canonical structured `ToolSpec` index, giving you three fast modes:

- **Synthesize (`sem "..."` / `ask` / `Ctrl+S`):** Turn plain English into a verified, safety-checked shell command.
- **Inspect (`sem show <tool> [subcommand]`):** Instant 0ms-LLM structured reference card replacing `man` and `--help`.
- **Discover (`sem search <query>`):** Instant 0ms-LLM ranked tool search replacing `apropos`.

No API key, telemetry, or cloud backend is required.

## Requirements

- Linux/POSIX with Python 3.11 or newer and SQLite FTS5 (`porter unicode61` tokenizer).
- Optional synthesis LLM:
  - **Built-in local GGUF (`local_gguf`):** Install `sempropos[local-llm]` (or `llama-server`) and run `sem model install balanced` — no Ollama required.
  - **Ollama / OpenAI-compatible:** Pure-HTTP support out of the box.
  - **Tier-0 fallback:** Works completely without an LLM by matching verified `tldr`/man examples or displaying structured `ToolSpec` cards.

## Install

### With `uv` (recommended)

```bash
uv tool install sempropos
sem install
```

### With built-in local GGUF synthesis

```bash
uv tool install "sempropos[local-llm]"
sem install --model balanced    # downloads Qwen2.5-Coder-1.5B (~1 GB) & builds index
```

### From source

```bash
git clone https://github.com/ADPer0705/sempropos.git
cd sempropos
uv sync --group dev
uv run sem install
```

`sem install` verifies SQLite FTS5 with Porter stemming, writes `config.toml`, optionally syncs the English `tldr-pages` snapshot and local GGUF model, builds the SQLite v3 index + tool-card embeddings, and warms the configured synthesis backend.

## Tri-Modal Usage

### 1. Command Synthesis (`sem "..."` / `ask`)

```bash
sem "list files in archive.7z"
sem -e "delete files older than 30 days"      # flag-by-flag breakdown + safety warning
sem -t tar "extract archive to /tmp"          # scope to a single tool
sem -n 3 "sync directory over ssh"            # request alternative commands
sem --porcelain "find markdown files"         # raw command only on stdout
sem --json "show disk usage by folder"        # full JSON response
```

Bare natural-language queries (including multi-word queries starting with a verb like `sem check open ports`) route to `ask`. Generated commands are verified on `$PATH` and classified (`safe`, `caution`, `destructive`) — and are **never executed automatically**.

### 2. Instant Tool Reference (`show` — replaces `man` & `--help`)

```bash
sem show du                   # summary, synopsis, examples, top flags
sem show du --flags depth     # filter flags by keyword
sem show git commit           # inspect a specific subcommand
sem show 7z --examples        # verified usage examples only
```

### 3. Instant Tool Discovery (`search` — replaces `apropos`)

```bash
sem search "open listening ports"
sem search "archive" --category archive --limit 15
```

## Shell Integration (`Ctrl+S` + Tab Completion)

Add one line to your shell startup file:

```bash
# ~/.zshrc or ~/.bashrc
eval "$(sem init zsh)"     # or: eval "$(sem init bash)"

# fish
sem init fish | source
```

Type a plain-English task at the prompt and press **Ctrl+S**:

```text
❯ find all files larger than 100MB        # press Ctrl+S
❯ find . -type f -size +100M
```

The widget calls `sem --porcelain -- "$BUFFER"`, surfaces errors cleanly, and registers tab completions for `sem` subcommands and `sem show <TAB>`. See [Shell Integration & Completions](shell-integration.md).

## Management Commands

```bash
sem install      # first-time setup: check FTS5, write config.toml, build v3 index
sem update       # incremental hash-checked refresh + prune uninstalled binaries
sem doctor       # index health, parse coverage %, unindexed PATH bins, daemon, providers
sem model list   # list curated local GGUF models (install / use / remove)
sem daemon status # inspect warm Unix-socket daemon (start / stop / restart / status)
sem config show  # inspect config.toml (also: config get / set / wizard)
sem providers    # list synthesis and embedding providers and availability
sem init zsh     # print shell integration (zsh, bash, fish)
sem version      # print the version
```

## Data & Runtime Layout

Override paths with `SEMPROPOS_DATA_DIR`, `SEMPROPOS_CONFIG_DIR`, or `SEMPROPOS_RUNTIME_DIR`.

- **Config:** `~/.config/sempropos/config.toml` (auto-reads legacy `intelligence.toml` if present).
- **Data:** `~/.local/share/sempropos/` (`index.db`, `tool_embeddings.npy`, `tool_embedding_ids.npy`, `index.meta`, `last_indexed`, `tldr/`, `models/`, `embeddings/`, `bin/`).
- **Runtime:** `$XDG_RUNTIME_DIR/sempropos/sem.sock` (`0600` private Unix socket for the warm daemon).

## Where to go next

| If you want to… | Read |
| --- | --- |
| Understand the whole request flow | [System Architecture](architecture.md) |
| Learn all commands, routing, and exit codes | [CLI Reference & Command Routing](cli.md) |
| Configure providers, daemon, and indexing | [Configuration & Metadata](configuration.md) |
| Understand the multi-source ingestion pipeline | [Multi-Source Ingestion & Indexing Pipeline](indexing.md) |
| Inspect the ToolSpec IR & SQLite v3 schema | [ToolSpec IR & SQLite v3 Schema](index-schema.md) |
| Understand query analysis, BM25, and fusion | [Retrieval & Intent Ranking](retrieval.md) |
| Work on prompts, local GGUF, providers, or safety | [Command Synthesis, Providers & Safety Verification](synthesis.md) |
| Inspect the warm daemon & `SemService` | [Warm Daemon & Service Layer](daemon.md) |
| Set up `Ctrl+S` and tab completions | [Shell Integration & Completions](shell-integration.md) |
| Troubleshoot or manage models and daemon | [Operations, Diagnostics & Model Lifecycle](operations.md) |
| Run tests, 37-query evals, or CI checks | [Development, Testing & Release Pipeline](development.md) |

## Privacy

All retrieval, parsing, and embedding run locally on your machine. Network activity is limited to optional `tldr-pages` / model downloads (`--offline` disables all downloads) and any remote synthesis endpoint you explicitly configure.
