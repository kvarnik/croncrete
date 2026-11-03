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

## M2 (wiring)
- dataflow: select_group, scan_trace (trusted `to`), wire_group (exact-equality).
- Derived markers: priority=judge, subject/body=template; idempotency_key excluded.
- Two hand-written injection fixtures under tests/fixtures/.
- Provenance `<tool>.<field>` (unambiguous prev lookup) instead of `result.<field>`.
- Checks: real traces wire `to`->sender unflagged; ambiguity fails named.

## M3 (compiler)
- schema_infer: required=in-every-response, extra forbid, nested models, no enums.
- checks: import/call fence + map_* signatures, then ruff format+check.
- codegen: guards/outputs/maps, STEPS/SCOPES, judge(), template render, end checks.
- Generated module embeds judge prompt/template/SLA; no json import (tight fence).
- Template adherence: e01-e04+e07 exact; 5 others omit the Re: header line.
- Replay test generator covers first three clean traces (all exact).
- Live compile ok (1 retry on 60s timeout); triage.py + replay test committed.
- Checks: module passes fence+ruff, replay passes, eval/banned-import rejected.

## M4 (run and halt)
- executor: build/call/guard/map/output/checkpoint; guard halt carries shapes.
- checkpoints in sqlite (attempt ints); validate halts use failed_step NULL.
- scoped client; run_email.py loads inbox then runs; `run` CLI wired.
- Live completed run needs Spark Open keys; drift halt verifiable without them.

## Privacy hardening
- Mock server binds 127.0.0.1 only (Makefile); keys never leave this machine.
- .env gitignored+untracked; role configs hold env names, never values (tested).
- Cassettes record payloads only, never auth headers; demo replays keyless.
- Live Meta probe ok (OpenAI-compatible confirmed); Spark Open keys still missing.

## M4 live halt
- Drift on: run halts at step 0, expected guard schema vs observed plan shape.
- Live completed run still needs Spark Open keys (judge).

## M5 (repair + resume) — plumbing done, live model call pending Open keys
- repair: fenced exec validation vs drifted response + original output model.
- splice replaces exactly the fn+guard pair; patch via difflib; test generated+run.
- approve copies patched over workflow; resume reuses run_id + ticket key.
- Nested models live inside the guard (single-def splice contract).
- Full cycle tested keyless with canned repair answer; live repair needs Open.

## M6 (report + keyless demo) — code done, cassettes pending Open keys
- report: agent stats from traces, script stats from validation replay.
- deterministic exact-match on template-following runs (5); priority agreement.
- make demo recipe written (normal/drift/halt/repair/resume/report/restore).
- demo-normal=e01, demo-drift=e02; 10 judge + 1 repair cassettes to record live.
