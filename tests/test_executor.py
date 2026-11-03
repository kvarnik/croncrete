"""Executor: completes with checkpoints, halts on drift, scopes enforced."""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples" / "support_triage" / "workflows"))
sys.path.insert(0, str(ROOT / "src"))

import triage  # noqa: E402

from croncrete import llm  # noqa: E402
from croncrete.runtime.executor import run  # noqa: E402
from croncrete.runtime.tools_client import ScopeError, ToolsClient  # noqa: E402

WORKFLOW = ROOT / "examples" / "support_triage" / "workflows" / "triage.py"
EMAIL = {
    "id": "e-test",
    "sender": "alice@acme.example",
    "subject": "URGENT: production API down",
    "body": "Everything is broken, please help urgently.",
}
CRM_OK = {"customer_id": "c-001", "name": "Alice", "tier": "enterprise", "open_tickets": 2}
CRM_DRIFT = {
    "customer_id": "c-001",
    "name": "Alice",
    "plan": {"plan_tier": "enterprise", "renewal": "2027-01-01"},
    "open_tickets": 2,
}


class FakeTools:
    def __init__(self, crm):
        self.crm = crm
        self.calls = []

    def call(self, endpoint, **kwargs):
        self.calls.append(endpoint)
        if endpoint == "GET /crm/customers":
            return dict(self.crm)
        if endpoint == "POST /tickets":
            return {"ticket_id": "T-1"}
        if endpoint == "POST /outbox":
            return {"message_id": "M-1"}
        raise AssertionError(endpoint)


def _judge_cassette(cassettes: Path, priority: str = "P1"):
    customer = {k: CRM_OK[k] for k in ("tier", "open_tickets")}
    messages = triage.judge_messages(EMAIL, customer, EMAIL["subject"])
    key = llm.cassette_key("judge", llm.strip_for_prompt(messages), None, triage.Priority)
    (cassettes / f"{key}.json").write_text(
        json.dumps(
            {
                "request": {"role": "judge"},
                "response": {
                    "text": f'"{priority}"',
                    "tool_calls": [],
                    "tokens_in": 100,
                    "tokens_out": 5,
                    "cached_in": 0,
                    "latency_ms": 200,
                    "cost_usd": 0.0,
                },
            }
        )
    )


def test_run_completes_and_checkpoints(tmp_path, monkeypatch):
    cassettes = tmp_path / "cassettes"
    cassettes.mkdir()
    _judge_cassette(cassettes)
    monkeypatch.setenv("CRONCRETE_REPLAY", "1")
    monkeypatch.setenv("CRONCRETE_CASSETTES", str(cassettes))
    db = tmp_path / "state.db"
    result = run(WORKFLOW, dict(EMAIL), FakeTools(CRM_OK), state_db=db)
    assert result.status == "completed"
    assert result.output["ticket_id"] == "T-1"
    assert result.output["priority"] == "P1"
    assert result.output["to"] == EMAIL["sender"]
    assert result.output["subject"] == f"Re: {EMAIL['subject']}"
    assert "T-1" in result.output["body"] and "4 hours" in result.output["body"]
    con = sqlite3.connect(db)
    assert con.execute("SELECT status FROM runs").fetchone() == ("completed",)
    assert con.execute("SELECT COUNT(*) FROM step_runs").fetchone() == (3,)
    assert con.execute("SELECT COUNT(*) FROM calls").fetchone() == (3,)
    assert con.execute("SELECT DISTINCT attempt FROM step_runs").fetchall() == [(0,)]


def test_drift_halts_at_crm_with_shapes(tmp_path, monkeypatch):
    monkeypatch.setenv("CRONCRETE_REPLAY", "1")  # empty dir: any judge call explodes
    monkeypatch.setenv("CRONCRETE_CASSETTES", str(tmp_path))
    db = tmp_path / "state.db"
    tools = FakeTools(CRM_DRIFT)
    result = run(WORKFLOW, dict(EMAIL), tools, state_db=db)
    assert result.status == "halted"
    assert result.failed_step == 0
    assert result.error["stage"] == "guard"
    assert "tier" in result.error["expected"]["properties"]
    assert result.error["observed"]["plan"]["plan_tier"] == "enterprise"
    assert tools.calls == ["GET /crm/customers"]  # halted before later steps
    con = sqlite3.connect(db)
    assert con.execute("SELECT status FROM runs").fetchone() == ("halted",)


def test_scoped_client_rejects_admin():
    client = ToolsClient("http://127.0.0.1:9", list(triage.SCOPES))
    with pytest.raises(ScopeError):
        client.call("POST /admin/drift")


def test_validate_rejects_wrong_recipient():
    with pytest.raises(ValueError, match="recipient"):
        triage.validate_run("P1", "evil@x.example", EMAIL["sender"])
    triage.validate_run("P1", EMAIL["sender"], EMAIL["sender"])  # ok
    with pytest.raises(ValueError):
        triage.validate_run("P0", EMAIL["sender"], EMAIL["sender"])
