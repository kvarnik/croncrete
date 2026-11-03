.PHONY: traces demo test lint report

traces:
	@echo "M1: record 10 seed emails"

demo:
	@echo "M6: keyless replay demo"

test:
	uv run pytest

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

report:
	@echo "M6: agent vs script report"
