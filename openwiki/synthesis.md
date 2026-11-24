---
type: architecture
title: Command Synthesis, Providers & Safety Verification
description: Provider contracts, lazy facade dispatch, managed local GGUF models, pure-HTTP Ollama and OpenAI-compatible backends, FastEmbed embeddings, Tier-0 deterministic fallback, JSON-schema prompt budgeting, and command safety verification.
tags: [synthesis, prompts, local-gguf, ollama, openai, embeddings, safety, providers]
verified:
  - by: openwiki/0.9.0
    at: 2026-11-23T21:48:04.805Z
sources:
  - id: openwiki-source-e6e2fa64cad475f151db6633
    resource: repo://src/sempropos/intelligence/contracts.py
  - id: openwiki-source-96f0681bdb464aa6414af509
    resource: repo://src/sempropos/intelligence/facade.py
  - id: openwiki-source-a94edd5f1a01c22cc4966cf2
    resource: repo://src/sempropos/intelligence/models.py
  - id: openwiki-source-c8919dac9c46b272e6ad65f8
    resource: repo://src/sempropos/intelligence/prompt.py
  - id: openwiki-source-0069c226d8bdc9dcf3d0ab51
    resource: repo://src/sempropos/intelligence/providers/_fastembed.py
  - id: openwiki-source-00669afb919c3364afab6bb8
    resource: repo://src/sempropos/intelligence/providers/_local_gguf.py
  - id: openwiki-source-b0d48c6108d71d833be6da9d
    resource: repo://src/sempropos/intelligence/providers/_ollama.py
  - id: openwiki-source-6c668f3544f6961d7a48ad3c
    resource: repo://src/sempropos/intelligence/providers/_openai_compatible.py
  - id: openwiki-source-dde87ad38c67b724ad33ca8c
    resource: repo://src/sempropos/intelligence/providers/_tier0.py
  - id: openwiki-source-dc62abaf7fc821e7fdccd526
    resource: repo://src/sempropos/intelligence/safety.py
  - id: openwiki-source-ff870d0a5a99aff8f66b5e2b
    resource: repo://src/sempropos/service.py
generated: { by: "antigravity", at: "2026-11-23T21:48:04.805Z" }
---

# Command Synthesis, Providers & Safety Verification

The intelligence layer under `src/sempropos/intelligence/` separates provider-neutral contracts from lazy-loaded backends, structured JSON prompting, and deterministic command grounding/safety verification.

## Contracts

`src/sempropos/intelligence/contracts.py` defines:

- `StructuredPrompt(system, context, query, json_schema=None)` — the system instruction, truncated `ToolSpec` candidate dicts, user task, and optional JSON schema (`COMMAND_JSON_SCHEMA`).
- `SynthesisResult(provider, command, raw_output, tool=None, explanation="", alternatives=(), metadata={})` — normalized provider output.
- `SynthesisOptions` — `think`, `temperature`, `max_tokens`, `keep_alive`, `stop`, `base_url`, `api_key`, `timeout_seconds`, `n_ctx`, `n_gpu_layers`, `threads`, and `structured_output`.
- Exception hierarchy: `IntelligenceError`, `ProviderUnavailableError`, `ProviderExecutionError`, and `ProviderConfigurationError`.
- `SynthesisProviderBackend` and `EmbeddingProviderBackend` protocols.

## Lazy Facade & Dispatch

`src/sempropos/intelligence/facade.py` imports provider modules (`_fastembed`, `_local_gguf`, `_ollama`, `_openai_compatible`, `_tier0`) strictly on demand inside the branches that use them so CLI startup and pure-lexical commands never pay ML runtime import overhead.

- **Synthesis selection:** `detect_synthesis_provider()` inspects `config.load_config().synthesis.provider` and returns `(provider_name, SynthesisConfig)` for `local_gguf`, `ollama`, `openai_compatible`, or `tier0` if available, raising `ProviderUnavailableError` otherwise (no silent cross-provider fallback).
- **Embedding selection:** `detect_embedding_provider()` supports `fastembed_local`, `ollama`, and `none` (lexical-only mode).
- **Dispatch:** `synthesize(prompt, backend=None, model=None)` honors an explicit `backend` override while preserving configured `SynthesisConfig` options, and dispatches to the target provider.
- **Warm-up & teardown:** `warm_synthesis_provider()` warms `ollama` or `local_gguf` models; `shutdown_providers()` unloads FastEmbed and terminates any in-process or `llama-server` GGUF runtime.

## Prompt Construction & JSON Output Parsing

`src/sempropos/intelligence/prompt.py` enforces structured output with `COMMAND_JSON_SCHEMA`:

```json
{
  "tool": "<name>",
  "command": "<one runnable command>",
  "explanation": "<short explanation>",
  "alternatives": [],
  "none": false
}
```

| Budget Constant | Value |
| --- | --- |
| `MAX_PROMPT_WORDS` | 680 |
| `MAX_TOOLS` | 5 |
| `MAX_SUBCOMMANDS_PER_TOOL` | 4 |
| `MAX_FLAGS_PER_TOOL` | 8 |
| `MAX_EXAMPLES_PER_TOOL` | 4 |

