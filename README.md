# sempropos 🧰

[![PyPI version](https://badge.fury.io/py/sempropos.svg)](https://badge.fury.io/py/sempropos)
[![Python versions](https://img.shields.io/pypi/pyversions/sempropos.svg)](https://pypi.org/project/sempropos/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Build Status](https://github.com/ADPer0705/sempropos/actions/workflows/ci.yml/badge.svg)](https://github.com/ADPer0705/sempropos/actions)

`sempropos` is an open-source, local, offline-first CLI tool that turns a natural-language task description into a shell command. It does this by retrieving relevant context from your system's local man pages and synthesizing the output using a tiny local LLM.

Say goodbye to complex web searches just to find the right flag—`sempropos` keeps it entirely on your machine.

## ✨ Features

- **Privacy First, Offline First:** No API keys, no telemetry, no cloud backend. Everything runs locally natively.
- **Local SQLite Index:** Fast retrieval of man pages (sections 1 and 8).
- **Semantic Retrieval:** Hybrid ranking via BM25, embedding similarity, and Reciprocal Rank Fusion.
- **Local LLM backend:** Synthesizes terminal commands using `llama.cpp` + a tiny Qwen 2.5 1.5B GGUF model, or Ollama.
- **Smart staleness checks:** Detects when your package manager updates software and gently prompts an index refresh.

## 🚀 Quick Start

### Option 1: One-Command Installer (Recommended)

Simply pipe the installer script into bash (make sure you have `curl` and Python installed!):

```bash
curl -fsSL https://raw.githubusercontent.com/ADPer0705/sempropos/main/install.sh | bash
```

The script will automatically set up `pipx`, install `sempropos`, download the runtime assets (`llama-cli` and the LLM model), and build the local index.

### Option 2: Install from Source

```bash
git clone https://github.com/ADPer0705/sempropos.git
cd sempropos

# 1. Setup a virtual environment & install dependencies
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .

# 2. Bootstrap runtime assets & download LLM model
sempropos --install
```

## 📖 Usage

### The Main Query

Describe what you want to do in natural language:

```bash
sempropos "list files in archive.7z"
sempropos "find all files larger than 100MB"
sempropos "monitor network traffic on eth0"
```

If it succeeds, you'll be handed the correct shell invocation instantly!

### Management Commands

```bash
sempropos --check    # Check if a new package installation requires updating the man page index
sempropos --update   # Update the index with new installed programs
sempropos --install  # (Re)download runtime assets and (re)build the index completely
```

## 🧠 Backend behavior

Backend selection order per query:

1. Local `~/.local/share/sempropos/bin/llama-cli` (or `llama-cli` in `PATH`)
2. Ollama if `127.0.0.1:11434` is reachable
3. tier0 fallback output (retrieved tools, key flags, and examples)

When your package database changes since the last index, sempropos prints a non-blocking warning so you know it's time to run `--update`.

## 📁 Data Layout

Default location for data assets:

```text
~/.local/share/sempropos/
	index.db
	tool_embeddings.npy
	flag_embeddings.npy
	flag_embedding_ids.npy
	last_indexed
	models/
		qwen2.5-1.5b-instruct-q4_k_m.gguf
	bin/
		llama-cli
```

Override data location with:

```bash
export SEMPROPOS_DATA_DIR=/path/to/sempropos-data
```

## 🤝 Contributing

We welcome contributions from everyone! Whether you're fixing bugs, adding new features, or improving documentation, your help makes `sempropos` better for the entire community.

Please read our [CONTRIBUTING.md](CONTRIBUTING.md) for conventions on pull requests, code style, and reporting issues.

For a deep dive into how `sempropos` indexes man pages, retrieves vectors and generates commands, read the [Architecture Guide (AGENTS.md)](AGENTS.md).

## 🧪 Testing & Quality

Run the full test suite with the project coverage gate:

```bash
python -m pytest tests/ --cov=src/sempropos --cov-report=term-missing --cov-report=html --cov-fail-under=65
```

Quality policy for this repository:

- CI coverage gate is **65% minimum**.
- Provider tests are **mocked/offline by default** for deterministic CI runs.
- Optional live provider smoke checks should be local-only and guarded by explicit env vars/markers.
- Running tests should **not require any API key**.

You can also use the Makefile shortcuts:

```bash
make test      # full suite + coverage gate
make coverage  # full suite + coverage report only
```

## 📄 License

This project is licensed under the [MIT License](LICENSE).

## 💬 Community & Support

- If you found a bug or have a feature request, please [open an issue](https://github.com/ADPer0705/sempropos/issues).
- Want to chat or ask a question? Join the discussion on GitHub Discussions.

