"""Recorder writes schema-valid traces with adding-up totals. No network."""

from croncrete import Recorder, TraceRecord


def test_recorder_writes_valid_trace(tmp_path):
    rec = Recorder(str(tmp_path))
    with rec.run(input={"id": "e1", "sender": "a@x.com"}) as run:
        out = run.tool("crm_lookup", lambda email: {"name": "A"}, email="a@x.com")
        assert out == {"name": "A"}
        run.output({"ok": True})
    trace = TraceRecord.model_validate_json((tmp_path / "e1.json").read_text())
    assert trace.run_id
    assert trace.output == {"ok": True}
    assert len(trace.steps) == 1
    assert trace.steps[0].tool == "crm_lookup"
    assert trace.steps[0].args == {"email": "a@x.com"}
    assert trace.totals.steps == 1
    assert trace.flagged is False


def test_totals_add_up_and_hook_appends_llm_calls(tmp_path):
    rec = Recorder(str(tmp_path))
    with rec.run(input={"id": "e2"}) as run:
        run.tool("crm_lookup", lambda: {"tier": "pro"})
        run._hook("agent", 100, 20, 300)  # simulates one recorded model call
    trace = Recorder.load(tmp_path / "e2.json")
    assert len(trace.llm_calls) == 1
    assert trace.totals.tokens_in == 100
    assert trace.totals.tokens_out == 20
    assert trace.totals.cost_usd > 0
    assert trace.totals.latency_ms >= 300
