---
type: guide
title: Development and Quality
description: Local development setup, Makefile targets, ruff and mypy configuration, CI quality gates, test layout and offline policy, release publishing, and roadmap themes.
tags: [development, ci, linting, testing, roadmap]
verified:
  - by: openwiki/0.9.0
    at: 2026-11-23T21:48:04.805Z
sources:
  - id: openwiki-source-164e2da859b5277df81c7d94
    resource: repo://.github/workflows/ci.yml
  - id: openwiki-source-6d4b4e707b8d60b6ccfa3425
    resource: repo://.github/workflows/openwiki-update.yml
  - id: openwiki-source-f2608d0d515da097485b6ec5
    resource: repo://.github/workflows/publish.yml
  - id: openwiki-source-f317ee207e1653d2033c81a4
    resource: repo://CONTRIBUTING.md
  - id: openwiki-source-012f2c78e3b1446dfc35803f
    resource: repo://Makefile
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
  - id: openwiki-source-72fe826953aaa47d17a811e4
    resource: repo://ROADMAP.md
generated: { by: "opencode", at: "2026-11-21T06:12:23.249Z" }
---

# Development and Quality

## Prerequisites

- Python 3.11 or newer (`requires-python = ">=3.11"`); CI covers 3.11, 3.12, and 3.13.
- [`uv`](https://docs.astral.sh/uv/) for environment and dependency management.
- `man` on `PATH` and a SQLite build with FTS5 for a realistic local index; `sem install` checks FTS5.
- Optional: Ollama, an OpenAI-compatible endpoint, or a GGUF runtime (`llama-cpp-python` or `llama-server`) for synthesis. The default `tier0` mode works without any of them.

## Local setup

```bash
git clone https://github.com/ADPer0705/sempropos.git
cd sempropos
uv sync --group dev
uv run sem install      # bootstrap config, embeddings, and the v3 index
make check-all          # lint + strict mypy + full test suite
```

`uv sync --group dev` installs runtime dependencies plus the dev group: `build`, `mypy`, `pre-commit`, `pytest`, `pytest-cov`, `ruff`, and `twine`. `CONTRIBUTING.md` recommends building an index before testing retrieval locally.

## Make targets

| Target | Action |
| --- | --- |
| `make help` | Default target; lists available targets |
| `make install-dev` | `uv sync --group dev` |
| `make format` | `uv run ruff format src/ tests/` then `uv run ruff check --fix src/ tests/` |
| `make lint` | `uv run ruff check src/ tests/` and `uv run ruff format --check src/ tests/` |
| `make typecheck` | `uv run mypy src/` |
| `make test` | `uv run pytest -v` |
| `make eval` | `uv run pytest tests/eval/ -v` |
| `make check-all` | `lint`, `typecheck`, and `test` in sequence |
| `make build` | Clean, `uv run python -m build`, then `uv run twine check dist/*` |
| `make clean` | Remove build/test caches, `__pycache__`, `.pyc`/`.pyo`, `.egg-info`, and tool caches |

## Linting and typing

Ruff configuration in `pyproject.toml`:

- target `py311`, line length 100;
- enabled rule families: `E`, `F`, `W`, `I`, `N`, `UP`, `B`, `SIM`, `C90`;
- ignored: `E501` (formatter owns line length), `B008` (Typer's `x = typer.Option(...)` pattern), `B904` (intentional `typer.Exit` re-raises);
- McCabe complexity limit 15;
- formatting uses double quotes and space indentation.

mypy runs in strict mode for Python 3.11 with error codes shown and `ignore-without-code`, `redundant-expr`, and `truthy-bool` enabled. `make typecheck` runs it on `src/`.

## Test suite

Pytest is configured with `testpaths = ["tests"]` and default `addopts = "-m 'not live'"`. The `live` marker exists for tests that call real external daemons or network services; those tests are opt-in and excluded by default.

| Directory | Coverage |
| --- | --- |
| `tests/unit/` | Roff/mdoc parsing, retrieval analysis/fusion, safety classification, shell script validity, source discovery and merging, builder incrementality, `ToolSpec` hashing, repository round-trips, provider parsing and mocked HTTP, tier-0 fallback |
| `tests/integration/` | Tri-modal CLI dispatch and rendering, daemon RPC lifecycle, idle auto-shutdown, warm porcelain fast path |
| `tests/eval/` | 37-query golden retrieval benchmark asserting top-1 and top-3 tool sets |

`tests/conftest.py` provides an `isolated_env` fixture that points `SEMPROPOS_DATA_DIR`, `SEMPROPOS_CONFIG_DIR`, and `SEMPROPOS_RUNTIME_DIR` at a temporary tree. All default tests run offline with mocked providers and never require an API key.

## CI

`.github/workflows/ci.yml` runs on pushes to `main` and on pull requests.

### Quality job

A matrix over Python 3.11, 3.12, and 3.13:

1. checkout and install `uv`;
2. install the requested Python;
3. `uv sync --group dev --python <version>`;
4. `ruff check src/ tests/`;
5. `ruff format --check src/ tests/`;
6. `mypy src/`;
7. `pytest -v` for unit, integration, and the golden retrieval eval.

### Build job

On Python 3.12 it upgrades pip, installs `build` and `twine`, runs `python -m build`, and validates the artifacts with `python -m twine check dist/*`.

## Release publishing

`.github/workflows/publish.yml` triggers on a published GitHub release. It builds with `python -m build` on Python 3.11 and uploads through `pypa/gh-action-pypi-publish` using PyPI trusted publishing (OIDC) with the `pypi` environment configured in repository settings.

## Roadmap

`ROADMAP.md` records v1.0.0 as shipped:

- canonical versioned `ToolSpec` with content-hash incremental updates and pruning;
- direct man/mdoc parsing, offline tldr enrichment, static Fish/Zsh completion parsing, and safe `--help` probing;
- Porter-stemmed multi-column FTS5, structured tool-card embeddings, confidence-gated semantic execution, artifact/intent boosts;
- curated local GGUF synthesis, pure-HTTP Ollama and OpenAI-compatible providers, the warm daemon and porcelain fast path;
- the tri-modal CLI, command grounding and safety warnings, strict mypy, and a full pytest suite with a 37-query benchmark.

Planned for v1.1+:

- automated `llama-server` binary download during `sem model install`;
- an interactive TUI picker (`sem browse` / `--interactive`);
- macOS and BSD testing (Homebrew manpaths, BSD man/mdoc edge cases, launchd);
- custom user tool specs under `~/.config/sempropos/tools/*.toml`;
- opt-in shell-history-aware ranking.

## OpenWiki maintenance

`.github/workflows/openwiki-update.yml` runs manually or daily at 08:00 UTC. It checks out full history, installs Node 22 and `openwiki@0.9.0` with Mermaid validation dependencies, runs `openwiki code --update --print`, removes transient run state, and opens a pull request for `openwiki/`, `AGENTS.md`, and the workflow file. Source and hand-written docs should be updated first; generated wiki pages are refreshed by this process.

## Related pages

- [Architecture Overview](architecture.md)
- [Operations and Troubleshooting](operations.md)
