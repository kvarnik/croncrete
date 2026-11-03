# Croncrete interfaces (frozen in M0)

Canonical paths (repo-relative). Library takes explicit paths; CLI resolves
relative to cwd. No cwd magic in `croncrete/`.

- Traces: `examples/support_triage/traces/`
- Workflow: `examples/support_triage/workflows/triage.py` (committed)
- Cassettes: `examples/support_triage/cassettes/` (committed)
- Repairs: `repairs/<run_id>.patch` (`repairs/*.patch` ignored, `.gitkeep` kept)
- State DB: `.croncrete/state.db` (ignored)
- Patched: `.croncrete/patched/triage_<run_id>.py` (ignored)

## Library

```python
import croncrete as cc

rec = cc.Recorder("examples/support_triage/traces/")
with rec.run(input=email) as run:
    customer = run.tool("crm_lookup", crm_lookup, email=email["sender"])
    run.output(result)

wf: Path = cc.compile(
    "examples/support_triage/traces/", tools=TOOL_SCHEMAS,
    template="examples/support_triage/reply_template.txt",
    sla="examples/support_triage/sla.json",
    trusted_fields=["input.sender"],
    out="examples/support_triage/workflows/triage.py",
)
result: RunResult = cc.run(wf, input=email, tools=client)
proposal: RepairProposal = cc.repair(result.run_id)          # no apply
proposal.approve()                                            # overwrites wf
resumed: RunResult = cc.resume(result.run_id, tools=client)  # same run_id
report: Report = cc.report("examples/support_triage/traces/", wf)
```

`RunResult`: `{run_id, status: completed|halted, output|None,
failed_step: int|None, error: {expected, observed}|None, workflow: Path}`.
`halted` carries the guard diff; only guard halts are repaired.
`validate` halts (end checks) are terminal.

`RepairProposal`: `{run_id, step_index, diff: str, patched_module: Path,
test_results: str, approve()}`. `approve()` copies patched module over `wf`.

## Tools, wiring, guards

`TOOL_SCHEMAS: dict[str, {endpoint, args}]`. Example:
`{"crm_lookup": {"endpoint": "GET /crm/customers", "args": {"email": "string"}}}`.
Client: `call(endpoint: str, **kwargs) -> dict`. `SCOPES: list[str]` per
workflow; anything else raises `ScopeError`.

Wiring: exact value equality to `input.<f>` or earlier `result.<f>`. Ignore
`None`/`""`/bools. Multi-match uses the path stable across every trace in the
group, else compile fails naming the argument. `POST /tickets` gets
`idempotency_key = f"{run_id}:{step_index}"` (excluded from matching/compare).
Reply `subject`/`body` come from template + `sla.json`, never provenance.

`trusted_fields` (default `["input.sender"]`): allowed provenance roots for
`to` in send steps. A send whose `to` is neither in `trusted_fields` nor an
earlier tool result is flagged and excluded from compile.

Step order: build args → `tools.call` → guard(raw) → `map(response,input,prev)`
→ output-model(mapped) → checkpoint. `prev: dict[str, dict]` maps earlier tool
names to their mapped outputs.

Guards: each observed field → `str|int|float|bool|nested`. Required = present
in every response. Extra forbidden. No enums/ranges. Output model = fields read
by later steps, the judge, or the template.

`Priority`: `P1|P2|P3` (library enum, generated module imports it). End checks
(fixed code): priority in enum; recipient == `input.sender`. Failure halts at
`validate`, not repaired.

Generated module exports: `STEPS` (list of
`{index, tool, endpoint, args: {arg: provenance}, guard, map_fn, output_model}`),
`SCOPES`, `judge()`, `render_reply()`, mapping fns `map_<tool>`,
guards `<Tool>Guard`, outputs `<Tool>Out`. Stable names for AST splice.

`checks.py` allowlist: imports from `pydantic`, `typing`, `croncrete` only
(includes `croncrete.llm`, `Priority`); bans `eval/exec/compile/__import__/
subprocess/os/sys/open/input/breakpoint/pickle/marshal/socket`. Step fns
`(response, input, prev)`, pure, no I/O. Then ruff. Fail rejects the module.

## Traces, DB, LLM

