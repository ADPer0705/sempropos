---
type: reference
title: Configuration and Paths
description: Data, config, and runtime directory resolution, config.toml schema, provider and model settings, index metadata validation, revision constants, and API key resolution.
tags: [configuration, paths, toml, index-metadata]
verified:
  - by: openwiki/0.9.0
    at: 2026-11-23T21:48:04.805Z
sources:
  - id: openwiki-source-219b42e1aa58452b690c638d
    resource: repo://src/sempropos/config.py
  - id: openwiki-source-ea5077150cf30546a7983ff8
    resource: repo://src/sempropos/index/builder.py
  - id: openwiki-source-e6e2fa64cad475f151db6633
    resource: repo://src/sempropos/intelligence/contracts.py
  - id: openwiki-source-96f0681bdb464aa6414af509
    resource: repo://src/sempropos/intelligence/facade.py
  - id: openwiki-source-6c668f3544f6961d7a48ad3c
    resource: repo://src/sempropos/intelligence/providers/_openai_compatible.py
  - id: openwiki-source-02bc0bb1b5d2e945c282f049
    resource: repo://src/sempropos/utils.py
generated: { by: "opencode", at: "2026-11-21T06:12:23.249Z" }
---

# Configuration and Paths

`src/sempropos/config.py` is the single source of truth for directory resolution, file locations, provider lists, the configuration dataclasses, TOML persistence, and the index metadata contract used to reject incompatible indexes. `src/sempropos/utils.py` provides atomic file writes and keyring helpers.

## Directories

Directories derive from `platformdirs.PlatformDirs("sempropos", "adper")` with environment overrides:

| Purpose | Function | Override |
| --- | --- | --- |
| Data (index, embeddings, model and tldr caches) | `data_dir()` | `SEMPROPOS_DATA_DIR` |
| Configuration (`config.toml`) | `config_dir()` | `SEMPROPOS_CONFIG_DIR` |
| Runtime (daemon socket, pid, lock) | `runtime_dir()` | `SEMPROPOS_RUNTIME_DIR` |
| Embedding model cache | `embeddings_cache_dir()` | `<data>/embeddings` |
| Managed GGUF models | `models_dir()` | `<data>/models` |
| Managed `llama-server` binary | `bin_dir()` | `<data>/bin` |
| Offline tldr snapshot | `tldr_dir()` | `<data>/tldr` |

`runtime_dir()` follows this order: `SEMPROPOS_RUNTIME_DIR`; `<data>/run` when `SEMPROPOS_DATA_DIR` is set; `$XDG_RUNTIME_DIR/sempropos` when that directory exists; otherwise `<data>/run`. `ensure_data_dirs()` creates the data, config, embeddings, models, bin, and tldr directories.

## Data files

| File | Constant | Role |
| --- | --- | --- |
| `index.db` | `DB_FILE` | SQLite v3 index with tools, aliases, subcommands, flags, examples, and FTS5 tables |
| `tool_embeddings.npy` | `TOOL_EMBEDDINGS_FILE` | Float32 tool embedding matrix, memory-mapped at query time |
| `tool_embedding_ids.npy` | `TOOL_EMBEDDING_IDS_FILE` | Tool id vector aligned to the matrix rows |
| `index.meta` | `INDEX_META_FILE` | Index compatibility metadata |
| `last_indexed` | `LAST_INDEXED_FILE` | ISO timestamp used for staleness checks |
| `config.toml` | `CONFIG_FILE` | Active configuration (config directory) |
| `intelligence.toml` | `LEGACY_INTELLIGENCE_CONFIG_FILE` | Legacy configuration name, read only when `config.toml` is absent |

Daemon runtime files are `sem.sock`, `sem.pid`, and `sem.lock` under `runtime_dir()`. There are no flag-embedding files in the current index; flags are ranked through FTS5 and stored in SQLite.

## Model defaults and revisions

- `EMBEDDING_PRIMARY_MODEL` and `RECOMMENDED_EMBEDDING_MODEL` are `nomic-ai/nomic-embed-text-v1.5-Q`.
- `RECOMMENDED_LOCAL_MODEL_TIER` is `balanced`.
- `RECOMMENDED_SYNTHESIS_MODEL` (the Ollama recommendation) is `qwen2.5-coder:1.5b`.
- `EMBEDDING_REVISION = 3` invalidates stored vectors when the embedding representation changes.
- `INDEX_REVISION = 3` invalidates the SQLite index when parsing or schema changes.

Both revisions are written into `index.meta` and compared at query time. `sem update` rebuilds incompatible assets.

## Provider types

```python
SynthesisProviders = Literal["local_gguf", "ollama", "openai_compatible", "tier0"]
EmbeddingProviders = Literal["fastembed_local", "ollama", "none"]
```

`SUPPORTED_SYNTHESIS_PROVIDERS` and `SUPPORTED_EMBEDDING_PROVIDERS` are derived with `get_args`; `SUPPORTED_PROVIDERS` is their de-duplicated union. Defaults are `tier0` for synthesis and `fastembed_local` for embedding.

## Configuration dataclasses

`AppConfig` contains four frozen sections:

| Section | Class | Fields |
| --- | --- | --- |
| `[synthesis]` | `SynthesisConfig` | `provider`, `model`, `base_url`, `api_key_env`, `api_key_secret`, `api_key_plaintext`, `timeout_seconds`, `think`, `temperature`, `max_tokens`, `keep_alive`, `stop`, `n_ctx`, `n_gpu_layers`, `threads` |
| `[embedding]` | `EmbeddingConfig` | `provider`, `model`, `base_url`, `timeout_seconds`, `lazy_fallback` |
| `[daemon]` | `DaemonConfig` | `enabled`, `idle_timeout_seconds`, `auto_start` |
| `[indexing]` | `IndexingConfig` | `enable_tldr`, `enable_completions`, `probe_help`, `help_probe_timeout` |

