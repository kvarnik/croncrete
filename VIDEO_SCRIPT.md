# Video script (3 minutes, no live commands)

Format: narrate over prepared screens — open files and saved outputs,
scroll and highlight. Nothing is typed on camera. Every artifact below
is committed in the repo: traces, the workflow, and the `demo/`
transcript + report.

## 0:00 The pitch (20s)

Show: the logo + one line: "Agents prototype. Croncrete compiles the
workflow into Python."

Say: "If you have a support agent that works, Croncrete freezes the
happy path into checked code. Built during the hackathon." Who it's
for: "teams with a working agent that want the repeating part
deterministic."

## 0:20 The problem (25s)

Show: one recorded trace (`examples/support_triage/traces/e01.json`) —
steps on the left, totals on the right: 4 model calls, ~$0.01.

Say: "Every email costs four model round-trips. Worse, when an API
changes shape, the agent doesn't stop — it guesses, and files a ticket
with garbage. You pay per email and you can't audit what happened."

## 0:45 The compiled artifact (30s)

Show: `examples/support_triage/workflows/triage.py`, two stops while
scrolling. First `JUDGE_PROMPT` — "the only thing a model wrote."
Then one STEPS entry — `"email": "input.sender"` — "everything else
is derived: wired arguments, inferred guards, no model-written logic."

Say: "The agent loop becomes one judgment call. The rest is Python you
can read, test, and checkpoint."

## 1:15 The drift story (65s)

Open `demo/transcript.md` and scroll through three screens; linger on each.

1. The halt: `status: halted, failed_step: 0`, expected `tier` vs
   observed `plan`. Say: "We broke the CRM on purpose. The script
   stops and shows both shapes instead of guessing."
2. The diff: the two-hunk patch — nested `Plan` guard, `tier` read
   from `plan.plan_tier` — plus `1 passed`. Say: "A model rewrites
   exactly one function and one guard. A generated test proves the
   output shape is unchanged, and a human approves."
3. The resume: `completed` with the same run id. Say: "Same run,
   same ticket key, earlier steps never reran."

## 2:20 The numbers (25s)

Show: `demo/report.md` (10 runs).

- "Four model calls per email become one."
- "Deterministic fields match 5 for 5 where the agent followed the
  template. Priority agreement 10 for 10."
- "Comparison is deterministic code — validated, never proven."

## 2:45 The close (15s)

Show: `make demo` in the README and the repo URL.

Say: "The whole story replays with no keys and no real email — mocks
only. Repo link below."
