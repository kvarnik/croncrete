"""Mock CRM, ticket desk, and outbox. In-memory; restart clears tickets/outbox."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

seed = json.loads((Path(__file__).resolve().parent.parent / "seed.json").read_text())
CUSTOMERS: dict[str, dict[str, Any]] = {c["email"]: c for c in seed["customers"]}
EMAILS: dict[str, dict[str, Any]] = {e["id"]: e for e in seed["emails"]}

app = FastAPI(title="support-triage mocks")

drift_on = False
tickets_by_key: dict[str, str] = {}
outbox: list[dict[str, Any]] = []
ticket_seq = 1000
message_seq = 5000


class TicketRequest(BaseModel):
    customer_id: str
    priority: str
    summary: str
    idempotency_key: str


class OutboxRequest(BaseModel):
    to: str
    subject: str
    body: str


@app.get("/inbox/{email_id}")
def get_inbox(email_id: str) -> dict[str, Any]:
    if email_id not in EMAILS:
        raise HTTPException(status_code=404, detail="unknown email")
    return EMAILS[email_id]


@app.get("/crm/customers")
def get_customer(email: str) -> dict[str, Any]:
    if email not in CUSTOMERS:
        raise HTTPException(status_code=404, detail="unknown customer")
    c = CUSTOMERS[email]
    if drift_on:
        return {
            "customer_id": c["customer_id"],
            "name": c["name"],
            "plan": {"plan_tier": c["tier"], "renewal": "2027-01-01"},
            "open_tickets": c["open_tickets"],
        }
    return {
        "customer_id": c["customer_id"],
        "name": c["name"],
        "tier": c["tier"],
        "open_tickets": c["open_tickets"],
    }


@app.post("/tickets")
def create_ticket(req: TicketRequest) -> dict[str, str]:
    global ticket_seq
    if req.idempotency_key in tickets_by_key:
        return {"ticket_id": tickets_by_key[req.idempotency_key]}
    ticket_seq += 1
    ticket_id = f"T-{ticket_seq}"
    tickets_by_key[req.idempotency_key] = ticket_id
    return {"ticket_id": ticket_id}


@app.post("/outbox")
def send_outbox(req: OutboxRequest) -> dict[str, str]:
    global message_seq
    message_seq += 1
    message_id = f"M-{message_seq}"
    outbox.append({"message_id": message_id, **req.model_dump()})
    return {"message_id": message_id}


@app.post("/admin/drift")
def toggle_drift() -> dict[str, bool]:
    global drift_on
    drift_on = not drift_on
    return {"drift": drift_on}
