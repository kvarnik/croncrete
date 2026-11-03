"""Wiring: real traces wire cleanly, fixtures flag, ambiguity fails named."""

from pathlib import Path

import pytest

from croncrete import dataflow
from croncrete.tracing import Recorder

ROOT = Path(__file__).resolve().parents[1]
TRACES = sorted((ROOT / "examples" / "support_triage" / "traces").glob("e*.json"))


def load_traces():
    return [Recorder.load(p) for p in TRACES]


def test_real_traces_form_one_clean_group():
    group = dataflow.select_group(load_traces())
    assert len(group) == 10
    assert [s.tool for s in group[0].steps] == ["crm_lookup", "tickets_create", "outbox_send"]


def test_real_traces_wire_to_to_sender_and_are_unflagged():
    group = dataflow.select_group(load_traces())
    for t in group:
        assert dataflow.scan_trace(t) is None
    wiring = dataflow.wire_group(group)
    assert wiring[0] == {"email": "input.sender"}
    assert wiring[1]["customer_id"] == "crm_lookup.customer_id"
    assert wiring[1]["summary"] == "input.subject"
    assert wiring[1]["priority"] == "judge"
    assert wiring[2]["to"] == "input.sender"
    assert wiring[2]["subject"] == "template"
    assert wiring[2]["body"] == "template"


def test_fixtures_are_flagged():
    for name in ("flag_to_external.json", "flag_to_injected.json"):
        trace = Recorder.load(ROOT / "tests" / "fixtures" / name)
        reason = dataflow.scan_trace(trace)
        assert reason is not None and "outbox_send.to" in reason


def _trace_with_summary(summary, subject="Subj", body="Body"):
    base = Recorder.load(TRACES[0]).model_copy(deep=True)
    base.input["subject"] = subject
    base.input["body"] = body
    for s in base.steps:
        if s.tool == "tickets_create":
            s.args["summary"] = summary
    return base


def test_ambiguous_value_fails_naming_argument():
    # subject == body in both traces: summary matches two stable paths.
    group = [_trace_with_summary("same", "same", "same") for _ in range(2)]
    with pytest.raises(dataflow.UnexplainedArgument) as exc:
        dataflow.wire_group(group)
    assert exc.value.argument == "tickets_create.summary"
    assert "ambiguous" in str(exc.value)


def test_unmatched_value_fails_naming_argument():
    group = [_trace_with_summary("paraphrased beyond matching") for _ in range(2)]
    with pytest.raises(dataflow.UnexplainedArgument) as exc:
        dataflow.wire_group(group)
    assert exc.value.argument == "tickets_create.summary"
