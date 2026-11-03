"""Report: agent stats from traces, script stats from replay. Keyless.

Judge answers are canned per trace (plumbing test); live priorities land
with Spark Open keys + recorded cassettes.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples" / "support_triage" / "workflows"))
sys.path.insert(0, str(ROOT / "src"))

import triage  # noqa: E402

from croncrete import llm  # noqa: E402
from croncrete.report import report  # noqa: E402
from croncrete.tracing import Recorder  # noqa: E402

TRACES = ROOT / "examples" / "support_triage" / "traces"
WORKFLOW = ROOT / "examples" / "support_triage" / "workflows" / "triage.py"


def _cassette_all(cassettes: Path):
    for f in sorted(TRACES.glob("e*.json")):
        t = Recorder.load(f)
        by_tool = {s.tool: s for s in t.steps}
        crm = by_tool["crm_lookup"].result
        messages = triage.judge_messages(
            dict(t.input),
            {"tier": crm["tier"], "open_tickets": crm["open_tickets"]},
            by_tool["tickets_create"].args["summary"],
        )
        key = llm.cassette_key("judge", llm.strip_for_prompt(messages), None, triage.Priority)
        (cassettes / f"{key}.json").write_text(
            json.dumps(
                {
                    "request": {"role": "judge"},
                    "response": {
                        "text": f'"{by_tool["tickets_create"].args["priority"]}"',
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


def test_report_replay_and_agreement(tmp_path, monkeypatch):
    cassettes = tmp_path / "cassettes"
    cassettes.mkdir()
    _cassette_all(cassettes)
    monkeypatch.setenv("CRONCRETE_REPLAY", "1")
    monkeypatch.setenv("CRONCRETE_CASSETTES", str(cassettes))
    rep = report(str(TRACES), WORKFLOW)
    assert rep.agent.runs == 10
    assert rep.agent.success_rate == 100.0
    assert rep.agent_tools == ["crm_lookup", "outbox_send", "tickets_create"]
    assert rep.script.runs == 10
    assert rep.script.model_calls_per_run == 1.0
    assert rep.script_scopes == [
        "GET /crm/customers",
        "POST /tickets",
        "POST /outbox",
    ]
    assert rep.following_runs == 5
    assert all(v == "5/5" for v in rep.deterministic.values())
    assert rep.priority_agree == 10 and rep.priority_total == 10
    md = rep.to_markdown()
    for section in (
        "## Agent (recorded)",
        "## Script (replay)",
        "## Deterministic",
        "## Priority agreement",
    ):
        assert section in md
    assert "10/10 (100.00%)" in md
