"""Compile the largest clean trace group into a checked workflow module.

The generated module embeds the judge prompt, template, and SLA table as
constants, so the committed file is self-contained and deterministic. It
uses no imports beyond pydantic/typing/croncrete (no json: the fence stays
tight; message serialization is plain f-strings).
"""

from __future__ import annotations

import json
import pprint
import re
from pathlib import Path
from string import Formatter
from typing import Any

from pydantic import BaseModel

from croncrete import dataflow, llm
from croncrete.compiler import checks, schema_infer
from croncrete.tracing import Recorder, TraceRecord

JUDGE_CUSTOMER_STEP = "crm_lookup"
JUDGE_CUSTOMER_READS = ("tier", "open_tickets")
JUDGE_PROMPT_REGENS = 2


class JudgePrompt(BaseModel):
    judge_prompt: str


def generate_judge_prompt(group: list[TraceRecord]) -> str:
    """One compiler-role call; examples are stripped trace outcomes."""
    system = (Path(__file__).parent / "prompts" / "judge.md").read_text()
    examples = []
    for t in group:
        by_tool = {s.tool: s for s in t.steps}
        crm = by_tool["crm_lookup"].result
        ticket_args = by_tool["tickets_create"].args
        examples.append(
            {
                "email": {
                    "sender": t.input["sender"],
                    "subject": t.input["subject"],
                    "body": t.input["body"],
                },
                "customer": {"tier": crm["tier"], "open_tickets": crm["open_tickets"]},
                "summary": ticket_args["summary"],
                "priority": ticket_args["priority"],
            }
        )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(llm.strip_for_prompt(examples))},
    ]
    result = llm.chat(role="compiler", messages=messages, response_schema=JudgePrompt)
    return JudgePrompt.model_validate_json(result.text).judge_prompt


def _emit_str_const(name: str, value: str, width: int = 68) -> str:
    """Adjacent literal chunks that concatenate back to value exactly."""
    words = re.findall(r"\S+\s*", value) or [""]
    chunks: list[str] = []
    current = ""
    for w in words:
        if len(current) + len(w) > width and current:
            chunks.append(current)
            current = ""
        current += w
    if current or not chunks:
        chunks.append(current)
    lines = [f"{name} = ("]
    lines.extend(f"    {json.dumps(c)}" for c in chunks)
    lines.append(")")
    return "\n".join(lines)


def _template_vars(template: str) -> list[str]:
    return [f for _, f, _, _ in Formatter().parse(template) if f]


