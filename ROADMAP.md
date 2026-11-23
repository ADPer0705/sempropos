# Roadmap

## Shipped in v1.0.0

### Structured Ingestion & Discovery
- [x] Canonical versioned `ToolSpec` IR (`tools`, `aliases`, `subcommands`, `flags`, `positionals`, `examples`, `see_also`, `tool_sources`) with content-hash incremental updates and uninstalled tool pruning.
- [x] Direct `man(7)` / `mdoc(7)` macro parser capturing GNU coreutils flags inside `DESCRIPTION` and filtering English prose out of `EXAMPLES`.
- [x] Multi-source enrichment merging offline `tldr-pages` (root + hyphenated subcommands), static Fish/Zsh completions, and safe `--help` probing for non-man `$PATH` binaries (`7z`, `uv`, `ruff`, etc.).
- [x] Observable discovery & parse coverage metrics in `sem doctor` (flag/example coverage %, source breakdown, unindexed `$PATH` binaries).

### Retrieval & Ranking
- [x] Porter-stemmed multi-column FTS5 (`tokenize='porter unicode61'`) indexing `name`, `aliases`, `summary`, `tags`, `subcommands`, `examples`, and `flags`.
- [x] Rich structured tool-card embeddings synced incrementally by `tool_id`, with confidence-gated lazy semantic execution.
- [x] Query analyzer with stopword filtering, file-extension/artifact boosts (`.7z`, `.tar.gz`, `.json`, `.service`), compound intent signals (`du`, `ss`, `find`, `git`, `systemctl`), and directional weighted expansions.
- [x] Removed unused `reciprocal_rank_fusion`.

### Local Intelligence, Latency & CLI Polish
- [x] Curated local GGUF synthesis provider (`local_gguf`) with JSON-schema constrained output via `llama-cpp-python` or `llama-server`.
- [x] Pure-HTTP Ollama and OpenAI-compatible providers (dropped `pip` and `ollama` SDK runtime dependencies).
- [x] Auto-managed Unix-socket warm daemon with idle auto-shutdown and thin-client `--porcelain` fast path.
- [x] Tri-modal CLI (`ask`, `show`, `search`, `doctor`, `model`, `daemon`, `config`, `init`), command grounding & destructive-command safety warnings, and multi-word bare-query disambiguation (`sem check open ports`).
- [x] Full `pytest` unit/integration suite, 37-query golden retrieval eval benchmark, strict `mypy`, and CI enforcement.

## Planned (v1.1+)

- [ ] **Automated `llama-server` binary downloader** in `sem model install` for platforms without prebuilt `llama-cpp-python` wheels.
- [ ] **Interactive TUI picker** (`sem browse` / `--interactive`) to fuzzy-filter tools, flags, and examples live.
- [ ] **macOS & BSD extended testing** (Homebrew manpaths, BSD `man`/`mdoc` edge cases, `launchd` daemon niceties).
- [ ] **Custom user tool specs** (`~/.config/sempropos/tools/*.toml`) so teams can document internal scripts and project Makefiles in the same index.
- [ ] **Shell history awareness (opt-in)** to personalize ranking toward tools the user frequently runs.
