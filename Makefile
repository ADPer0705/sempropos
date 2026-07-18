.PHONY : clean install-dev format lint help
.DEFAULT_GOAL := help

clean:
	@echo "Cleaning project..."
	rm -rf .pytest_cache build dist htmlcov .coverage .ruff_cache
	find . -name "__pycache__" -type d -exec rm -rf {} +
	find . -name "*.pyc" -delete
	find . -name "*.pyo" -delete
	find . -name ".DS_Store" -delete
	find . -name "*.egg-info" -type d -exec rm -rf {} +
	find . -name "htmlcov" -type d -exec rm -rf {} +
	find . -name ".ruff_cache" -type d -exec rm -rf {} +
	find . -name ".mypy_cache" -type d -exec rm -rf {} +



install-dev:
	@echo "Installing development dependencies..."
	uv sync

format:
	@echo "Formatting code with ruff..."
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

lint:
	@echo "Running ruff checks..."
	uv run ruff check src/ tests/

help:
	@echo "Available targets:"
	@echo "  clean   - Remove build artifacts and temporary files"
	@echo "  install-dev - Install editable package with development dependencies"
	@echo "  format   - Format code with ruff"
	@echo "  lint    - Run ruff lint checks"
	@echo "  help    - Show this help message"
