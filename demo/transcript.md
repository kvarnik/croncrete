# Demo transcript (`make demo`, keyless replay)

## Normal run (completes)

```
status: completed
run_id: 77c087d1bb40
{
  "body": "Re: URGENT: production API down, all requests failing\n\nHi Alice, we've logged your request as ticket T-1001 with priority P1. Our team will reply within 4 hours.",
  "priority": "P1",
  "subject": "Re: URGENT: production API down, all requests failing",
  "ticket_id": "T-1001",
  "to": "alice@acme.example"
}
```

## Drift on

```
{"drift":true}
```

## Drifted run (halts)

```
status: halted
run_id: 05d2c9b206ae
failed_step: 0
{
  "expected": {
    "properties": {
      "customer_id": {"type": "string"},
      "name": {"type": "string"},
      "open_tickets": {"type": "integer"},
      "tier": {"type": "string"}
    },
    "required": ["customer_id", "name", "open_tickets", "tier"],
    "title": "CrmLookupGuard"
  },
  "observed": {
    "customer_id": "c-002",
    "name": "Bob",
    "open_tickets": 1,
    "plan": {"plan_tier": "enterprise", "renewal": "2027-01-01"}
  },
  "stage": "guard",
  "tool": "crm_lookup"
}
```

## Repair (model-written, human-approved)

```diff
 class CrmLookupGuard(BaseModel):
     model_config = ConfigDict(extra="forbid")
+
+    class Plan(BaseModel):
+        plan_tier: str
+        renewal: str
+
     customer_id: str
     name: str
     open_tickets: int
-    tier: str
+    plan: Plan
@@
-        "tier": response["tier"],
+        "tier": response["plan"]["plan_tier"],
```

```
1 passed in 0.07s
applied to run 05d2c9b206ae
```

## Resume (same run id)

```
completed 05d2c9b206ae
```

Earlier steps never reran; the ticket key was reused. See
[report.md](report.md) for the agent-vs-script numbers.