Defaults worth knowing:

| Field | Default | Meaning |
| --- | --- | --- |
| `synthesis.provider` | `tier0` | Deterministic offline fallback unless changed |
| `synthesis.temperature` | `0.0` | Deterministic sampling |
| `synthesis.max_tokens` | `256` | Generation cap |
| `synthesis.keep_alive` | `"30m"` | Ollama model residency hint |
| `synthesis.n_ctx` | `2048` | Local GGUF context window |
| `synthesis.n_gpu_layers` | `-1` | Offload all layers when possible |
| `embedding.lazy_fallback` | `true` | Skip cold ONNX load when BM25 is decisive |
| `daemon.enabled` | `true` | Use the warm daemon when available |
| `daemon.idle_timeout_seconds` | `900` | Auto-shutdown after 15 idle minutes; minimum 30 |
| `daemon.auto_start` | `true` | Start the daemon on first query |
| `indexing.help_probe_timeout` | `1.5` | Per-binary `--help` timeout; minimum 0.2 |

`RoleConfig` and `IntelligenceConfig` remain import aliases for `SynthesisConfig` and `AppConfig`; the active names are the section-specific classes.

## TOML schema

```toml
[synthesis]
provider = "local_gguf"       # local_gguf | ollama | openai_compatible | tier0
model = "qwen2.5-coder-1.5b"  # tier, catalog model id, or GGUF path
# base_url, api_key_env, api_key_secret, api_key_plaintext,
# timeout_seconds, think, temperature, max_tokens, keep_alive, stop,
# n_ctx, n_gpu_layers, threads

[embedding]
provider = "fastembed_local"  # fastembed_local | ollama | none
model = "nomic-ai/nomic-embed-text-v1.5-Q"
lazy_fallback = true

[daemon]
enabled = true
idle_timeout_seconds = 900
auto_start = true

[indexing]
enable_tldr = true
enable_completions = true
probe_help = true
help_probe_timeout = 1.5
```

`sem config show` prints the resolved configuration as TOML; `sem config get section.field` and `sem config set section.field value` edit one key at a time; `sem config wizard` (also `sem configure`) walks through provider and model selection interactively.

Loading behavior (`load_config`):

- `config.toml` is preferred; `intelligence.toml` is used only when `config.toml` does not exist.
- A missing, unreadable, or malformed TOML file yields empty tables and therefore defaults.
- Provider names are lowercased, `-` becomes `_`, and an unrecognized or missing value falls back to the section default.
- String fields require strings; numeric fields accept `int`/`float` but reject booleans; `think` requires a bool; `stop` requires a list and is stringified item by item; `threads` must be a positive int.
- Out-of-range numeric values are clamped for `daemon.idle_timeout_seconds` (minimum 30) and `indexing.help_probe_timeout` (minimum 0.2).

Saving (`save_config`) strips `None` values with an `asdict` factory and writes atomically through `atomic_write_text`.

## Index metadata contract

`write_index_meta` records:

- `embedding_dim`, `embedding_model`, `embedding_provider`;
- `embedding_revision`, `index_revision`;
- `indexed_at`, `sempropos_version`, `status = "complete"`;
- optional `tool_count`, `flag_count`, `subcommand_count`, and `example_count`.

`read_validated_index_meta()` is the query-time gate. It raises `RuntimeError(MISMATCH_UPDATE_NOTICE)` when:

1. `index.meta` is missing or unparsable, or its `status` is present and not `"complete"`;
2. stored `index_revision` differs from `INDEX_REVISION`;
3. `index.db` is missing;
4. the stored embedding provider is not in the supported list.

When `embedding.provider = "none"`, validation returns `("", 0, "none")` after the checks above and does not require model, dimension, or vector files. Otherwise it further requires:

5. stored `embedding_revision` equals `EMBEDDING_REVISION`;
6. model name and positive dimension are present and the provider is not `none`;
7. the configured embedding provider equals the stored provider;
8. the configured embedding model, when set, equals the stored model;
9. `tool_embeddings.npy` exists.

On success the function returns `(model_name, embedding_dim, embedding_provider)`. `is_index_complete()` is a boolean wrapper. The remediation text `MISMATCH_UPDATE_NOTICE` tells the user to run `sem update`; `VERSION_UPDATE_NOTICE_TEMPLATE` is defined but not used by the current query path.

## API key resolution

For providers that need a key, `_resolve_api_key` in `src/sempropos/intelligence/facade.py` checks, in order:

1. the environment variable named by `api_key_env`;
2. the system keyring entry named by `api_key_secret` (service `sempropos`);
3. `api_key_plaintext`.

The OpenAI-compatible provider follows the same order and additionally recognizes `SEMPROPOS_OPENAI_BASE_URL` and `SEMPROPOS_OPENAI_API_KEY`. When an API key is available but no base URL is configured, it defaults to `https://api.openai.com/v1`; availability is defined as having either a base URL or an API key. Keyring helpers live in `src/sempropos/utils.py`.

## Related pages

- [Operations and Troubleshooting](operations.md)
- [Prompting and Synthesis Providers](synthesis.md)
- [Indexing Pipeline](indexing.md)
