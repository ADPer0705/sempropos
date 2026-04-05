# sempropos

sempropos is a local, offline-first CLI tool that turns a natural-language task
into a shell command or pipeline by retrieving relevant context from local man
pages and synthesizing output with a local LLM.

After install-time indexing and runtime asset bootstrap, query-time operation is fully local.

## Features

- Local SQLite index of man pages (sections 1 and 8)
- Semantic retrieval over tool descriptions and flag descriptions
- Hybrid ranking (BM25 + embedding similarity + Reciprocal Rank Fusion)
- Runtime asset bootstrap via `sempropos --install`:
	- Downloads `llama-cli` into `~/.local/share/sempropos/bin/`
	- Downloads the Qwen 2.5 1.5B GGUF model into `~/.local/share/sempropos/models/`
	- Builds and refreshes the local index and embedding artifacts
- Local synthesis backend selection:
	- `llama-cli` (preferred)
	- Ollama (localhost)
	- tier0 structured fallback when no LLM backend is available
- Staleness warning when package DB changes after indexing

## Quick Start

### Option 1: One command installer (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/ADPer0705/sempropos/main/install.sh | bash
```

The installer script:

1. Ensures `python3` exists
2. Installs `pipx` (user scope) if missing
3. Installs sempropos with `pipx`
4. Runs `sempropos --install` to fetch runtime assets and build the index

### Option 2: Run script from repository

From repository root:

```bash
./install.sh
```

The script is idempotent and will:

1. Install sempropos with `pipx`
2. Run `sempropos --install` (asset bootstrap + indexing)

### Option 3: Manual development setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
sempropos --install
```

## Usage

### Main query mode

```bash
sempropos "list files in archive.7z"
sempropos "find all files larger than 100MB"
sempropos "monitor network traffic on eth0"
```

### Management commands

```bash
sempropos --install
sempropos --update
sempropos --check
```

`--install` and `--update` both ensure runtime assets are present, then build/update the index.

## Backend behavior

Backend selection order per query:

1. Local `~/.local/share/sempropos/bin/llama-cli` (or `llama-cli` in `PATH`)
2. Ollama if `127.0.0.1:11434` is reachable
3. tier0 fallback output (retrieved tools, key flags, and examples)

When package DB changes since last index, sempropos prints a warning and still
continues query processing.

## Data Layout

Default location:

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

## Development

### Run tests

```bash
python -m pip install pytest
python -m pytest -q
```

### CI/CD

GitHub Actions workflows are included:

1. CI workflow runs tests on push and pull requests across Python 3.11-3.13 and validates package build artifacts.
2. Publish workflow builds distributions and publishes to PyPI on release, with manual dispatch support for TestPyPI.

For publishing, configure trusted publishing in PyPI/TestPyPI for this repository.

### Project layout

```text
sempropos/
	cli.py
	config.py
	index/
	retrieval/
	synthesis/
tests/
	fixtures/
```

## Notes

- v1 scope is Linux only.
- v1 does not include cloud APIs, telemetry, or background daemons.
- Prompt synthesis quality depends on local index completeness and backend model
	availability.

