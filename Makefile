sPYTHON ?= python
PIP ?= $(PYTHON) -m pip

.PHONY : clean help install-dev test test-offline coverage format lint
.DEFAULT_GOAL := help

install-dev:
	@echo "Installing development dependencies..."
	$(PIP) install -e ".[dev]"

clean:
	@echo "Cleaning project..."
	rm -rf .pytest_cache build dist htmlcov .coverage .ruff_cache
	find . -name "__pycache__" -type d -exec rm -rf {} +
	find . -name "*.pyc" -delete
	find . -name "*.pyo" -delete
	find . -name ".DS_Store" -delete
	find . -name "*.egg-info" -type d -exec rm -rf {} +

test:
	@echo "Running full test suite with coverage gate..."
	$(PYTHON) -m pytest tests/ --cov=src/sempropos --cov-report=term-missing --cov-report=html --cov-fail-under=65

test-offline:
	@echo "Running tests in strict offline mode (default CI policy)..."
	SEMPROPOS_TEST_ALLOW_NETWORK=0 $(PYTHON) -m pytest tests/ --cov=src/sempropos --cov-report=term-missing --cov-report=html --cov-fail-under=65

coverage:
	@echo "Generating coverage report without fail-under gate..."
	$(PYTHON) -m pytest tests/ --cov=src/sempropos --cov-report=term-missing --cov-report=html

format:
	@echo "Formatting code with ruff..."
	ruff format src/ tests/
	ruff check --fix src/ tests/

lint:
	@echo "Running ruff checks..."
	ruff check src/ tests/

help:
	@echo "Available targets:"
	@echo "  install-dev - Install editable package with development dependencies"
	@echo "  test     - Run full pytest suite with coverage gate (>=65%)"
	@echo "  test-offline - Run tests with strict offline policy (no live network)"
	@echo "  coverage - Run pytest with coverage report (no fail-under)"
	@echo "  format   - Format code with ruff"
	@echo "  lint    - Run ruff lint checks"
	@echo "  clean   - Remove build artifacts and temporary files"
	@echo "  help    - Show this help message"