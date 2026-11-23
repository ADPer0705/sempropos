---
type: guide
title: Quickstart
description: Install sem, run first-time setup, issue a natural-language query, enable Ctrl+S shell integration, and find the right page for deeper detail.
tags: [quickstart, installation, usage, offline]
verified:
  - by: openwiki/0.9.0
    at: 2026-11-21T06:12:23.249Z
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
  - id: openwiki-source-8f55faa7a495acc24641ff9a
    resource: repo://src/sempropos/shell.py
generated: { by: "opencode", at: "2026-11-21T06:12:23.249Z" }
---

# Quickstart

`sem` (distributed as the `sempropos` package) is a local, offline-first CLI that turns a natural-language task into a shell command. It retrieves context from your system's man pages and synthesizes a command with a local model. No API key, telemetry, or cloud backend is required.

## Requirements

- Linux/POSIX with `man` and a SQLite build that includes FTS5.
- Python 3.11 or newer.
- Optional: [Ollama](https://ollama.com/) for synthesis. Without it, `sem` still retrieves and displays relevant man pages.

## Install

### With `uv` (recommended)

```bash
uv tool install sempropos
sem install
```

### From source

```bash
git clone https://github.com/ADPer0705/sempropos.git
cd sempropos
uv venv && uv sync
uv run sem install
```

`sem install` checks for FTS5 and `man`, writes a default `intelligence.toml`, downloads the embedding model into the application data directory, builds the man-page index, and warms the synthesis model when Ollama is reachable.

## First query

Describe the task in plain language:

```bash
sem "list files in archive.7z"
sem ask "find all files larger than 100MB"
sem ask "monitor network traffic on eth0"
```

A bare query is automatically routed to the `ask` command. On success you get a generated shell command; the command is shown for review and is never executed automatically.

Useful query options:

```bash
sem --backend ollama "show disk usage by folder"
sem --porcelain "find markdown files"    # command only on stdout
```

`--porcelain` is intended for scripts and shell widgets: the command is the only stdout output, diagnostics go to stderr, and failures exit non-zero.

## Shell integration (Ctrl+S)

Add one line to your shell startup file:

```bash
# ~/.zshrc or ~/.bashrc
eval "$(sem init zsh)"     # or: eval "$(sem init bash)"

# fish
sem init fish | source
```

Now type a natural-language task at the prompt and press **Ctrl+S**:

```text
❯ find all files larger than 100MB        # press Ctrl+S
❯ find . -type f -size +100M
```

The line is replaced in place and left for you to review. The snippet runs `stty -ixon` so the terminal does not swallow Ctrl+S. See [Shell Integration](shell-integration.md) for binding details and caveats.

## Management commands

```bash
sem install      # first-time setup: check requirements, write config, build index
sem update       # incrementally add newly installed tools
sem check        # report index completeness and staleness
sem configure    # choose synthesis and embedding providers interactively
sem providers    # list providers and whether they are available
sem init zsh     # print shell integration (also bash, fish)
sem version      # print the version
```

Details and failure remedies are in [Operations and Troubleshooting](operations.md).

## How it works

1. The query is tokenized, singularized, and expanded with synonyms.
2. BM25 over man-page descriptions provides the primary ranking, with a boost for exact tool-name matches.
3. Embedding cosine similarity adds recall; it can break exact lexical ties but cannot outrank a confident lexical hit.
4. The top tools, their relevant flags, and examples are assembled into a budgeted prompt.
5. Ollama or an OpenAI-compatible endpoint synthesizes one command. Without a usable backend, the retrieved man pages are shown directly.

The full pipeline is described in [Architecture Overview](architecture.md) and [Retrieval and Ranking](retrieval.md).

## Data location

By default, runtime assets live under the platform user data directory (`platformdirs` for `sempropos`), with configuration under the user config directory. Override them with:

```bash
export SEMPROPOS_DATA_DIR=/path/to/sempropos-data
export SEMPROPOS_CONFIG_DIR=/path/to/sempropos-config
```

The data directory contains `index.db`, `tool_embeddings.npy`, `tool_embedding_ids.npy`, `index.meta`, `last_indexed`, and the cached embedding model under `embeddings/`. See [Configuration and Paths](configuration.md).

## Where to go next

| If you want to… | Read |
| --- | --- |
| Understand the whole request flow | [Architecture Overview](architecture.md) |
| Learn the commands and exit codes | [CLI Surface and Query Dispatch](cli.md) |
| Configure providers, paths, and keys | [Configuration and Paths](configuration.md) |
| Understand index building and parsing | [Indexing Pipeline](indexing.md) |
| Inspect the SQLite/FTS5 layout | [Index Database Schema](index-schema.md) |
| Understand ranking and fusion | [Retrieval and Ranking](retrieval.md) |
| Work on prompts or add a provider | [Prompting and Synthesis Providers](synthesis.md) |
| Set up Ctrl+S | [Shell Integration](shell-integration.md) |
| Fix an index or provider problem | [Operations and Troubleshooting](operations.md) |
| Contribute or run CI checks | [Development and Quality](development.md) |

## Privacy

All retrieval and embedding run locally. The only network activity is the optional first-time embedding-model download and any configured remote synthesis endpoint. Ollama and FastEmbed operate on your machine.
