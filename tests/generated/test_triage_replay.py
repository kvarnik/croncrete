"""Generated replay test for triage. Replays recorded tool responses."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "examples" / "support_triage" / "workflows"))
sys.path.insert(0, str(ROOT / "src"))

import triage  # noqa: E402

from croncrete.tracing import Recorder  # noqa: E402

TRACES = ["e01.json", "e02.json", "e03.json"]
TRACES_DIR = ROOT / "examples" / "support_triage" / "traces"


def test_replay_first_three():
    for name in TRACES:
        t = Recorder.load(TRACES_DIR / name)
        prev = {}
        for i, step in enumerate(t.steps):
            meta = triage.STEPS[i]
            assert meta["tool"] == step.tool
            getattr(triage, meta["guard"]).model_validate(step.result)
            mapped = getattr(triage, meta["map_fn"])(dict(step.result), dict(t.input), prev)
            out_cls = getattr(triage, meta["output_model"])
            assert mapped == {f: step.result[f] for f in out_cls.model_fields}
            prev[step.tool] = mapped
        prio = next(s.args["priority"] for s in t.steps if s.tool == "tickets_create")
        for i, step in enumerate(t.steps):
            earlier = {s.tool: prev[s.tool] for s in t.steps[:i]}
            built = triage.build_args(i, dict(t.input), earlier, t.run_id, priority=prio)
            built.pop("idempotency_key", None)
            assert built == dict(step.args), name
        sent = next(s.args for s in t.steps if s.tool == "outbox_send")
        assert triage.render_reply(dict(t.input), prev, prio) == {
            "subject": sent["subject"],
            "body": sent["body"],
        }
        triage.validate_run(prio, sent["to"], t.input["sender"])