Trace JSON per run: `run_id, input, steps[{index, tool, args, result,
latency_ms}], llm_calls[{role, tokens_in, tokens_out, latency_ms}], output|None,
totals{steps, latency_ms, tokens_in, tokens_out, cost_usd}, flagged: bool,
flag_reason: str|None`. No wall-clock timestamps. Over-cap/failed runs have
`output None` and are excluded. `llm_calls` appended via context var during
`rec.run()`. Cost from pricing table at close.

SQLite `.croncrete/state.db`, attempt ints, no timestamps:
`runs(run_id PK, workflow, input_json, status, output_json, failed_step,
error_json)`; `step_runs(run_id, step_index, attempt, tool, args_json,
result_json, status, latency_ms)`; `calls(run_id, step_index, attempt,
endpoint, args_json, response_json, latency_ms)`.

`llm.chat(role, messages, tools=None, response_schema=None) -> LLMResult`
(`text, tool_calls[{name, arguments}], tokens_in, tokens_out, cached_in,
latency_ms, cost_usd`). Roles: `agent|compiler|repair|judge`. OpenAI-compatible
`httpx` client, per-role `{model, base_url, api_key_env}`. Timeout 60s, retry
once on 429/5xx. Schema miss retries once with the error attached, then fails
naming the role. Reasoning channel stripped; JSON payload only. `strip_for_prompt(obj)`
drops `run_id/latency_ms/cost_usd/tokens_in/tokens_out` and clock times.
Cassettes: `CRONCRETE_REPLAY=1` serves, miss raises (never network);
`CRONCRETE_RECORD=1` saves. Key = SHA-256 canonical JSON of
`{role, messages, tools, response_schema}`.

Pricing: Spark 1.2 Standard $1.25 in / $0.15 cached-in / $4.25 out per M
(cached only when reported); Spark Open per-provider in config.

## CLI (thin), example, report

CLI: `compile --traces --tools-schemas(JSON) --template --sla --out`;
`run --workflow --input(JSON) --base-url`; `repair --run-id [--approve]`;
`resume --run-id --base-url`; `report --traces --workflow`. Email loading is
`examples/support_triage/run_email.py` (fetches `GET /inbox/{id}` over HTTP),
never core. `croncrete run` builds its own HTTP client from `--base-url`.

Mocks (FastAPI, example-owned; core never imports it): `GET /inbox/{id} →
{id, sender, subject, body}`; `GET /crm/customers?email= →
{customer_id, name, tier, open_tickets}`; `POST /tickets
{customer_id, priority, summary, idempotency_key} → {ticket_id}` (same key,
same ticket); `POST /outbox {to, subject, body} → {message_id}`;
`POST /admin/drift` toggles drift (`tier` → `plan.{plan_tier, renewal}`).
Agent tools: `crm_lookup|tickets_create|outbox_send`. Cap 8 steps.
`seed.json`: `{customers[{email, customer_id, name, tier, open_tickets}],
emails[{id, sender, subject, body}]}` (10 hand-written). `sla.json`:
`{tier: sla}` e.g. `{"enterprise": "4 hours", "pro": "1 business day",
"free": "3 business days"}`. Template `Re: {subject} / Hi {name} ... {ticket_id}
{priority} {sla}` shared by agent prompt and codegen.

Judge in: `{email{sender, subject, body}, customer{tier, open_tickets},
summary}`; out: `Priority` via `response_schema`. Compiler in: stripped
examples of that mapping from clean traces; out: `{judge_prompt: str}`.
Repair in: `{tool, function_source, guard_source, output_model,
failed_response, error}`; out: `{function_source, guard_model_source}` (new
guard accepts drifted shape; new fn output validates against original model).

Makefile: `traces` (live record 10, resumable by id); `demo` (replay keyless:
mocks → normal → drift → halt → repair → approve → resume → report → restore
`workflows/triage.py`, delete own patch, keep `.croncrete/`); `test` (pytest);
`lint` (ruff check + format check); `report` (replay report). Server deps in
`examples/support_triage/requirements-server.txt` (pinned fastapi+uvicorn);
demo installs `pip install -e .` plus that file. No `croncrete[server]`.

Report: agent stats from traces; script stats from replay of every clean trace
(recorded responses by step index; judge live unless replaying, one cassette
per clean trace). Deterministic exact-match on template-following runs:
`customer_id, name, tier, recipient, rendered reply` (priority+SLA spans
stripped via template split). Priority = agreement rate + counts. Latency =
sum recorded step latencies + judge latency. Sorted by trace filename, fixed
2-decimal format, no timestamps. Prints and writes `report.md`.
