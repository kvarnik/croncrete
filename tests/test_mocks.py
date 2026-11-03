"""Mock services: inbox, CRM shapes, idempotent tickets, drift. No network."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples" / "support_triage"))

from fastapi.testclient import TestClient  # noqa: E402
from mock_services.app import app  # noqa: E402

client = TestClient(app)


def test_inbox_returns_email():
    r = client.get("/inbox/e01")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "e01"
    assert set(body) == {"id", "sender", "subject", "body"}


def test_crm_normal_shape():
    r = client.get("/crm/customers", params={"email": "alice@acme.example"})
    assert r.status_code == 200
    assert set(r.json()) == {"customer_id", "name", "tier", "open_tickets"}


def test_tickets_idempotent_on_key():
    payload = {
        "customer_id": "c-001",
        "priority": "P1",
        "summary": "x",
        "idempotency_key": "test-key-1",
    }
    r1 = client.post("/tickets", json=payload)
    r2 = client.post("/tickets", json=payload)
    assert r1.json()["ticket_id"] == r2.json()["ticket_id"]


def test_drift_toggles_crm_shape_and_back():
    assert client.post("/admin/drift").json() == {"drift": True}
    r = client.get("/crm/customers", params={"email": "alice@acme.example"})
    assert set(r.json()) == {"customer_id", "name", "plan", "open_tickets"}
    assert set(r.json()["plan"]) == {"plan_tier", "renewal"}
    assert client.post("/admin/drift").json() == {"drift": False}
    r = client.get("/crm/customers", params={"email": "alice@acme.example"})
    assert "tier" in r.json()


def test_outbox_returns_message_id():
    r = client.post("/outbox", json={"to": "a@x.example", "subject": "Re: hi", "body": "hello"})
    assert r.status_code == 200
    assert r.json()["message_id"].startswith("M-")
