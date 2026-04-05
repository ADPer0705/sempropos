.PHONY : clean

clean:
	@echo "Cleaning project..."
	rm -rf .pytest_cache build dist htmlcov .coverage
	find . -name "__pycache__" -type d -exec rm -rf {} +
	find . -name "*.pyc" -delete