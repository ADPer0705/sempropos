---
type: guide
title: Operations and Troubleshooting
description: Install, update, doctor, config, daemon, model, and provider workflows, index staleness detection, and remedies for common index, provider, daemon, and embedding failures.
tags: [operations, install, staleness, troubleshooting]
verified:
  - by: openwiki/0.9.0
    at: 2026-11-23T21:48:04.805Z
sources:
  - id: openwiki-source-401c3a8b8dfa9b92b6d17737
    resource: repo://src/sempropos/cli.py
  - id: openwiki-source-219b42e1aa58452b690c638d
    resource: repo://src/sempropos/config.py
  - id: openwiki-source-3ff6264169f17b0fd3181675
    resource: repo://src/sempropos/index/staleness.py
  - id: openwiki-source-0069c226d8bdc9dcf3d0ab51
    resource: repo://src/sempropos/intelligence/providers/_fastembed.py
generated: { by: "opencode", at: "2026-11-21T06:12:23.249Z" }
---

# Operations and Troubleshooting

Management commands are Typer subcommands in `src/sempropos/cli.py`. They write to the data and config directories described in [Configuration and Paths](configuration.md).

## `sem install`

First-time setup and the reliable full rebuild:

1. Prints a welcome panel and verifies SQLite FTS5 by creating an in-memory table with the Porter tokenizer. A missing FTS5 build exits 1. `man` is no longer checked because man pages are read directly from disk.
2. Downloads a curated GGUF model when `--model <tier>` is given and the run is not offline; a download failure is reported but does not abort setup.
3. Writes a default `config.toml` when none exists.
4. Prints the selected synthesis and embedding providers.
5. Runs `build_index(force=True)` with tldr, completions, and help probing enabled unless disabled.
6. Prints the build report, notifies a running daemon to reload, and warms the synthesis provider.

Options: `--offline`, `--no-tldr`, `--no-help-probe`, `--skip-embeddings`, and `--model <tiny|balanced|quality|none>`.

Because install uses `force=True`, it deletes and rebuilds the index, re-parses every man page, and recovers from corrupt or incompatible assets.

### Default configuration

When no configuration exists, `_suggest_initial_config` chooses:

- `local_gguf` when `--model` names an already-downloaded model, or when a local runtime and at least one managed model are present;
- otherwise `ollama` with `qwen2.5-coder:1.5b` when Ollama is reachable, preferring that model when installed and falling back to the first installed model;
- otherwise `tier0`.

Embedding defaults to `fastembed_local` with `nomic-ai/nomic-embed-text-v1.5-Q`, or `none` when `--skip-embeddings` is used.

## `sem update`

Runs `build_index(force=False)`, prints the build report, and notifies a running daemon. The current pipeline parses all discovered documentation but only writes tools whose content hash changed; it also prunes tools that are no longer installed and re-embeds new or changed tool ids.

Options: `--full` (force a complete rebuild), `--offline`, `--no-tldr`, `--no-help-probe`, `--skip-embeddings`.

`sem update` does not warm the synthesis provider; the first query after an update pays that cost.

## `sem doctor` (alias `sem check`)

Collects diagnostics through `SemService.doctor` and adds live daemon status. It reports:

- whether `index.meta` validates (`config.is_index_complete()`);
- the stored and expected index revision;
- the `last_indexed` timestamp, or `never`;
- whether an update is needed (`is_index_stale()`);
- corpus counts, flag/example coverage percentages, and per-source counts;
- installed PATH binaries missing from the index, with a sample;
- synthesis and embedding configuration, including `lazy_fallback`;
- local GGUF runtime and installed models;
- warm daemon pid, uptime, requests served, and socket.

`--json` emits the raw report. `--no-daemon` bypasses the warm daemon. The command exits 1 when the index is incomplete, so it works as a health check.

## `sem config` and `sem configure`

| Command | Behavior |
| --- | --- |
| `sem config show` | Print the resolved configuration as TOML, omitting `None` values |
| `sem config get section.field` | Print one value; a malformed key exits 2, an unknown key exits 3 |
| `sem config set section.field value` | Coerce and save one value; validates provider names and exits 2 for unsupported providers |
| `sem config wizard` / `sem configure` | Interactive provider and model selection |

`config set` coerces values to the current field type: booleans accept true/false/1/0/yes/no/on/off, numbers are parsed, and `none`/`null`/empty clears optional strings. After a successful set it notifies the daemon to reload.

The wizard:

1. lists available synthesis providers and exits 1 if none are available;
2. defaults to `local_gguf`, then `ollama`, then `openai_compatible`, then `tier0`;
3. for `openai_compatible`, asks for a base URL (default `https://api.openai.com/v1`), a model name, and an optional API key environment variable;
4. otherwise lists provider models with `get_provider_models` and asks for a selection;
5. repeats provider and model selection for embeddings;
6. saves the configuration and advises `sem update` when embeddings changed.

Cancelling a prompt exits 1. Provider configuration errors, such as an unreachable Ollama daemon, print the error and exit 1.

## `sem daemon`

| Command | Behavior |
| --- | --- |
| `sem daemon status` | Print running state, pid, uptime, requests, and socket; `--json` for machine output |
| `sem daemon start` | Start the daemon if needed; exits 1 on failure |
| `sem daemon stop` | Stop the daemon; reports when it was not running |
| `sem daemon restart` | Stop and start; exits 1 on failure |

