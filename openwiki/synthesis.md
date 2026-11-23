---
type: architecture
title: Prompting and Synthesis Providers
description: Provider contracts and dispatch, Ollama and OpenAI-compatible backends, FastEmbed embeddings, tier0 fallback, prompt budgeting, and command extraction and repair.
tags: [synthesis, prompts, ollama, openai, embeddings, providers]
verified:
  - by: openwiki/0.9.0
    at: 2026-11-21T06:12:23.249Z
sources:
  - id: openwiki-source-401c3a8b8dfa9b92b6d17737
    resource: repo://src/sempropos/cli.py
  - id: openwiki-source-e6e2fa64cad475f151db6633
    resource: repo://src/sempropos/intelligence/contracts.py
  - id: openwiki-source-96f0681bdb464aa6414af509
    resource: repo://src/sempropos/intelligence/facade.py
  - id: openwiki-source-c8919dac9c46b272e6ad65f8
    resource: repo://src/sempropos/intelligence/prompt.py
  - id: openwiki-source-0069c226d8bdc9dcf3d0ab51
    resource: repo://src/sempropos/intelligence/providers/_fastembed.py
  - id: openwiki-source-b0d48c6108d71d833be6da9d
    resource: repo://src/sempropos/intelligence/providers/_ollama.py
  - id: openwiki-source-6c668f3544f6961d7a48ad3c
    resource: repo://src/sempropos/intelligence/providers/_openai_compatible.py
  - id: openwiki-source-dde87ad38c67b724ad33ca8c
    resource: repo://src/sempropos/intelligence/providers/_tier0.py
generated: { by: "opencode", at: "2026-11-21T06:12:23.249Z" }
---

# Prompting and Synthesis Providers

The intelligence layer under `src/sempropos/intelligence/` separates provider-neutral contracts from concrete backends. The CLI only talks to the facade and the prompt helpers.

## Contracts

`src/sempropos/intelligence/contracts.py` defines:

- `StructuredPrompt(system, context, query)` — the system instruction, the truncated list of candidate dicts, and the user query.
- `SynthesisResult(provider, command, raw_output, metadata)` — normalized provider output.
- `SynthesisOptions` — `think`, `temperature`, `max_tokens`, `keep_alive`, `stop`, `base_url`, `api_key`, `timeout_seconds`.
- The exception hierarchy: `IntelligenceError`, `ProviderUnavailableError`, `ProviderExecutionError`, `ProviderConfigurationError`.
- `SynthesisProviderBackend` and `EmbeddingProviderBackend` protocols with `is_available`, `get_available_models`, and the role-specific execution method.

## Facade dispatch

`src/sempropos/intelligence/facade.py` owns selection and dispatch.

### Availability

`list_synthesis_providers()` and `list_embedding_providers()` return a name-to-boolean map in the order declared by `SUPPORTED_SYNTHESIS_PROVIDERS` and `SUPPORTED_EMBEDDING_PROVIDERS`.

### Synthesis selection

`detect_synthesis_provider()` reads the configured synthesis `RoleConfig` and returns it only if that specific provider is available:

- `ollama` if the daemon is reachable;
- `openai_compatible` if a base URL is configured;
- `tier0`, which is always available.

Otherwise it raises `ProviderUnavailableError`. There is no automatic fallback chain between configured providers: the configured provider is authoritative, and an unavailable one produces the CLI's retrieval fallback. The default configuration written by `sem install` prefers Ollama when it is reachable.

### Embedding selection

`detect_embedding_provider()` similarly accepts `fastembed_local` when the library is importable or `ollama` when the daemon is reachable, and raises otherwise.

### Dispatch

`synthesize(prompt, backend=None, model=None)`:

1. uses `backend` when given, otherwise the detected provider;
2. keeps the configured role config for options even when the backend is overridden;
3. rejects providers outside the supported synthesis list;
4. picks the explicit model, then the configured model;
5. builds `SynthesisOptions` from the role config;
6. dispatches to `ollama`, `openai_compatible`, or `tier0`.

`embed_texts(texts, backend=None, model=None, kind="document")` dispatches to FastEmbed or Ollama and passes `kind` only to FastEmbed, where it selects the query/document embedding method.

`_options_from_role` maps role fields to options and `_resolve_api_key` resolves a key from environment, keyring, then plaintext.

### Warm-up

`warm_synthesis_provider()` detects the provider and, for Ollama with a configured model, calls `warm_model`. Failures are reported as `"warm-up failed: ..."` rather than raised. Non-Ollama providers return `"no warm-up required"`, and an unavailable provider returns `None`.

## Prompt construction

`src/sempropos/intelligence/prompt.py` defines the synthesis contract sent to every backend.

### System instruction

`SYSTEM_INSTRUCTION` tells the model to:

- choose one tool from the supplied context;
- reply with one concrete shell command on one line;
- prefer the flags shown but allow other well-known flags of the same tool;
- never invent tools outside the context;
- avoid usage/synopsis echoes and markdown fences;
- reply exactly `NONE` when no listed tool can accomplish the task.