`build_prompt` truncates candidates in stages until total words fit `MAX_PROMPT_WORDS` (reducing examples `4 -> 1`, then flags `8 -> 4`, then subcommands `4 -> 2`, then tools `5 -> 1`).

`parse_synthesis_output(raw_output)` (and `extract_command`) first attempts to parse a JSON object (including inside fenced blocks), honoring `"none": true` as an explicit non-fit. When JSON is absent, it strips language-tagged markdown fences (```` ```bash ````), leading shell prompts (`$`, `#`, `>`), rejects prose markers and usage/synopsis echoes (`du [OPTION]...`), and rejects outputs over 500 characters. If synthesis fails to produce a command and did not explicitly return `none`, `SemService._repair_once` performs one repair retry.

## Providers

### Managed Local GGUF (`local_gguf`)

`src/sempropos/intelligence/models.py` provides a curated catalog of small CLI-tuned GGUF models downloaded atomically into `config.models_dir()`:

| Tier | Model ID | Approx. Size | Min RAM |
| --- | --- | --- | --- |
| `tiny` | `qwen2.5-coder-0.5b` | 398 MB | 2 GB |
| `balanced` | `qwen2.5-coder-1.5b` | 986 MB | 4 GB |
| `quality` | `qwen2.5-coder-3b` | 1930 MB | 8 GB |

`src/sempropos/intelligence/providers/_local_gguf.py` supports two automatic drivers:
1. **In-process `llama-cpp-python`** (when `sempropos[local-llm]` is installed), caching the `Llama` singleton and passing `response_format={"type": "json_object", "schema": COMMAND_JSON_SCHEMA}`.
2. **Managed `llama-server`** (on `$PATH` or `data_dir()/bin/llama-server`), spawned on an ephemeral loopback port (`127.0.0.1`), polled on `/health`, called via `/v1/chat/completions` with `json_schema`, and terminated cleanly on `unload_model()` / `atexit`.

### Pure-HTTP Ollama (`ollama`)

`src/sempropos/intelligence/providers/_ollama.py` uses Python stdlib `urllib.request` directly (requiring neither the `ollama` Python SDK nor runtime `pip install`). It resolves the host from `base_url`, `OLLAMA_HOST`, or `http://127.0.0.1:11434`, checks `/api/tags`, passes `format: COMMAND_JSON_SCHEMA` and `think` to `/api/chat` (retrying once without `think`/`format` if rejected), and calls `/api/embed` for embeddings.

### OpenAI-Compatible (`openai_compatible`)

`src/sempropos/intelligence/providers/_openai_compatible.py` resolves `base_url` and bearer API keys (options, `api_key_env`, keyring, plaintext, or `SEMPROPOS_OPENAI_API_KEY`), sends `response_format` `json_schema` to `/chat/completions` (retrying without `response_format` on HTTP 400/422), and maps HTTP vs. URL failures cleanly.

### FastEmbed (`fastembed_local`)

`src/sempropos/intelligence/providers/_fastembed.py` caches one `TextEmbedding` ONNX singleton in `config.embeddings_cache_dir()`, caps threads at `min(cpu_count, 4)`, uses `query_embed` / `passage_embed` when available, and exposes `is_model_loaded()` for confidence-gated semantic execution.

### Deterministic Tier-0 (`tier0`)

`src/sempropos/intelligence/providers/_tier0.py` is always available offline. Rather than returning an empty placeholder, it scores retrieved `ToolSpec` examples by token overlap against the query (with a `0.15` top-candidate bonus) and emits the matching verified command when overlap reaches `>= 0.55`, falling back cleanly to structured `ToolSpec` card display otherwise.

## Command Grounding & Safety Verification

Before `SemService.ask` returns a command, `verify_command(command, spec)` (`src/sempropos/intelligence/safety.py`):

1. Extracts the primary executable across pipelines/chains (`|`, `&&`, `;`), skipping leading `KEY=VAL` env assignments and wrapper commands (`sudo`, `doas`, `env`, `time`, `nice`, `nohup`) including wrapper value flags (`sudo -u root`).
2. Checks whether the executable exists in `$PATH`, shell builtins, or the matched `ToolSpec` name/aliases.
3. Expands bundled short flags (e.g. `-sh`, `-xzf`) and cross-checks flags against the matched `ToolSpec` to produce verified `FlagAnnotation` entries for `--explain` and record any `unverified_flags`.
4. Classifies operational risk as `safe`, `caution` (`sudo`, `rm`, `sed -i`, `chmod`/`chown`, `systemctl stop|restart|disable`, `docker|podman rm|prune`, `kill`), or `destructive` (`rm -rf`, `find -delete`, `dd of=/dev/...`, `mkfs*`/`wipefs`/`shred`, `chmod -R 777`, `git reset --hard`, `git push --force`, `git clean -f`, `kill -9`, `curl|wget ... | sh|bash`, block-device redirects).

## Related pages

- [Retrieval & Intent Ranking](retrieval.md)
- [Configuration & Metadata](configuration.md)
- [CLI Reference & Command Routing](cli.md)
- [System Architecture](architecture.md)
