# sem 🧰

[![PyPI version](https://badge.fury.io/py/sempropos.svg)](https://badge.fury.io/py/sempropos)
[![Python versions](https://img.shields.io/pypi/pyversions/sempropos.svg)](https://pypi.org/project/sempropos/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Build Status](https://github.com/ADPer0705/sempropos/actions/workflows/ci.yml/badge.svg)](https://github.com/ADPer0705/sempropos/actions)

`sem` (distributed as the `sempropos` package) is an open-source, local,
offline-first CLI tool that turns a natural-language task description into a
shell command. It retrieves relevant context from your system's local man pages
and synthesizes the output using a local model.

Say goodbye to complex web searches just to find the right flag — `sem` keeps it
entirely on your machine.

## ✨ Features

- **Privacy First, Offline First:** No API keys, no telemetry, no cloud backend required. Run entirely on your machine.
- **Local SQLite Index:** Fast retrieval of man pages (sections 1 and 8).
- **Semantic Retrieval:** BM25-primary hybrid ranking with an exact-tool-name boost. Embedding similarity adds recall and breaks exact ties, so a weak embedding match can never outrank a confident lexical one.
- **Fast local inference:** Uses Ollama (with reasoning disabled and the model kept warm) for near-instant answers, and falls back to showing matched man pages when no model is available.
- **Pluggable providers:** Ollama out of the box, plus any OpenAI-compatible endpoint (vLLM, LM Studio, llama.cpp server, a gateway…). More providers are on the way.
- **Shell integration:** Press `Ctrl+S` to turn the natural-language sentence you just typed into a command, in place.
- **Smart staleness checks:** Detects when your package manager updates software and gently prompts an index refresh.

## 🚀 Quick Start

### Install with `uv` (recommended)

```bash
uv tool install sempropos
sem install          # check requirements, download the embedding model, build the index
```

### Install from source

```bash
git clone https://github.com/ADPer0705/sempropos.git
cd sempropos
uv venv && uv sync
uv run sem install
```

`sem install` writes a default configuration, downloads the embedding model into
the local data directory (so it works offline afterwards), and builds the index.
If Ollama is running it is selected as the synthesis backend; otherwise `sem`
still works and falls back to showing matched man pages.

## 📖 Usage

### The Main Query

Describe what you want to do in natural language. A bare query works, and `ask`
is available when you want to be explicit:

```bash
sem "list files in archive.7z"
sem ask "find all files larger than 100MB"
sem ask "monitor network traffic on eth0"
```

If it succeeds, you'll be handed the correct shell invocation instantly.

### Commands

Management is done with subcommands, not flags:

```bash
sem install      # First-time setup: check requirements, write config, build the index
sem update       # Refresh the index with newly installed tools (incremental)
sem check        # Check index freshness and completeness
sem configure    # Choose synthesis/embedding providers interactively
sem providers    # List providers and whether they are available
sem init zsh     # Print shell integration for zsh (also: bash, fish)
sem version      # Print the version
```

## 🐚 Shell integration (Ctrl+S)

Add one line to your shell rc, then reload it:

```bash
# ~/.zshrc  (or ~/.bashrc; use `sem init fish` for fish)
eval "$(sem init zsh)"
```

Now, instead of running `sem` as a separate command, type the natural-language
task directly on your prompt and press **Ctrl+S**. The line is replaced in place
with the generated command, ready to review and run:

```text
❯ find all files larger than 100MB        # press Ctrl+S
❯ find . -type f -size +100M
```

Notes:

- The snippet runs `stty -ixon`, which frees `Ctrl+S` from the terminal's
  XOFF/flow-control behavior. If your terminal still intercepts it, you can
  re-bind a different key (for example `^G`) by editing the `bindkey`/`bind`
  line.
- `Ctrl+R` (history) and `Ctrl+T` (files) are left to [fzf](https://github.com/junegunn/fzf);
  `sem` only adds `Ctrl+S`.
- The command is inserted, never executed — you always get the final say.

## 🧠 Backend behavior

Synthesis backend selection order per query:

1. **Ollama** if the daemon is reachable, using the configured model.
2. **OpenAI-compatible** endpoint if one is configured (`base_url`).
3. **tier0** fallback: retrieved tools, key flags, and examples, with no LLM.

To keep queries fast, Ollama is called with reasoning/thinking disabled, a
temperature of `0`, a bounded output length, and a long `keep_alive` so the
model stays warm between invocations. `sem install` / `update` warm the model
once so your first real query is not the one paying the load cost.

The default recommendation is a small instruct model (see
`RECOMMENDED_SYNTHESIS_MODEL`) that stays fast on most consumer hardware while
remaining capable when grounded by retrieved man-page context. `sem configure`
lets you opt into a larger model or a remote endpoint.

When your package database changes since the last index, `sem` prints a
non-blocking warning so you know it's time to run `sem update`.

## 📁 Data Layout

Default location for data assets:

```text
~/.local/share/sempropos/
	index.db
	tool_embeddings.npy
	tool_embedding_ids.npy
	index.meta
	last_indexed
	embeddings/          # pinned embedding-model cache (kept for offline use)
```

Flag ranking uses the SQLite FTS5 index, so no flag embedding files are needed.

Override the data location with:

```bash
export SEMPROPOS_DATA_DIR=/path/to/sempropos-data
```

## 🤝 Contributing

We welcome contributions from everyone! Whether you're fixing bugs, adding new
features, or improving documentation, your help makes `sem` better for the
entire community.

Please read our [CONTRIBUTING.md](CONTRIBUTING.md) for conventions on pull
requests, code style, and reporting issues.

## 🗺️ Roadmap

Known gaps and planned work live in [ROADMAP.md](ROADMAP.md). It is kept short
and concrete — a good place to find a first contribution.

## 🧪 Testing & Quality

> Tests are being rebuilt. Until the suite returns, CI installs the package and
> verifies that every module imports cleanly on Python 3.11–3.13.

The quality policy for this repository remains:

- Running the tool must never require an API key.
- Provider integration tests are **mocked/offline by default** for deterministic CI runs.
- Optional live provider smoke checks are local-only and guarded by explicit env vars/markers.

Local checks:

```bash
make lint     # ruff (when development dependencies are installed)
make format   # ruff format + autofix
```

## 📄 License

This project is licensed under the [MIT License](LICENSE).

## 💬 Community & Support

- If you found a bug or have a feature request, please [open an issue](https://github.com/ADPer0705/sempropos/issues).
- Want to chat or ask a question? Join the discussion on GitHub Discussions.
