# Croncrete report

## Agent (recorded)
- runs: 10
- success_rate: 100.00%
- avg_cost_usd: 0.01
- avg_latency_ms: 6477
- model_calls_per_run: 4.00
- tools: crm_lookup, outbox_send, tickets_create

## Script (replay)
- runs: 10
- success_rate: 100.00%
- avg_cost_usd: 0.00
- avg_latency_ms: 11168
- model_calls_per_run: 1.00
- scopes: GET /crm/customers, POST /tickets, POST /outbox

## Deterministic (5 template-following runs)
- customer_id: 5/5
- name: 5/5
- tier: 5/5
- recipient: 5/5
- reply: 5/5

## Priority agreement
- 10/10 (100.00%)
