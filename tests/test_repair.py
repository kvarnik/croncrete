"""Repair + resume: splice pair, approve, same-run_id resume. Keyless.

The repair-role answer is canned (plumbing test); live model repair is
verified separately once Spark Open keys land.
"""

import ast
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples" / "support_triage" / "workflows"))
sys.path.insert(0, str(ROOT / "src"))

import triage  # noqa: E402

from croncrete import llm  # noqa: E402
from croncrete.repair.splice import RepairError, RepairOutput, repair, splice_nodes  # noqa: E402
from croncrete.runtime.executor import resume, run  # noqa: E402

WORKFLOW = ROOT / "examples" / "support_triage" / "workflows" / "triage.py"
EMAIL = {
    "id": "e-repair",
    "sender": "alice@acme.example",
    "subject": "URGENT: production API down",
    "body": "Everything is broken, please help urgently.",
}
CRM_DRIFT = {
    "customer_id": "c-001",
    "name": "Alice",
    "plan": {"plan_tier": "enterprise", "renewal": "2027-01-01"},
    "open_tickets": 2,
}
CANNED_GUARD = """class CrmLookupGuard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class Plan(BaseModel):
        model_config = ConfigDict(extra="forbid")
        plan_tier: str
        renewal: str

    customer_id: str
    name: str
    open_tickets: int
    plan: Plan
"""
CANNED_FN = """def map_crm_lookup(response, input, prev):
    return {
        "customer_id": response["customer_id"],
        "name": response["name"],
        "open_tickets": response["open_tickets"],
        "tier": response["plan"]["plan_tier"],
    }
"""


class FakeTools:
    def __init__(self, crm):
        self.crm = crm

    def call(self, endpoint, **kwargs):
        if endpoint == "GET /crm/customers":
            return dict(self.crm)
        if endpoint == "POST /tickets":
            return {"ticket_id": "T-9"}
        if endpoint == "POST /outbox":
            return {"message_id": "M-9"}
        raise AssertionError(endpoint)


def _judge_cassette(cassettes: Path):
    customer = {"tier": "enterprise", "open_tickets": 2}
    messages = triage.judge_messages(EMAIL, customer, EMAIL["subject"])
    key = llm.cassette_key("judge", llm.strip_for_prompt(messages), None, triage.Priority)
    (cassettes / f"{key}.json").write_text(
        json.dumps(
            {
                "request": {"role": "judge"},
                "response": {
                    "text": '"P1"',
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


def test_splice_replaces_only_the_pair():
    source = WORKFLOW.read_text()
    patched = splice_nodes(source, CANNED_FN, CANNED_GUARD, "map_crm_lookup", "CrmLookupGuard")

    def stripped(src):
        tree = ast.parse(src)
        tree.body = [
            n
            for n in tree.body
            if not (
                isinstance(n, (ast.FunctionDef, ast.ClassDef))
                and n.name in ("map_crm_lookup", "CrmLookupGuard")
            )
        ]
        return ast.dump(tree)

    assert stripped(source) == stripped(patched)
    assert "plan_tier" in patched
    with pytest.raises(RepairError):
        splice_nodes(
            source, "def wrong():\n    pass\n", CANNED_GUARD, "map_crm_lookup", "CrmLookupGuard"
        )


def test_repair_unknown_run_refused(tmp_path):
    with pytest.raises(RepairError, match="unknown run_id"):
        repair("nope123", state_db=tmp_path / "db.sqlite")


def test_full_cycle_halt_repair_approve_resume(tmp_path, monkeypatch):
    workdir = tmp_path / "wf"
    workdir.mkdir()
    wf_copy = workdir / "triage.py"
    shutil.copy(WORKFLOW, wf_copy)
    db = tmp_path / "state.db"

    monkeypatch.setenv("CRONCRETE_REPLAY", "1")
    monkeypatch.setenv("CRONCRETE_CASSETTES", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    halted = run(wf_copy, dict(EMAIL), FakeTools(CRM_DRIFT), state_db=db)
    assert halted.status == "halted" and halted.failed_step == 0

    canned = llm.LLMResult(
        text=RepairOutput(
            function_source=CANNED_FN, guard_model_source=CANNED_GUARD
        ).model_dump_json(),
        tokens_in=100,
        tokens_out=200,
    )
    real_chat = llm.chat

    def fake_chat(role, *args, **kwargs):
        if role == "repair":
            assert kwargs.get("response_schema") is RepairOutput
            return canned
        return real_chat(role, *args, **kwargs)

    monkeypatch.setattr(llm, "chat", fake_chat)
    proposal = repair(
        halted.run_id,
        state_db=db,
        patched_dir=tmp_path / "patched",
        repairs_dir=tmp_path / "repairs",
    )
    assert "plan_tier" in proposal.diff
    assert "passed" in proposal.test_results
    assert (tmp_path / "repairs" / f"{halted.run_id}.patch").exists()
    assert WORKFLOW.read_text().find("plan_tier") == -1  # untouched until approve
    proposal.approve()
    assert "plan_tier" in wf_copy.read_text()

    cassettes = tmp_path / "cassettes"
    cassettes.mkdir()
    _judge_cassette(cassettes)
    monkeypatch.setenv("CRONCRETE_CASSETTES", str(cassettes))
    resumed = resume(halted.run_id, FakeTools(CRM_DRIFT), state_db=db)
    assert resumed.status == "completed"
    assert resumed.run_id == halted.run_id
    assert resumed.output["priority"] == "P1"
    assert resumed.output["to"] == EMAIL["sender"]

    con = sqlite3.connect(db)
    crm_calls = con.execute(
        "SELECT attempt FROM calls WHERE endpoint='GET /crm/customers' ORDER BY attempt"
    ).fetchall()
    assert crm_calls == [(0,), (1,)]  # retried once, earlier steps never reran
    assert con.execute("SELECT COUNT(*) FROM calls WHERE endpoint LIKE 'POST%'").fetchone() == (2,)
    key = con.execute("SELECT args_json FROM calls WHERE endpoint='POST /tickets'").fetchone()[0]
    assert json.loads(key)["idempotency_key"] == f"{halted.run_id}:1"
    assert json.loads(key)["customer_id"] == "c-001"  # repaired map feeds downstream
