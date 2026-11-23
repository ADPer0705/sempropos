.PHONY: clean install-dev format lint typecheck test eval check-all build help
.DEFAULT_GOAL := help

clean:
	@echo "Cleaning project..."
	rm -rf .pytest_cache build dist htmlcov .coverage .ruff_cache .mypy_cache
	find . -name "__pycache__" -type d -exec rm -rf {} +
	find . -name "*.pyc" -delete
	find . -name "*.pyo" -delete
	find . -name ".DS_Store" -delete
	find . -name "*.egg-info" -type d -exec rm -rf {} +

install-dev:
	uv sync --group dev

format:
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

typecheck:
	uv run mypy src/

test:
	uv run pytest -v

eval:
	uv run pytest tests/eval/ -v

check-all: lint typecheck test

build: clean
	uv run python -m build
	uv run twine check dist/*

help:
	@echo "Available targets:"
	@echo "  install-dev - Install package with dev dependencies"
	@echo "  format      - Format and autofix with ruff"
	@echo "  lint        - Run ruff lint and format checks"
	@echo "  typecheck   - Run strict mypy checks on src/"
	@echo "  test        - Run full pytest suite (unit, integration, retrieval eval)"
	@echo "  eval        - Run golden retrieval benchmark suite"
	@echo "  check-all   - Run lint, typecheck, and test"
	@echo "  build       - Build wheel/sdist and validate with twine"
	@echo "  clean       - Remove build and cache artifacts"
