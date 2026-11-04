# Croncrete

Agents prototype. Croncrete compiles the workflow into Python.

For teams that already have a working support agent and want the happy path
frozen into checked, deterministic code.

## The repair in 30 seconds

The CRM renames a field mid-demo. The script halts instead of guessing:

```
status: halted
failed_step: 0
"observed": {"customer_id": "c-002", "plan": {"plan_tier": "enterprise", ...}}
```

A model patches exactly one function and one guard. A human approves:

```diff
 class CrmLookupGuard(BaseModel):
+    class Plan(BaseModel):
+        plan_tier: str
+        renewal: str
     customer_id: str
-    tier: str
+    plan: Plan
@@
-        "tier": response["tier"],
+        "tier": response["plan"]["plan_tier"],
```

```
1 passed in 0.07s
applied to run b67006e3469e
== resume ==
completed b67006e3469e
```

Same run id, same ticket key, earlier steps never rerun.

## How it works

1. **Record.** Wrap agent tool calls; each run becomes a trace JSON.
2. **Compile.** The most common tool sequence becomes `triage.py`: wired
   arguments (exact value provenance, no model-written expressions), pydantic
   guards inferred from recorded responses, one model-written judge prompt
   for priority, template rendering, fixed end checks.
3. **Run.** The executor checkpoints every step to sqlite. A failed guard
   halts with the expected and observed shapes.
4. **Repair.** A model rewrites the failed step's mapping function and guard
   so the output shape stays the same. The splice touches exactly those two
   AST nodes, and only after approval. Resume continues the same run.
5. **Report.** Deterministic replay compares agent vs script on every trace.

## Install

```sh
git clone https://github.com/kvarnik/croncrete.git
cd croncrete
uv venv && uv pip install -e ".[dev]" -r examples/support_triage/requirements-server.txt
```

## Record

```python
import croncrete as cc
rec = cc.Recorder("traces/")
with rec.run(input=email) as run:
    customer = run.tool("crm_lookup", crm_lookup, email=email["sender"])
    run.output(result)
```

## Demo

```sh
make demo
```

No keys needed: the judge and repair calls replay from committed cassettes.
A normal run completes, drift halts the next run, the repair diff prints,
approval applies it, resume completes, and the report prints. Run it twice;
the second run halts on drift again.

## Models

Two roles, configured in `.env` (see `.env.example`):

- The **compiler model** runs the example agent and writes the judge prompt.
- The **runtime model** runs the priority judge and writes repairs.

Comparison against recorded runs is deterministic code: **validated**, never
proven. Priority is an agreement rate. Repair replaces exactly one function
and one guard, by an AST splice, and only after approval.

## Security

- Scoped tools: the client only calls the workflow's endpoints, else `ScopeError`.
- Fenced judge: fixed prompt, enum-only output, no control flow from the model.
- Static checks pass before any generated code is imported: import fence,
  no eval/exec/open, step signatures, then ruff.
- Injection fixtures: a send to an untrusted recipient is flagged and
  excluded from compilation.
- Human-approved splice, idempotent writes (`run_id:step` keys).

## Report

10 recorded runs, 5 template-following:

| | agent | script |
|---|---|---|
| runs | 10 | 10 |
| success | 100.00% | 100.00% |
| avg cost | $0.01 | $0.00 |
| model calls/run | 4.00 | 1.00 |
| deterministic fields | — | 5/5 |
| priority agreement | — | 10/10 (100.00%) |

**Built during the hackathon.**

## Prior art

AgentJIT, rote-cli, Skyvern, Stagehand, and UiPath explore adjacent ground:
recording runs, driving UIs, and productionizing agents. Croncrete differs by
compiling recorded tool calls into checked Python with inferred guards, and
patching one step when a response shape drifts.

## Limitations

- Priority agreement is 10/10 on the seed fixtures; harder mail may score lower.
- One domain (support triage), mock services, no real side effects.
- v0 compiles a single tool sequence plus one judge plus a template.