def _read_sets(
    group: list[TraceRecord],
    wiring: dict[int, dict[str, str]],
    guards: dict[str, schema_infer.ModelDef],
    template: str,
) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Per-tool read fields + template var resolution to generated exprs."""
    guard_fields: dict[str, set[str]] = {}
    for tool, g in guards.items():
        fields: set[str] = set()
        stack = [g]
        while stack:
            m = stack.pop()
            fields.update(f.name for f in m.fields)
            stack.extend(m.nested)
        guard_fields[tool] = fields

    order = [s.tool for s in group[0].steps]
    reads: dict[str, set[str]] = {tool: set() for tool in order}
    for i in range(len(order)):
        for prov in wiring[i].values():
            if prov.startswith(("input.", "literal:")) or prov in ("judge", "template"):
                continue
            tool, _, path = prov.partition(".")
            if "." in path:
                raise ValueError(f"nested provenance unsupported in MVP: {prov}")
            reads[tool].add(path)

    input_fields = set()
    for t in group:
        input_fields.update(t.input.keys())
    resolution: dict[str, str] = {}
    for var in _template_vars(template):
        if var in input_fields:
            resolution[var] = f'input["{var}"]'
            continue
        if var == "priority":
            resolution[var] = "priority"
            continue
        if var == "sla":
            continue  # handled via tier lookup below
        owners = [tool for tool in order if var in guard_fields.get(tool, set())]
        if not owners:
            raise ValueError(f"template var {var!r} resolves nowhere")
        reads[owners[0]].add(var)
        resolution[var] = f'prev["{owners[0]}"]["{var}"]'

    for f in JUDGE_CUSTOMER_READS:
        if f not in guard_fields.get(JUDGE_CUSTOMER_STEP, set()):
            raise ValueError(f"judge read {f!r} missing from {JUDGE_CUSTOMER_STEP}")
        reads[JUDGE_CUSTOMER_STEP].add(f)
    tier_owners = [tool for tool in order if "tier" in guard_fields.get(tool, set())]
    if "sla" in _template_vars(template):
        if not tier_owners:
            raise ValueError("template sla needs a tier field")
        reads[tier_owners[0]].add("tier")
        resolution["sla"] = f'SLA_TABLE[prev["{tier_owners[0]}"]["tier"]]'
    return reads, resolution


def _render_map_fn(tool: str, fields: set[str]) -> str:
    lines = [f"def map_{tool}(response, input, prev):"]
    if not fields:
        lines.append("    # nothing downstream reads this response")
        lines.append("    return {}")
        return "\n".join(lines)
    lines.append("    return {")
    for f in sorted(fields):
        lines.append(f'        "{f}": response["{f}"],')
    lines.append("    }")
    return "\n".join(lines)


def render_module(
    group: list[TraceRecord],
    wiring: dict[int, dict[str, str]],
    tools: dict[str, dict[str, Any]],
    template: str,
    sla: dict[str, str],
    judge_prompt: str,
) -> str:
    order = [s.tool for s in group[0].steps]
    responses: dict[str, list[dict[str, Any]]] = {tool: [] for tool in order}
    for t in group:
        for s in t.steps:
            responses[s.tool].append(dict(s.result))
    guards = {tool: schema_infer.infer_guard_model(tool, responses[tool]) for tool in order}
    reads, resolution = _read_sets(group, wiring, guards, template)
    outputs = {
        tool: schema_infer.infer_output_model(tool, responses[tool], reads[tool]) for tool in order
    }

    need_any = any(schema_infer.uses_any(m) for m in [*guards.values(), *outputs.values()])
    parts: list[str] = [
        '"""Compiled support-triage workflow. Generated; do not hand-edit."""',
        "",
        "from pydantic import BaseModel, ConfigDict"
        + ("\nfrom typing import Any" if need_any else "")
        + "\nfrom croncrete import Priority, llm",
    ]
    for tool in order:
        parts += ["", "", schema_infer.render_model(guards[tool])]
    for tool in order:
        parts += ["", "", schema_infer.render_model(outputs[tool])]
    for tool in order:
        parts += ["", "", _render_map_fn(tool, reads[tool])]

    steps = []
    for i, step in enumerate(group[0].steps):
        steps.append(
            {
                "index": i,
                "tool": step.tool,
                "endpoint": tools[step.tool]["endpoint"],
                "args": wiring[i],
                "guard": guards[step.tool].name,
                "map_fn": f"map_{step.tool}",
                "output_model": outputs[step.tool].name,
            }
        )
    scopes = [tools[tool]["endpoint"] for tool in order]
    parts += [
        "",
        "",
        f"STEPS = {pprint.pformat(steps, width=88, sort_dicts=False)}",
        "",
        f"SCOPES = {scopes!r}",
    ]
    parts += ["", "", _emit_str_const("JUDGE_PROMPT", judge_prompt)]
    parts += ["", "", _emit_str_const("TEMPLATE", template)]
    parts += ["", "", f"SLA_TABLE = {sla!r}"]

    customer_lines = "\n".join(
        f"            f\"{f}: {{customer['{f}']}}\\n\"" for f in JUDGE_CUSTOMER_READS
    )
    fmt_args = ", ".join(f"{var}={expr}" for var, expr in resolution.items())
    parts += [
        "",
        "",
        "def _dig(obj, dotted):",
        '    for part in dotted.split("."):',
        "        obj = obj[part]",
        "    return obj",
        "",
        "",
        "def _literal(marker):",
        '    return {"null": None, "true": True, "false": False, \'""\': ""}[marker[8:]]',
        "",
        "",
        "def judge_messages(email, customer, summary):",
        "    return [",
        '        {"role": "system", "content": JUDGE_PROMPT},',
        "        {",
        '            "role": "user",',
        '            "content": (',
        "                f\"sender: {email['sender']}\\n\"",
        "                f\"subject: {email['subject']}\\n\"",
        "                f\"body: {email['body']}\\n\"",
        customer_lines,
        '                f"summary: {summary}"',
        "            ),",
        "        },",
        "    ]",
        "",
        "",
        "def _priority_from_text(text):",
        "    t = text.strip()",
        '    if t.startswith("{"):',
        '        i = t.index("\\"priority\\"") + len("\\"priority\\"")',
        '        i = t.index(":", i) + 1',
        '        j = t.index("\\"", i) + 1',
        '        return Priority(t[j : t.index("\\"", j)])',
        "    return Priority(t.strip('\"'))",
        "",
        "",
        "def judge(email, customer, summary):",
        "    result = llm.chat(",
        '        role="judge",',
        "        messages=judge_messages(email, customer, summary),",
        "        response_schema=Priority,",
        "    )",
        "    return _priority_from_text(result.text)",
        "",
        "",
        "def render_reply(input, prev, priority):",
        f'    body = TEMPLATE.format({fmt_args}).rstrip("\\n")',
        '    return {"subject": f"Re: {input[\'subject\']}", "body": body}',
        "",
        "",
        "def validate_run(priority, to, sender):",
        "    Priority(priority)",
        "    if to != sender:",
        '        raise ValueError(f"recipient {to!r} != sender {sender!r}")',
        "",
        "",
        "def build_args(index, input, prev, run_id, priority=None):",
        "    step = STEPS[index]",
        "    args = {}",
        '    for arg, prov in step["args"].items():',
        '        if prov in ("judge", "template"):',
        "            continue",
        '        if prov.startswith("literal:"):',
        "            args[arg] = _literal(prov)",
        '        elif prov.startswith("input."):',
        "            args[arg] = _dig(input, prov[6:])",
        "        else:",
        '            tool, _, path = prov.partition(".")',
        "            args[arg] = _dig(prev[tool], path)",
        '    if step["tool"] == "tickets_create":',
        '        args["idempotency_key"] = f"{run_id}:{index}"',
        "        if priority is not None:",
        '            args["priority"] = priority',
        "        else:",
        f'            customer = prev["{JUDGE_CUSTOMER_STEP}"]',
        '            args["priority"] = judge(input, customer, args["summary"])',
        '    if step["tool"] == "outbox_send":',
        "        if priority is None:",
        '            raise ValueError("outbox_send needs priority")',
        "        rendered = render_reply(input, prev, priority)",
        '        args["subject"] = rendered["subject"]',
        '        args["body"] = rendered["body"]',
        "    return args",
        "",
    ]
    return "\n".join(parts)


def compile(
    traces_dir: str,
    tools: dict[str, dict[str, Any]],
    template: str,
    sla: str,
    trusted_fields: list[str] | tuple[str, ...] = ("input.sender",),
    out: str = "examples/support_triage/workflows/triage.py",
) -> Path:
    """Compile the largest clean group into a checked module. Returns its path."""
    traces = sorted(Path(traces_dir).glob("*.json"))
    records = [Recorder.load(p) for p in traces]
    for t in records:
        reason = dataflow.scan_trace(t, tuple(trusted_fields))
        if reason:
            t.flagged = True
            t.flag_reason = reason
    group = dataflow.select_group(records)
    wiring = dataflow.wire_group(group)
    template_text = Path(template).read_text()
    sla_table = json.loads(Path(sla).read_text())

    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    candidate = out_path.with_name(out_path.stem + ".candidate.py")
    # Dry run with a stub prompt: deterministic codegen bugs fail here with
    # zero live spend, before any model call.
    stub = render_module(group, wiring, tools, template_text, sla_table, "STUB PROMPT")
    candidate.write_text(stub)
    try:
        checks.check_file(candidate)
    finally:
        candidate.unlink(missing_ok=True)

    last_err: Exception | None = None
    for _ in range(JUDGE_PROMPT_REGENS + 1):
        prompt = generate_judge_prompt(group)
        source = render_module(group, wiring, tools, template_text, sla_table, prompt)
        try:
            checks.check_source(source)
        except checks.CheckError as e:
            last_err = e
            continue
        candidate.write_text(source)
        try:
            checks.check_file(candidate)
        except checks.CheckError as e:
            candidate.unlink(missing_ok=True)
            last_err = e
            continue
        candidate.replace(out_path)
        return out_path
    if last_err is not None:
        raise last_err
    raise RuntimeError("unreachable")


REPLAY_TEST_TEMPLATE = '''"""Generated replay test for {stem}. Replays recorded tool responses."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "examples" / "support_triage" / "workflows"))
sys.path.insert(0, str(ROOT / "src"))

