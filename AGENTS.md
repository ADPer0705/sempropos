# Welcome to the sempropos architecture guide!

> **sempropos** is a smart linux CLI tool that takes a natural language task description
> and returns the exact shell command or pipeline to accomplish it — by doing semantic retrieval
> over installed man pages and synthesizing a command with the help of a  LLM.

---

## What the tool does

```
Install time --> parse all man pages --> build SQLite index + embedding vectors
Query time   --> retrieve relevant tools + flags --> synthesize command via LLM
```

---

## Repository Layout

```
sempropos/
├── AGENTS.md                  <-- this file
├── README.md
├── pyproject.toml             <-- packaging metadata
├── install.sh                 <-- install script for Linux
├── src/
│   └── sempropos/
│       ├── __init__.py
│       ├── cli.py                 <-- entry point, argument parsing
│       ├── index/                 <-- indexing pipeline and SQLite schema
│       ├── intelligence/          <-- Intelligence layer: embeddings and command synthesis
│       ├── retrieval/             <-- BM25, semantic search, RRF, flag retrieval
│       ├── synthesis/             <-- prompt construction, backend detection, LLM call
│       └── config.py              <-- paths, constants, backend detection
└── tests/
```

---

## Data Directory Layout

```
~/.local/share/sempropos/
  index.db                     <-- SQLite database (schema below)
  tool_embeddings.npy          <-- float32 array, shape [N_tools, D]
  flag_embeddings.npy          <-- float32 array, shape [N_flags, D]
  flag_embedding_ids.npy       <-- int32 array mapping flag_embeddings rows → flags.id
  index.meta                   <-- embedding model/provider + sempropos version
  last_indexed                 <-- plaintext ISO timestamp
```

---



## SQLite schema

Three tables: `tools`, `flags`, `examples`.

`tools` — one row per installed man page (sections 1 and 8 only).
Fields: `id`, `name`, `section`, `description` (one-liner from `man -k`), `synopsis`.

`flags` — one row per parsed flag, foreign-keyed to `tools`.
Fields: `id`, `tool_id`, `flag`, `long_flag`, `takes_value`, `value_hint`, `description`.

`examples` — one row per parsed example, foreign-keyed to `tools`.
Fields: `id`, `tool_id`, `command`, `context`.

Source: `man -k . -s 1,8` for the tool list. `man -P cat <tool>` for page content.

---

## CLI commands

| Command | Behaviour |
|---------|-----------|
| `sempropos "<query>"` | Main query flow |
| `sempropos --install` | Download models + build full index |
| `sempropos --update` | Rebuild index (skip downloads if assets present) |
| `sempropos --check` | Print staleness status |
| `sempropos --setup` | Interactive setup for synthesis/embedding providers |
| `sempropos --set-synthesis-provider <name>` | Persist synthesis provider |
| `sempropos --set-embedding-provider <name>` | Persist embedding provider |
| `sempropos --set-embedding-model <name>` | Persist embedding model |
| `sempropos --show-config` | Print current runtime intelligence config |
| `sempropos --list-providers` | List all registered providers and their status |

On every query: run a staleness check and print a one-line warning if stale. Do not
block the query.

---

## Indexing

Index source is `man -k . -s 1,8`. Parse each page for SYNOPSIS, OPTIONS/FLAGS, and
EXAMPLES sections. On failure for any individual page: log a warning, continue — never
abort the full index build for one bad page.

The index build must be resumable — re-running after an interruption skips already-indexed
tools.

Staleness is detected by comparing the mtime of the distro package DB against the
`last_indexed` timestamp. Supported paths:

| Distro | Package DB path |
|--------|----------------|
| Debian/Ubuntu | `/var/lib/dpkg/status` |
| Arch | `/var/lib/pacman/sync` |
| Fedora/RHEL | `/var/lib/rpm/Packages` |
| Alpine | `/lib/apk/db/installed` |

---

## Retrieval pipeline (query time)

```
1. Query expansion      — static synonym table, no model, ~0ms
2. BM25 search          — over tools.description, rank_bm25.BM25Okapi
3. Semantic search      — embed query, cosine sim over tool_embeddings.npy
4. RRF fusion           — Reciprocal Rank Fusion of BM25 + semantic lists → top-5 tools
5. Flag retrieval       — for each candidate tool, cosine sim over that tool's flag
                          embeddings → top-6 most relevant flags
6. Prompt construction  — SYNOPSIS + relevant flags + examples, target < 600 tokens
7. LLM synthesis        — via intelligence module
```

Embeddings are pre-normalized at index time. Query-time cosine similarity is a numpy
dot product — no vector DB, no sklearn.

---

## Intelligence module

The `intelligence/` module is the single integration point for all LLM providers.
Nothing outside this module makes direct LLM calls.

### Design

**`contracts.py`** — defines the provider interface. Every provider implements this.
A provider receives a fully-constructed prompt string and returns a response string.
Providers do not build prompts — that is `synthesis/prompt.py`'s responsibility.

**`base.py`** — abstract base class for all providers.

**`registry.py`** — maps provider names to their classes. Adding a new provider means
registering it here and implementing `base.py`.

**`policy.py`** — provider selection logic. Determines which provider to use at runtime
based on: user config, environment detection, and availability checks. See selection
order below.

