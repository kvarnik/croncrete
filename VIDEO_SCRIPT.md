# Video script (3 minutes, live models)

Setup: `.env` with keys, fresh shell, repo at this commit, port 8000 free.
Narrate replay/live honestly: the repair call and judges below are LIVE.

## 0:00 The pitch (20s)

"Agents prototype. Croncrete compiles the workflow into Python. If you have
a support agent that works, we freeze the happy path into checked code.
Built during the hackathon."

## 0:20 One agent run and its cost (25s)

Show a trace: `cat examples/support_triage/traces/e01.json | head -30`.
Point at steps (crm, ticket, outbox) and totals (~$0.01, 4 model calls).

## 0:45 Open triage.py (30s)

Open `examples/support_triage/workflows/triage.py`.
Point at `JUDGE_PROMPT` (model-written), then at a wired step
(`"email": "input.sender"` in STEPS — derived, no model).

## 1:15 Drift, halt, repair, approve, resume (65s)

Terminal, live:

1. Start mocks; run e01 (completes).
2. `curl -X POST localhost:8000/admin/drift`; run e02 → `status: halted`,
   `failed_step: 0`, expected `tier` vs observed `plan`.
3. `croncrete repair --run-id …` → read the diff (nested Plan guard,
   `tier` from `plan.plan_tier`), test passes.
4. `croncrete repair --run-id … --approve` → applied.
5. `croncrete resume --run-id …` → `completed` (same run id).

Say: one function and one guard, human-approved, earlier steps never reran.

## 2:20 The report (25s)

`croncrete report --traces … --workflow …`.
Read: 4 calls → 1 call, deterministic 5/5, priority agreement 10/10.
Say "validated, never proven; priority is an agreement rate."

## 2:45 Keyless demo (15s)

`make demo` with no keys in env. Say: cassettes replay the model calls,
mocks only, no real email. End card: repo URL.
