# Notes

## M0 (skeleton)
- Froze contracts in INTERFACES.md; fixed resume/report sigs, paths, checks allowlist.
- llm.py: OpenAI-compatible client, per-role config, record/replay, pricing, strip.
- Pinned core + server deps exact; example owns fastapi/uvicorn via requirements file.
- Live Meta + Open "reply with ok" calls pending keys/format confirmation.
- Checks: pytest + ruff clean, no-FastAPI import, five CLI commands.

## M1 (mocks, agent, traces) — done
- Recorder + TraceRecord schema; trace per email id, totals from pricing table.
- Mock services: inbox/CRM/tickets/outbox/drift; 10 hand-written seed emails.
- Agent loop capped at 8 steps; `make traces` resumable by email id.
- Fixes: unique tool_call ids (400s), must-call-all-three prompt + early-stop nudge.
- Checks: 10/10 three-tool signature, $0.13 total, 19 passed, ruff clean.

## Privacy hardening
- Mock server binds 127.0.0.1 only (Makefile); keys never leave this machine.
- .env gitignored+untracked; role configs hold env names, never values (tested).
- Cassettes record payloads only, never auth headers; demo replays keyless.
- Live Meta probe ok (OpenAI-compatible confirmed); Spark Open keys still missing.