The daemon is a warm Unix-socket process with a default 900-second idle timeout and auto-start on first query. Installation, update, model changes, and configuration changes call `notify_reload()` so a running daemon picks up new state.

## `sem model`

| Command | Behavior |
| --- | --- |
| `sem model list` | Show the curated catalog, install status, active model, and detected runtime |
| `sem model install [tier]` | Download a GGUF model (default `balanced`); `--use/--no-use` controls activation |
| `sem model use <id>` | Activate an installed model; exits 3 when not found |
| `sem model remove <id>` | Delete a managed model; exits 3 when not found |

Catalog tiers:

| Tier | Model | Approx. size | Minimum RAM |
| --- | --- | --- | --- |
| `tiny` | `qwen2.5-coder-0.5b` | 398 MB | 2 GB |
| `balanced` | `qwen2.5-coder-1.5b` | 986 MB | 4 GB |
| `quality` | `qwen2.5-coder-3b` | 1930 MB | 8 GB |

Models download from Hugging Face into `data_dir()/models`. A runtime is either `llama-cpp-python` or a `llama-server` binary in `data_dir()/bin` or on `PATH`; `sem model list` prints an install tip when neither is present. `sem model install` with an unknown tier exits 2.

## `sem providers`

Prints a Rich table of every supported synthesis and embedding provider with availability. This is the quickest way to see why a query fell back to retrieved tool cards.

## Staleness detection

`src/sempropos/index/staleness.py` compares `last_indexed` with the modification times of watched paths. The package database is the first existing path among:

| Distribution | Path |
| --- | --- |
| Debian/Ubuntu | `/var/lib/dpkg/status` |
| Arch | `/var/lib/pacman/local` |
| Fedora | `/var/lib/rpm/rpmdb.sqlite` |
| RPM legacy | `/var/lib/rpm/Packages` |
| Alpine | `/lib/apk/db/installed` |
| Homebrew ARM | `/opt/homebrew/Cellar` |
| Homebrew Intel | `/usr/local/Cellar` |
| Nix | `/nix/var/nix/profiles/default` |

It also watches `~/.local/bin` and `~/.cargo/bin` when they exist.

Behavior:

- a missing, empty, or unparsable `last_indexed` is treated as stale;
- a watched path newer than the marker makes the index stale;
- when no watched path exists, the index is not considered stale;
- timestamps are normalized to UTC epoch seconds;
- the check is a heuristic and does not cover every distribution.

At query time the warning is printed only in interactive output; `--porcelain` and `--json` suppress it. It never blocks a query.

## Common failures

### "Index is missing or incompatible... Run: sem update"

Raised by `read_validated_index_meta()` when metadata is absent or incomplete, the index revision differs, the database is missing, the stored embedding provider is unsupported, or the embedding revision, provider, model, dimension, or vector file does not match. Run `sem update`; if assets are corrupt or man pages need a clean re-parse, run `sem install`.

### `sem doctor` exits 1

The index is incomplete. Run `sem update` or `sem install`.

### "No relevant tools were found in the local index"

Retrieval returned no candidates. Check that `sem doctor` reports a complete index and that the tool is installed on `PATH`. A tool may still be absent when it has no man page, no tldr page, no completion script, and is unsafe to probe. Tools are no longer required to have a `man -k` whatis entry.

### Synthesis falls back to retrieved tool cards

`detect_synthesis_provider()` raises when the configured provider is unavailable, and a resolved `tier0` is treated as no backend. Check `sem providers`, then start Ollama, configure an OpenAI-compatible endpoint, or install a local GGUF model. Interactive queries still exit 0 with tier-0 cards.

### Ollama is reachable but the model is missing

`get_provider_models("ollama")` lists installed models. `sem config wizard` lets you select one; `sem install` warms the configured model when Ollama is available.

### OpenAI-compatible endpoint fails

The provider needs a base URL and a model. Availability requires a base URL from the role config or `SEMPROPOS_OPENAI_BASE_URL`, or a key from the configured env var, keyring, `api_key_plaintext`, or `SEMPROPOS_OPENAI_API_KEY`. HTTP errors are reported with status and a truncated body; unreachable endpoints raise an unavailable error.

### FTS5 is missing

`sem install` exits 1 with an explicit message when SQLite lacks FTS5 with the Porter tokenizer. Use a Python/SQLite build compiled with FTS5.

### First run is slow or offline embedding fails

The FastEmbed model is downloaded on first use into `data_dir()/embeddings`. After that the cache supports offline operation. A network failure during the first install prevents embedding; retry once the model can be fetched, run with `--skip-embeddings`, or configure `embedding.provider = "none"`.

### Daemon will not start or queries hang

Run `sem daemon status` and check the socket path, then `sem daemon restart`. Verify `SEMPROPOS_RUNTIME_DIR` is writable when it is set. `--no-daemon` bypasses the daemon entirely for a single query.

## Related pages

- [Quickstart](quickstart.md)
- [Configuration and Paths](configuration.md)
- [Indexing Pipeline](indexing.md)
- [Prompting and Synthesis Providers](synthesis.md)
