"""Support-triage agent: three tools, capped loop, records one trace per email.

Usage: .venv/bin/python examples/support_triage/agent.py <email_id>
Env: MOCK_BASE_URL (default http://127.0.0.1:8000).
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from croncrete import Recorder, llm  # noqa: E402

HERE = Path(__file__).resolve().parent
MAX_STEPS = 8

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "crm_lookup": {"endpoint": "GET /crm/customers", "args": {"email": "string"}},
    "tickets_create": {
        "endpoint": "POST /tickets",
        "args": {"customer_id": "string", "priority": "string", "summary": "string"},
    },
    "outbox_send": {
        "endpoint": "POST /outbox",
        "args": {"to": "string", "subject": "string", "body": "string"},
    },
}

OPENAI_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "crm_lookup",
            "description": "Look up a customer by email.",
            "parameters": {
                "type": "object",
                "properties": {"email": {"type": "string"}},
                "required": ["email"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tickets_create",
            "description": "File a support ticket. Priority is P1, P2, or P3.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string"},
                    "priority": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["customer_id", "priority", "summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "outbox_send",
            "description": "Send the reply email.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
]


def base_url() -> str:
    return os.environ.get("MOCK_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def crm_lookup(email: str) -> dict[str, Any]:
    r = httpx.get(f"{base_url()}/crm/customers", params={"email": email}, timeout=10)
    r.raise_for_status()
    return r.json()


def tickets_create(customer_id: str, priority: str, summary: str) -> dict[str, Any]:
    # Agent-side key is random per call; the compiler injects run-scoped keys.
    r = httpx.post(
        f"{base_url()}/tickets",
        json={
            "customer_id": customer_id,
            "priority": priority,
            "summary": summary,
            "idempotency_key": f"agent-{uuid.uuid4().hex[:12]}",
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def outbox_send(to: str, subject: str, body: str) -> dict[str, Any]:
    r = httpx.post(
        f"{base_url()}/outbox", json={"to": to, "subject": subject, "body": body}, timeout=10
    )
    r.raise_for_status()
    return r.json()


TOOLS = {"crm_lookup": crm_lookup, "tickets_create": tickets_create, "outbox_send": outbox_send}


def build_system_prompt() -> str:
    template = (HERE / "reply_template.txt").read_text()
    sla = (HERE / "sla.json").read_text()
    return (
        "You triage one support email. Use the tools in order: crm_lookup, "
        "tickets_create, outbox_send.\n"
        f"Reply template (fill exactly):\n{template}\n"
        f"SLA by tier (JSON):\n{sla}\n"
        "Rules: copy `summary` from the email subject. Set `to` to the sender. "
        "Set priority P1 for outages/blocked production, P2 for degraded/time-bound, "
        "P3 for routine. Render the template with the ticket id, priority, and SLA."
    )


def run_agent(email: dict[str, Any], run: Any) -> dict[str, Any] | None:
    """Execute the capped loop; steps recorded via run.tool. None = over cap."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": json.dumps(email, sort_keys=True)},
    ]
    calls = 0
    while True:
        result = llm.chat(role="agent", messages=messages, tools=OPENAI_TOOLS)
        assistant: dict[str, Any] = {"role": "assistant", "content": result.text}
        if result.tool_calls:
            assistant["tool_calls"] = [
                {
                    "id": f"call_{i}",
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                }
                for i, c in enumerate(result.tool_calls)
            ]
        messages.append(assistant)
        if not result.tool_calls:
            break
        for c in result.tool_calls:
            calls += 1
            if calls > MAX_STEPS:
                return None
            fn = TOOLS[c.name]
            observed = run.tool(c.name, fn, **c.arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": f"call_{calls - 1}",
                    "content": json.dumps(observed, sort_keys=True),
                }
            )
    return assemble_output(email, run)


def assemble_output(email: dict[str, Any], run: Any) -> dict[str, Any] | None:
    ticket_args: dict[str, Any] = {}
    ticket_id: str | None = None
    send_args: dict[str, Any] = {}
    for s in run._steps:
        if s.tool == "tickets_create":
            ticket_args = s.args
            ticket_id = s.result.get("ticket_id")
        elif s.tool == "outbox_send":
            send_args = s.args
    if ticket_id is None or not send_args:
        return None
    return {
        "ticket_id": ticket_id,
        "priority": ticket_args.get("priority"),
        "to": send_args.get("to"),
        "subject": send_args.get("subject"),
        "body": send_args.get("body"),
    }


def record_email(email_id: str) -> None:
    r = httpx.get(f"{base_url()}/inbox/{email_id}", timeout=10)
    r.raise_for_status()
    email = r.json()
    rec = Recorder(str(HERE / "traces"))
    with rec.run(input=email) as run:
        output = run_agent(email, run)
        run.output(output)
    print(f"{email_id}: {'failed' if output is None else 'ok'}")


if __name__ == "__main__":
    record_email(sys.argv[1])
