"""Agent-vs-script report: traces for the agent, validation replay for the script.

Replay feeds each clean trace's recorded tool responses through the compiled
module (guards, maps, arg wiring, render) and calls the live judge unless
replaying from cassettes. No HTTP, no checkpoints, no timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from croncrete import dataflow, llm
from croncrete.runtime.executor import load_workflow
from croncrete.tracing import Recorder, TraceRecord


@dataclass
class SideStats:
    runs: int
    success_rate: float
    avg_cost_usd: float
    avg_latency_ms: float
    model_calls_per_run: float


@dataclass
class Report:
    agent: SideStats
    agent_tools: list[str]
    script: SideStats
    script_scopes: list[str]
    following_runs: int
    deterministic: dict[str, str]
    priority_agree: int
    priority_total: int

    def priority_rate(self) -> float:
        return self.priority_agree / self.priority_total if self.priority_total else 0.0

    def to_markdown(self) -> str:
        a, s = self.agent, self.script
        lines = [
            "# Croncrete report",
            "",
            "## Agent (recorded)",
            f"- runs: {a.runs}",
            f"- success_rate: {a.success_rate:.2f}%",
            f"- avg_cost_usd: {a.avg_cost_usd:.2f}",
            f"- avg_latency_ms: {a.avg_latency_ms:.0f}",
            f"- model_calls_per_run: {a.model_calls_per_run:.2f}",
            f"- tools: {', '.join(self.agent_tools)}",
            "",
            "## Script (replay)",
            f"- runs: {s.runs}",
            f"- success_rate: {s.success_rate:.2f}%",
            f"- avg_cost_usd: {s.avg_cost_usd:.2f}",
            f"- avg_latency_ms: {s.avg_latency_ms:.0f}",
            f"- model_calls_per_run: {s.model_calls_per_run:.2f}",
            f"- scopes: {', '.join(self.script_scopes)}",
            "",
            f"## Deterministic ({self.following_runs} template-following runs)",
        ]
        for field, score in self.deterministic.items():
            lines.append(f"- {field}: {score}")
        lines += [
            "",
            "## Priority agreement",
            f"- {self.priority_agree}/{self.priority_total} ({self.priority_rate() * 100:.2f}%)",
            "",
        ]
        return "\n".join(lines)


def _is_following(t: TraceRecord, mod: ModuleType) -> bool:
    by_tool = {s.tool: s for s in t.steps}
    crm = by_tool["crm_lookup"].result
    tic = by_tool["tickets_create"]
    send = by_tool["outbox_send"].args
    body = mod.TEMPLATE.format(
        subject=t.input["subject"],
        name=crm["name"],
        ticket_id=tic.result["ticket_id"],
        priority=tic.args["priority"],
        sla=mod.SLA_TABLE[crm["tier"]],
    ).rstrip("\n")
    return send["body"].rstrip("\n") == body and send["subject"] == f"Re: {t.input['subject']}"


def _strip_spans(body: str, priority: str, sla: str) -> str:
    return body.replace(priority, "").replace(sla, "")


def _replay(mod: ModuleType, t: TraceRecord) -> dict[str, Any]:
    """Replay recorded responses; judge live unless replaying. Returns script values."""
    calls: list[tuple[str, int, int, int]] = []
    llm.set_recorder_hook(lambda role, ti, to, ms: calls.append((role, ti, to, ms)))
    try:
        input = dict(t.input)
        prev: dict[str, dict[str, Any]] = {}
        for i, step in enumerate(t.steps):
            meta = mod.STEPS[i]
            raw = dict(step.result)
            getattr(mod, meta["guard"]).model_validate(raw)
            mapped = getattr(mod, meta["map_fn"])(raw, dict(input), prev)
            getattr(mod, meta["output_model"]).model_validate(mapped)
            prev[step.tool] = mapped
        prio: Any = None
        built: dict[int, dict[str, Any]] = {}
        for i, step in enumerate(t.steps):
            earlier = {s.tool: prev[s.tool] for s in t.steps[:i]}
            args = mod.build_args(
                i, dict(input), earlier, f"replay-{t.input.get('id', t.run_id)}", priority=prio
            )
            if "priority" in args:
                prio = args["priority"]
            built[i] = args
        sent = next(args for args in built.values() if "to" in args)
        mod.validate_run(prio, sent["to"], input["sender"])
    finally:
        llm.set_recorder_hook(None)
    judge = [c for c in calls if c[0] == "judge"]
    cfg = llm.get_role_config("judge")
    cost = sum((ti * cfg.price_in + to * cfg.price_out) / 1_000_000 for _, ti, to, _ in judge)
    return {
        "prev": prev,
        "built": built,
        "priority": str(prio),
        "judge_ms": sum(ms for _, _, _, ms in judge),
        "judge_cost": cost,
        "judge_calls": len(judge),
    }


def report(traces_dir: str, workflow: str | Path) -> Report:
    traces = sorted(Path(traces_dir).glob("*.json"))
    records = [Recorder.load(p) for p in traces]
    mod = load_workflow(workflow)

    agent_runs = len(records)
    agent_ok = sum(1 for t in records if t.output is not None and not t.flagged)
    agent_tools = sorted({s.tool for t in records for s in t.steps})
    agent = SideStats(
        runs=agent_runs,
        success_rate=100 * agent_ok / agent_runs if agent_runs else 0.0,
        avg_cost_usd=sum(t.totals.cost_usd for t in records) / agent_runs if agent_runs else 0.0,
        avg_latency_ms=sum(t.totals.latency_ms for t in records) / agent_runs
        if agent_runs
        else 0.0,
        model_calls_per_run=sum(len(t.llm_calls) for t in records) / agent_runs
        if agent_runs
        else 0.0,
    )

    group = dataflow.select_group(records)
    det_fields = ["customer_id", "name", "tier", "recipient", "reply"]
    det_match = dict.fromkeys(det_fields, 0)
    following = 0
    agree = 0
    script_costs, script_lat, script_calls = [], [], []
    for t in group:
        r = _replay(mod, t)
        by_tool = {s.tool: s for s in t.steps}
        trace_prio = by_tool["tickets_create"].args["priority"]
        agree += r["priority"] == trace_prio
        script_costs.append(r["judge_cost"])
        script_lat.append(sum(s.latency_ms for s in t.steps) + r["judge_ms"])
        script_calls.append(r["judge_calls"])
        if not _is_following(t, mod):
            continue
        following += 1
        crm, send = by_tool["crm_lookup"].result, by_tool["outbox_send"].args
        script_crm = r["prev"]["crm_lookup"]
        det_match["customer_id"] += script_crm["customer_id"] == crm["customer_id"]
        det_match["name"] += script_crm["name"] == crm["name"]
        det_match["tier"] += script_crm["tier"] == crm["tier"]
        built_send = next(a for a in r["built"].values() if "to" in a)
        det_match["recipient"] += built_send["to"] == send["to"]
        script_body = mod.render_reply(dict(t.input), r["prev"], r["priority"])["body"]
        det_match["reply"] += _strip_spans(
            script_body, r["priority"], mod.SLA_TABLE[script_crm["tier"]]
        ) == _strip_spans(send["body"], trace_prio, mod.SLA_TABLE[crm["tier"]])

    n = len(group)
    script = SideStats(
        runs=n,
        success_rate=100.0 if n else 0.0,
        avg_cost_usd=sum(script_costs) / n if n else 0.0,
        avg_latency_ms=sum(script_lat) / n if n else 0.0,
        model_calls_per_run=sum(script_calls) / n if n else 0.0,
    )
    return Report(
        agent=agent,
        agent_tools=agent_tools,
        script=script,
        script_scopes=list(mod.SCOPES),
        following_runs=following,
        deterministic={f: f"{det_match[f]}/{following}" for f in det_fields},
        priority_agree=agree,
        priority_total=n,
    )
