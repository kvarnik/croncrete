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

DEMO_NORMAL ?= e01
DEMO_DRIFT ?= e02
DEMO_WF = examples/support_triage/workflows/triage.py
DEMO_TRACES = examples/support_triage/traces
DEMO_URL = http://127.0.0.1:8000

demo: .venv/bin/python
	set -e; export CRONCRETE_REPLAY=1; \
	.venv/bin/uvicorn app:app --app-dir examples/support_triage/mock_services --host 127.0.0.1 --port 8000 & echo $$! > /tmp/croncrete-demo.pid; sleep 2; \
	trap "kill $$(cat /tmp/croncrete-demo.pid) 2>/dev/null || true" EXIT; \
	echo "== normal run =="; \
	.venv/bin/python examples/support_triage/run_email.py $(DEMO_NORMAL) --workflow $(DEMO_WF) --base-url $(DEMO_URL); \
	echo "== drift on =="; \
	curl -s -X POST $(DEMO_URL)/admin/drift; echo; \
	echo "== drifted run (halts) =="; \
	RUNOUT=$$(.venv/bin/python examples/support_triage/run_email.py $(DEMO_DRIFT) --workflow $(DEMO_WF) --base-url $(DEMO_URL) || true); echo "$$RUNOUT"; \
	RUNID=$$(echo "$$RUNOUT" | grep run_id | awk '{print $$2}'); \
	echo "== repair =="; \
	.venv/bin/croncrete repair --run-id $$RUNID; \
	.venv/bin/croncrete repair --run-id $$RUNID --approve; \
	echo "== resume =="; \
	.venv/bin/croncrete resume --run-id $$RUNID --base-url $(DEMO_URL); \
	echo "== report =="; \
	.venv/bin/croncrete report --traces $(DEMO_TRACES) --workflow $(DEMO_WF); \
	git checkout -- $(DEMO_WF); rm -f repairs/$$RUNID.patch

test: .venv/bin/python
	.venv/bin/pytest

lint: .venv/bin/python
	.venv/bin/ruff check src tests examples
	.venv/bin/ruff format --check src tests examples

report: .venv/bin/python
	.venv/bin/croncrete report --traces $(DEMO_TRACES) --workflow $(DEMO_WF)