**`facade.py`** — the only public interface for the rest of the codebase. Callers do:
```python
from sempropos.intelligence.facade import complete
result = complete(prompt)
```

**`config.py`** — persists user's provider preference to disk.

### Registered providers

| Provider | File | Requires |
|----------|------|---------|
| `llama_cpp` | `llama_cpp.py` | local llama-cli binary + GGUF |
| `ollama` | `ollama.py` | Ollama running on localhost:11434 |
| `openai_compatible` | `openai_compatible.py` | API key + base URL (env vars) |
| `anthropic` | `anthropic.py` | `ANTHROPIC_API_KEY` env var |
| `gemini` | `gemini.py` | `GEMINI_API_KEY` env var |
| `mistral` | `mistral.py` | `MISTRAL_API_KEY` env var |
| `huggingface` | `huggingface.py` | `HF_TOKEN` env var |
| `tier0` | `tier0.py` | nothing — structured fallback output, no LLM |

### Provider selection order (`policy.py`)

```
1. User-persisted preference (from --set-synthesis-provider) if available + healthy
2. llama_cpp                                          if binary + GGUF present
3. ollama                                             if running + compatible model found
4. openai_compatible                                  if OPENAI_API_KEY or OPENAI_BASE_URL set
5. anthropic                                          if ANTHROPIC_API_KEY set
6. gemini                                             if GEMINI_API_KEY set
7. mistral                                            if MISTRAL_API_KEY set
8. huggingface                                        if HF_TOKEN set
9. tier0                                              always available, last resort
```

"Healthy" means the provider can be reached without error. Do a lightweight probe before
committing (socket check for ollama, file existence for llama_cpp, env var presence for
API providers). Do not make actual LLM calls during provider selection.

### Ollama model preference

When Ollama is selected, check available models and pick in this order:
```
gemma4:e2b  →  qwen3:1.7b  →  qwen3:0.6b  →  gemma3:1b
```
If none of these are present, do not auto-pull. Print a one-line advisory and fall
through to the next provider in the selection order.

### Tier 0 fallback output

When no LLM is available, print the top matched tools with their synopsis and key flags.
Make clear to the user that synthesis requires a provider and how to configure one.

---

## Embeddings

Embeddings live in `intelligence/embeddings/fastembed_local.py`. This is separate from
the synthesis providers because embeddings are used at both index time and query time,
not just for synthesis.

Do not use PyTorch or `sentence-transformers`. Use `fastembed` (ONNX Runtime backend).
The embedding model is managed entirely by fastembed — do not manually download or
cache embedding model files.

The embedding model used at index time must be recorded in `index.meta`. At query time,
the same model must be loaded. If there is a mismatch, abort and prompt the user to
run `sempropos --update`.

---

## Synthesis prompt

Target: under 600 tokens. Content: system instruction, task line, then for each
candidate tool — synopsis, relevant flags, examples. Omit missing sections silently.

System instruction: output only the exact shell command or pipeline. No explanation,
no markdown, no preamble.

For Qwen3 providers: append `/no_think` to the prompt to disable chain-of-thought.
For Gemma 4 E2B: thinking is off by default in instruction-tuned mode, no flag needed.

---

## Dependencies

```toml
[project]
dependencies = [
    "rank-bm25",
    "fastembed",
    "numpy",
    "tqdm",
    "requests",
    "huggingface_hub",
  "psutil",
  "tomli; python_version < '3.11'",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-cov", "ruff", "build", "twine"]
```

Do not add: `torch`, `transformers`, `sentence-transformers`, `langchain`, `llama-index`,
`chromadb`, `faiss`, or any agent/orchestration framework.

---

## Testing policy

- Tests must run without API keys by default.
- Live network/API access is disallowed in standard test runs.
- Provider tests should use mocks/stubs unless explicitly marked and opted-in.
- Coverage gate target is >= 65%.

---

## install.sh

Responsibilities in order:
1. Detect OS and CPU arch — exit with advisory if unsupported
2. Detect available RAM — select primary or floor model tier
3. Check for existing Ollama + compatible model — skip GGUF download if found
4. Download llama-cli binary (GitHub releases, pinned version)
5. Download synthesis GGUF (HuggingFace, via `huggingface_hub`, pinned version)
6. Run `sempropos --install` (triggers fastembed model download + index build)

Must be idempotent — re-running skips steps whose outputs already exist and are valid.

---

## Non-goals

- GUI or TUI
- Cloud sync or telemetry of any kind
- Persistent background process or daemon
- Man page sections other than 1 and 8
- Windows or macOS support
- LLM fine-tuning or training
- Auto-pulling Ollama models without user consent

---

## Definition of done

- [ ] `sempropos --install` completes without error on a fresh Debian/Ubuntu system
- [ ] `sempropos "list files in archive.7z"` returns a correct `7z` invocation
- [ ] `sempropos --set-synthesis-provider ollama` persists and is respected on next query
- [ ] `sempropos --list-providers` shows all providers with availability status
- [ ] Tier 0 produces readable output when no provider is available
- [ ] Staleness warning fires after a simulated package DB mtime change
- [ ] Full tests pass without API keys or live network
- [ ] Coverage gate (>= 65%) passes
