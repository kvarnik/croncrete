"""Dataflow: group traces by tool signature, scan sends, wire arg provenance.

Provenance strings: `input.<dotted>` for email fields, `<tool>.<dotted>` for
earlier tool results, plus markers `judge` (priority), `template` (reply
subject/body), and `literal:<json>` (constant None/""/bool args).
"""

from __future__ import annotations

import json
from typing import Any

from croncrete.tracing import TraceRecord

# Args the compiler derives instead of wiring by value equality.
DERIVED: dict[tuple[str, str], str] = {
    ("tickets_create", "priority"): "judge",
    ("outbox_send", "subject"): "template",
    ("outbox_send", "body"): "template",
}

EXCLUDED_ARGS = {"idempotency_key"}  # compiler-injected, never matched

DEFAULT_TRUSTED = ("input.sender",)


class UnexplainedArgument(ValueError):
    def __init__(self, argument: str, reason: str) -> None:
        self.argument = argument
        super().__init__(f"{argument}: {reason}")


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Nested dicts to dotted paths; lists and scalars are leaves."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}{k}."))
        return out
    return {prefix.rstrip("."): obj}


def _is_ignored(value: Any) -> bool:
    return value is None or value == "" or isinstance(value, bool)


def _sources(trace: TraceRecord, upto: int) -> dict[str, Any]:
    src = {f"input.{k}": v for k, v in flatten(trace.input).items()}
    for prev in trace.steps[:upto]:
        for k, v in flatten(prev.result).items():
            src[f"{prev.tool}.{k}"] = v
    return src


def _candidates(value: Any, sources: dict[str, Any]) -> list[str]:
    if _is_ignored(value):
        return []
    return [p for p, v in sources.items() if not _is_ignored(v) and v == value]


def select_group(traces: list[TraceRecord]) -> list[TraceRecord]:
    """Drop flagged/failed traces; keep the largest tool-signature group.

    Callers sort by filename first so tie-breaking is deterministic.
    """
    clean = [t for t in traces if not t.flagged and t.output is not None]
    groups: dict[tuple[str, ...], list[TraceRecord]] = {}
    for t in clean:
        groups.setdefault(tuple(s.tool for s in t.steps), []).append(t)
    if not groups:
        raise ValueError("no clean traces to compile")
    return max(groups.values(), key=len)


def scan_trace(trace: TraceRecord, trusted_fields: tuple[str, ...] = DEFAULT_TRUSTED) -> str | None:
    """Flag a send whose `to` is neither trusted nor an earlier tool result."""
    for i, step in enumerate(trace.steps):
        if "to" not in step.args:
            continue
        cands = _candidates(step.args["to"], _sources(trace, i))
        trusted = [c for c in cands if c in trusted_fields or not c.startswith("input.")]
        if not trusted:
            return f"{step.tool}.to={step.args['to']!r} is not {trusted_fields} nor a tool result"
    return None


def wire_group(
    group: list[TraceRecord],
    derived: dict[tuple[str, str], str] = DERIVED,
) -> dict[int, dict[str, str]]:
    """Wire every arg to the one source path shared across the group."""
    wiring: dict[int, dict[str, str]] = {}
    for i, first in enumerate(group[0].steps):
        args: set[str] = set()
        for t in group:
            args.update(t.steps[i].args)
        args -= EXCLUDED_ARGS
        step_wiring: dict[str, str] = {}
        for arg in sorted(args):
            if (first.tool, arg) in derived:
                step_wiring[arg] = derived[(first.tool, arg)]
                continue
            if any(arg not in t.steps[i].args for t in group):
                raise UnexplainedArgument(f"{first.tool}.{arg}", "missing in some traces")
            vals = [t.steps[i].args[arg] for t in group]
            if all(_is_ignored(v) for v in vals):
                consts = {json.dumps(v, sort_keys=True) for v in vals}
                if len(consts) == 1:
                    step_wiring[arg] = f"literal:{consts.pop()}"
                    continue
                raise UnexplainedArgument(f"{first.tool}.{arg}", "inconsistent constant")
            common: set[str] | None = None
            for t in group:
                cands = set(_candidates(t.steps[i].args[arg], _sources(t, i)))
                common = cands if common is None else common & cands
            if common is None or len(common) == 0:
                raise UnexplainedArgument(f"{first.tool}.{arg}", "no matching source")
            if len(common) > 1:
                raise UnexplainedArgument(f"{first.tool}.{arg}", f"ambiguous: {sorted(common)}")
            step_wiring[arg] = common.pop()
        wiring[i] = step_wiring
    return wiring
