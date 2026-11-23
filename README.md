# sem 🧰

[![PyPI version](https://badge.fury.io/py/sempropos.svg)](https://badge.fury.io/py/sempropos)
[![Python versions](https://img.shields.io/pypi/pyversions/sempropos.svg)](https://pypi.org/project/sempropos/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Build Status](https://github.com/ADPer0705/sempropos/actions/workflows/ci.yml/badge.svg)](https://github.com/ADPer0705/sempropos/actions)

`sem` (distributed as the `sempropos` package) is an **instant, offline-first CLI replacement for `apropos`, `--help`, and `man`**. It indexes every command installed on your machine into a canonical structured `ToolSpec` representation and gives you fast answers in three modes:

1. **Synthesize (`sem "..."` / `Ctrl+S`):** Turn plain English into a grounded, safety-checked shell command with optional flag-by-flag breakdowns.
2. **Inspect (`sem show <tool> [subcommand]`):** Instant 0ms-LLM structured cheat-sheet cards replacing `man` and `--help` (usage, subcommands, searchable flags, verified examples).
3. **Discover (`sem search <query>`):** Instant 0ms-LLM ranked tool search replacing `apropos`.

## ✨ Key Features

- **Canonical Structured `ToolSpec` Pipeline:** Merges four complementary local sources into one typed, content-hashed SQLite v3 index:
  - Direct **`man(7)` / `mdoc(7)` macro parser** (reads compressed `.gz`/`.xz`/`.bz2`/`.zst` man pages directly from disk, capturing GNU coreutils flags inside `DESCRIPTION` and filtering out prose from `EXAMPLES`).
  - Offline **`tldr-pages` cache/snapshot** (task-focused summaries, verified examples, and hyphenated subcommands like `git-commit`, `docker-run`).
  - Static **Fish & Zsh shell completion parsers** (zero code execution).
  - **Safe `--help` probe** for non-man `$PATH` binaries (`7z`, `uv`, `ruff`, `cargo`, custom CLIs) with strict denylist, setuid exclusion, headless env, and hard timeouts.
- **High-Precision Local Retrieval:** Porter-stemmed multi-column SQLite FTS5 (`name`, `aliases`, `summary`, `tags`, `subcommands`, `examples`, `flags`), file-extension/artifact hints (`.7z`, `.tar.gz`, `.json`, `.service`), compound intent matching, and confidence-gated vector recall (`fastembed`).
- **Task-Tailored Local Models + Clean External Providers:**
  - **Built-in managed GGUF (`local_gguf`):** Curated small CLI-tuned models (`tiny` 0.5B, `balanced` 1.5B, `quality` 3B) with JSON-schema grammar constraints via `sempropos[local-llm]` (`llama-cpp-python`) or standalone `llama-server`.
  - **Pure-HTTP Ollama & OpenAI-compatible:** Zero extra SDKs, structured JSON output, configurable endpoints.
  - **Tier-0 deterministic mode:** Instant zero-LLM example matching and structured ToolSpec cards when offline or unconfigured.
- **Auto-Managed Warm Daemon:** Lightweight Unix-domain-socket daemon (`0600` private socket) with automatic idle shutdown (15m default) keeps SQLite, embeddings, and local models hot for sub-200ms queries and `Ctrl+S` transforms—with transparent in-process fallback.
- **Command Grounding & Safety Warnings:** Verifies the executable exists on `$PATH`, cross-checks generated flags against `ToolSpec` to power `--explain`, and warns clearly on `caution` / `destructive` operations (`rm -rf`, `find -delete`, `dd of=/dev/...`, `git reset --hard`, `curl | bash`, etc.).

## 🚀 Quick Start

```bash
uv tool install sempropos
sem install                     # build structured v3 index (man + tldr + completions + safe --help)
```

Optional: enable built-in local GGUF synthesis without Ollama:

```bash
uv tool install "sempropos[local-llm]"   # or install llama-server on PATH
sem model install balanced               # downloads Qwen2.5-Coder-1.5B (~1 GB) & activates it
```

## 📖 Usage

### 1. Command Synthesis (`ask` / bare query)

```bash
sem "find files larger than 100MB"
sem -e "delete files older than 30 days"      # includes flag-by-flag breakdown + safety warning
sem -t tar "extract archive to /tmp"          # scope to a single tool
sem -n 3 "sync directory over ssh"            # request alternative commands
sem --porcelain "list open listening ports"   # raw command only (for scripts/widgets)
sem --json "show disk usage by folder"        # full machine-readable output
```

### 2. Instant Tool Reference Card (`show` — replaces `man` & `--help`)

```bash
sem show du                       # summary, usage, examples, top flags
sem show du --flags depth         # filter flags by keyword
sem show git commit               # inspect a specific subcommand
sem show 7z --examples            # verified usage examples only
sem show tar --json               # export canonical ToolSpec JSON
```

### 3. Instant Tool Discovery (`search` — replaces `apropos`)

```bash
sem search "disk usage by folder"
sem search "listening tcp ports"
sem search "archive" --category archive --limit 15
```

### 4. Management & Diagnostics

```bash
sem doctor               # index health, parse coverage %, unindexed PATH bins, daemon, providers
sem update               # incremental content-hash refresh + prune uninstalled binaries
sem model list           # list curated GGUF tiers and local runtime status
sem model install tiny   # download & activate tiny 0.5B GGUF model
sem daemon status        # inspect warm background daemon (start|stop|restart|status)
sem config show          # view resolved config.toml (also: config get/set/wizard)
sem providers            # check availability of synthesis and embedding backends
sem init zsh             # print Ctrl+S integration + tab completions (zsh, bash, fish)
```

## 🐚 Shell Integration (`Ctrl+S` + Tab Completion)

Add one line to your shell startup file:

```bash
# ~/.zshrc
eval "$(sem init zsh)"

# ~/.bashrc
eval "$(sem init bash)"

# ~/.config/fish/config.fish
sem init fish | source
```

Type a plain-English task on your prompt and press **`Ctrl+S`** — the line is replaced in place with the generated command (never auto-executed). Tab completion also completes `sem` subcommands and indexed tool names for `sem show <TAB>`.

## 📁 Data & Configuration Layout

- **Config:** `~/.config/sempropos/config.toml` (overridable via `SEMPROPOS_CONFIG_DIR`; automatically reads legacy `intelligence.toml`)
- **Data:** `~/.local/share/sempropos/` (overridable via `SEMPROPOS_DATA_DIR`)
  - `index.db` — SQLite v3 + Porter-stemmed FTS5
  - `tool_embeddings.npy`, `tool_embedding_ids.npy` — `tool_id`-keyed L2-normalized tool-card embeddings
  - `index.meta`, `last_indexed` — atomic revision and staleness markers
  - `tldr/` — offline `tldr-pages` snapshot
  - `models/` — downloaded local GGUF models
  - `embeddings/` — pinned FastEmbed ONNX model cache
- **Runtime socket:** `$XDG_RUNTIME_DIR/sempropos/sem.sock` (`0600`, auto-cleaned on idle shutdown)

## 🧪 Development & Testing

```bash
uv sync --group dev
make check-all          # ruff lint + format check + strict mypy + pytest (unit, integration, 37-query eval)
```

All default tests run 100% offline with zero API keys required.

## 📄 License

MIT License — see [LICENSE](LICENSE).
