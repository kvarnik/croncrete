.PHONY: traces demo test lint report

EMAIL_IDS ?= e01 e02 e03 e04 e05 e06 e07 e08 e09 e10
export UV_CACHE_DIR ?= /tmp/uv-cache

.venv/bin/python:
	uv venv
	uv pip install -e ".[dev]" -r examples/support_triage/requirements-server.txt

traces: .venv/bin/python
	.venv/bin/uvicorn app:app --app-dir examples/support_triage/mock_services --host 127.0.0.1 --port 8000 & echo $$! > /tmp/croncrete-mocks.pid; sleep 2; \
	fail=0; for id in $(EMAIL_IDS); do \
	  if [ -f examples/support_triage/traces/$$id.json ]; then echo "$$id: exists, skip"; \
	  else MOCK_BASE_URL=http://127.0.0.1:8000 .venv/bin/python examples/support_triage/agent.py $$id || fail=1; fi; \
	done; \
	kill $$(cat /tmp/croncrete-mocks.pid); exit $$fail

demo: .venv/bin/python
	@echo "M6: keyless replay demo"

test: .venv/bin/python
	.venv/bin/pytest

lint: .venv/bin/python
	.venv/bin/ruff check src tests examples
	.venv/bin/ruff format --check src tests examples

report: .venv/bin/python
	@echo "M6: agent vs script report"
