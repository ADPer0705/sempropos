# Contributing to sem

Thank you for contributing to `sem` (`sempropos`)!

## Principles

- **Offline-first & privacy-first:** Running `sem` and running the test suite must never require an API key or cloud account.
- **Deterministic tests:** All unit, integration, and retrieval evaluation tests run offline with mocked providers. Opt-in live tests must use `@pytest.mark.live`.
- **Strict typing & formatting:** All code under `src/` must pass `mypy src/` (strict mode) and `ruff check` / `ruff format --check`.

## Development Setup

```bash
git clone https://github.com/ADPer0705/sempropos.git
cd sempropos
uv sync --group dev
make check-all
```

## Running Checks

```bash
make lint        # ruff lint + format check
make typecheck   # strict mypy src/
make test        # full pytest suite (unit + integration + retrieval benchmark)
make eval        # 37-query golden retrieval benchmark only
make build       # build wheel/sdist and check with twine
```

## Pull Requests

1. Check [ROADMAP.md](ROADMAP.md) or open an issue for larger architectural changes.
2. Add or update unit/integration tests covering your changes.
3. For retrieval changes, ensure `tests/eval/test_retrieval_benchmark.py` passes and add a regression case for any ranking bug you fix.
4. Run `make check-all` before submitting.