import {stem}  # noqa: E402
from croncrete.tracing import Recorder  # noqa: E402

TRACES = {trace_names}
TRACES_DIR = ROOT / "examples" / "support_triage" / "traces"


def test_replay_first_three():
    for name in TRACES:
        t = Recorder.load(TRACES_DIR / name)
        prev = {{}}
        for i, step in enumerate(t.steps):
            meta = {stem}.STEPS[i]
            assert meta["tool"] == step.tool
            getattr({stem}, meta["guard"]).model_validate(step.result)
            mapped = getattr({stem}, meta["map_fn"])(dict(step.result), dict(t.input), prev)
            out_cls = getattr({stem}, meta["output_model"])
            assert mapped == {{f: step.result[f] for f in out_cls.model_fields}}
            prev[step.tool] = mapped
        prio = next(s.args["priority"] for s in t.steps if s.tool == "tickets_create")
        for i, step in enumerate(t.steps):
            earlier = {{s.tool: prev[s.tool] for s in t.steps[:i]}}
            built = {stem}.build_args(i, dict(t.input), earlier, t.run_id, priority=prio)
            built.pop("idempotency_key", None)
            assert built == dict(step.args), name
        sent = next(s.args for s in t.steps if s.tool == "outbox_send")
        assert {stem}.render_reply(dict(t.input), prev, prio) == {{
            "subject": sent["subject"],
            "body": sent["body"],
        }}
        {stem}.validate_run(prio, sent["to"], t.input["sender"])
'''


def generate_replay_test(trace_names: list[str], stem: str, test_path: str | Path) -> Path:
    """Write the replay test for a compiled workflow module."""
    path = Path(test_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(REPLAY_TEST_TEMPLATE.format(stem=stem, trace_names=[*trace_names]))
    checks.lint_file(path)
    return path