### Budgeting

Constants:

| Constant | Value |
| --- | --- |
| `MAX_PROMPT_WORDS` | 600 |
| `MAX_TOOLS` | 5 |
| `MAX_FLAGS_PER_TOOL` | 6 |
| `MAX_EXAMPLES_PER_TOOL` | 4 |

`build_prompt` appends `"\n\nCommand:"` to the query, then loops:

1. deep-copy and truncate candidates to the current limits;
2. format them with `context_to_str`;
3. count words in the system instruction, query, and formatted context;
4. return when the total is within budget;
5. otherwise drop one example at a time, then reduce flags toward three, then drop tools and reset the per-tool limits.

The loop returns its best effort at one tool, three flags, and zero examples.

### Context formatting

`context_to_str` renders each candidate as:

```text
--- tool ---
Synopsis: ...
Relevant flags:
  -f, --flag <hint> - description
Examples:
  command
---------
```

Flags render their short and long forms, a `<hint>` when they take a value, and the description.

## Command extraction

`extract_command(raw_output)` normalizes a provider reply:

- returns `None` for empty input;
- extracts the first non-language-tagged segment from fenced code blocks;
- takes the first non-blank line and strips leading `$`, `#`, or `>`;
- returns `None` for the `NONE` sentinel (ignoring trailing `.` or `!`);
- rejects prose markers such as "I'm sorry", "here is", "you can use", or "explanation";
- rejects usage echoes matching `[OPTION]`-style placeholders or containing `OPTION`/`OPTIONS`/`FLAGS`;
- rejects output still containing a fence or longer than 500 characters.

When extraction fails, the CLI's `_repair_command` sends one more prompt with the same system and context plus a query asking for exactly one concrete command or `NONE`. An explicit `NONE` reply is never repaired.

## Providers

### Ollama

`src/sempropos/intelligence/providers/_ollama.py` uses the official SDK, installing it on demand through `install_and_import` if missing.

- Availability is an HTTP GET to `http://127.0.0.1:11434/api/tags` with a 1.5-second timeout.
- `get_available_models` calls `ollama.list()` and supports both modern pydantic-style and older dict-style responses.
- `synthesize` builds chat messages (system + user with formatted context and task), sets `temperature`, `num_predict`, optional `stop`, `keep_alive`, and `think`.
- If a model rejects the `think` parameter, the call is retried once without it.
- `_extract_content` reads `response.message.content` or the equivalent dict field.
- `warm_model` sends a one-token chat request and suppresses failures.
- `embed_texts` calls `ollama.embed` and converts the result to a float32 array.

Defaults from `RoleConfig` keep latency low: `think=False`, `temperature=0.0`, `max_tokens=256`, `keep_alive="30m"`.

### OpenAI-compatible

`src/sempropos/intelligence/providers/_openai_compatible.py` speaks the `/chat/completions` contract and works with OpenAI, vLLM, LM Studio, llama.cpp server, Ollama's shim, or a gateway.

- Base URL resolution: role config `base_url`, then `SEMPROPOS_OPENAI_BASE_URL`, then `https://api.openai.com/v1` when a key exists.
- Key resolution: options key, `api_key_env`, keyring secret, `api_key_plaintext`, then `SEMPROPOS_OPENAI_API_KEY`.
- `is_available` is true whenever a base URL resolves.
- `get_available_models` performs a best-effort GET to `/models` and returns `[]` on any failure.
- `synthesize` POSTs `model`, `messages`, `temperature`, `max_tokens`, `stream: false`, and optional `stop`, with a bearer token when available.
- HTTP errors include the status and a truncated body; URL errors are reported as unavailable.

### FastEmbed

`src/sempropos/intelligence/providers/_fastembed.py` provides local embeddings.

- Availability is whether the `fastembed` package imported.
- The ONNX model is a process-wide singleton, reloaded only when the model name changes.
- The cache directory is `config.embeddings_cache_dir()`, keeping the model available offline after the first download.
- Thread count is capped at `min(cpu_count, 4)` to bound activation memory.
- `kind="query"` uses `query_embed`; `kind="document"` uses `passage_embed`; models without those methods fall back to `embed`.
- Output is written into a pre-allocated float32 matrix instead of being accumulated in Python lists.
- `unload_model()` releases the engine after indexing.

### tier0

`src/sempropos/intelligence/providers/_tier0.py` is always available and returns an empty `SynthesisResult(provider="tier0")`. It is a placeholder that satisfies the interface; the CLI never prints its output. A resolved tier0 is treated as "no synthesis backend" and triggers the retrieved-man-page display instead.

## Related pages

- [Retrieval and Ranking](retrieval.md)
- [Configuration and Paths](configuration.md)
- [CLI Surface and Query Dispatch](cli.md)
- [Architecture Overview](architecture.md)
