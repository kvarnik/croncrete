"""One-step repair: model-written function + guard, spliced after approval.

Flow: load the halted run, ask the repair role for two sources, validate the
new guard against the drifted response and the new function against the
ORIGINAL output model, AST-splice exactly those two nodes, write the patch,
generate + run a repair test. Nothing applies without approve().
"""

from __future__ import annotations

import ast
import difflib
import json
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from croncrete import llm
from croncrete.compiler import checks
from croncrete.runtime.executor import SCHEMA, load_workflow

PATCHED_DIR = ".croncrete/patched"
REPAIRS_DIR = "repairs"


class RepairError(RuntimeError):
    pass


class RepairOutput(BaseModel):
    function_source: str
    guard_model_source: str


@dataclass
class RepairProposal:
    run_id: str
    step_index: int
    diff: str
    patched_module: Path
    test_results: str
    _workflow: Path

    def approve(self) -> Path:
        """Copy the patched module over the workflow. This is the approval."""
        shutil.copy(self.patched_module, self._workflow)
        return self._workflow


def splice_nodes(source: str, fn_src: str, guard_src: str, fn_name: str, guard_name: str) -> str:
    """Replace exactly the named FunctionDef + ClassDef; nothing else changes.

    Textual replacement by node line spans, so untouched code (including
    string-chunk formatting) survives byte-for-byte.
    """
    _single_def(fn_src, ast.FunctionDef, fn_name)
    _single_def(guard_src, ast.ClassDef, guard_name)
    tree = ast.parse(source)
    spans = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in (fn_name, guard_name):
            spans[node.name] = (node.lineno, node.end_lineno)
    if set(spans) != {fn_name, guard_name}:
        raise RepairError(f"targets not found: {fn_name}, {guard_name}")
    lines = source.splitlines(keepends=True)
    repl = {fn_name: fn_src, guard_name: guard_src}
    for name in sorted(spans, key=lambda n: spans[n][0], reverse=True):
        start, end = spans[name]
        new_text = repl[name]
        if not new_text.endswith("\n"):
            new_text += "\n"
        lines[start - 1 : end] = [new_text]
    patched = "".join(lines)
    _assert_only_those_changed(source, ast.parse(patched), (fn_name, guard_name))
    return patched


def _single_def(source: str, kind: type, name: str) -> ast.AST:
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise RepairError(f"unparseable source for {name}: {e}") from e
    defs = [n for n in tree.body if isinstance(n, kind) and n.name == name]
    if len(defs) != 1 or len(tree.body) != 1:
        raise RepairError(f"expected exactly one {kind.__name__} {name}")
    return defs[0]


def _assert_only_those_changed(source: str, new_tree: ast.AST, names: tuple[str, str]) -> None:
    def stripped(tree: ast.AST) -> str:
        clone = ast.parse(ast.unparse(tree))
        clone.body = [
            n
            for n in clone.body
            if not (isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names)
        ]
        return ast.dump(clone)

    if stripped(ast.parse(source)) != stripped(new_tree):
        raise RepairError("splice touched nodes outside the pair")


def _run_row(state_db: Path, run_id: str) -> dict[str, Any]:
    con = sqlite3.connect(str(state_db))
    con.executescript(SCHEMA)
    row = con.execute(
        "SELECT workflow, input_json, status, failed_step, error_json FROM runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    con.close()
    if row is None:
        raise RepairError(f"unknown run_id: {run_id}")
    return {
        "workflow": row[0],
        "input": json.loads(row[1]),
        "status": row[2],
        "failed_step": row[3],
        "error": json.loads(row[4]) if row[4] else None,
    }


def _prev_at_failure(state_db: Path, run_id: str, failed_step: int) -> dict[str, dict]:
    con = sqlite3.connect(str(state_db))
    rows = con.execute(
        "SELECT tool, result_json FROM step_runs"
        " WHERE run_id=? AND step_index<? AND status='completed'",
        (run_id, failed_step),
    ).fetchall()
    con.close()
    return {tool: json.loads(result) for tool, result in rows}


def repair(
    run_id: str,
    state_db: str | Path = ".croncrete/state.db",
    patched_dir: str | Path = PATCHED_DIR,
    repairs_dir: str | Path = REPAIRS_DIR,
) -> RepairProposal:
    state_db = Path(state_db)
    row = _run_row(state_db, run_id)
    if row["status"] != "halted" or row["failed_step"] is None:
        raise RepairError(f"run {run_id} is not guard-halted")
    if (row["error"] or {}).get("stage") != "guard":
        raise RepairError(f"run {run_id} halted at {row['error']} (only guard halts repair)")
    failed_step: int = row["failed_step"]
    error = row["error"]
    wf_path = Path(row["workflow"])
    mod = load_workflow(wf_path)
    meta = mod.STEPS[failed_step]
    fn_name, guard_name = meta["map_fn"], meta["guard"]
    out_cls = getattr(mod, meta["output_model"])

    source = wf_path.read_text()
    tree = ast.parse(source)
    fn_src = guard_src = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            fn_src = ast.get_source_segment(source, node)
        if isinstance(node, ast.ClassDef) and node.name == guard_name:
            guard_src = ast.get_source_segment(source, node)
    if fn_src is None or guard_src is None:
        raise RepairError("originals not found")

    system = (Path(__file__).parent / "prompts" / "repair.md").read_text()
    context = {
        "tool": meta["tool"],
        "function_source": fn_src,
        "guard_source": guard_src,
        "output_fields": sorted(out_cls.model_fields),
        "failed_response": error["observed"],
        "error": {"expected": error["expected"]},
    }
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(llm.strip_for_prompt(context), sort_keys=True)},
    ]
    result = llm.chat(role="repair", messages=messages, response_schema=RepairOutput)
    output = RepairOutput.model_validate_json(result.text)

    for src in (output.function_source, output.guard_model_source):
        checks.check_source(src)
    guard_ns: dict[str, Any] = {}
    exec(
        "from pydantic import BaseModel, ConfigDict\nfrom typing import Any\n"
        + output.guard_model_source,
        guard_ns,
    )
    new_guard = guard_ns.get(guard_name)
    if new_guard is None:
        raise RepairError(f"repair guard must define {guard_name}")
    try:
        new_guard.model_validate(error["observed"])
    except ValidationError as e:
        raise RepairError(f"new guard rejects drifted response: {e}") from e
    fn_ns: dict[str, Any] = {}
    exec(output.function_source, fn_ns)
    new_fn = fn_ns.get(fn_name)
    if new_fn is None:
        raise RepairError(f"repair function must define {fn_name}")
    prev = _prev_at_failure(state_db, run_id, failed_step)
    try:
        mapped = new_fn(dict(error["observed"]), dict(row["input"]), prev)
        out_cls.model_validate(mapped)
    except ValidationError as e:
        raise RepairError(f"new function breaks original output model: {e}") from e

    patched_source = splice_nodes(
        source, output.function_source, output.guard_model_source, fn_name, guard_name
    )
    patched_dir = Path(patched_dir)
    patched_dir.mkdir(parents=True, exist_ok=True)
    patched_path = patched_dir / f"{wf_path.stem}_{run_id}.py"
    patched_path.write_text(patched_source)
    try:
        checks.check_file(patched_path)
    except checks.CheckError as e:
        raise RepairError(f"patched module fails checks: {e}") from e

    repairs_dir = Path(repairs_dir)
    repairs_dir.mkdir(parents=True, exist_ok=True)
    patch_path = repairs_dir / f"{run_id}.patch"
    diff = "".join(
        difflib.unified_diff(
            source.splitlines(keepends=True),
            patched_path.read_text().splitlines(keepends=True),
            fromfile=str(wf_path),
            tofile=str(patched_path),
        )
    )
    patch_path.write_text(diff)

    test_results = _generate_and_run_test(
        patched_dir, patched_path, wf_path, meta, error["observed"], row["input"], prev, run_id
    )
    return RepairProposal(
        run_id=run_id,
        step_index=failed_step,
        diff=diff,
        patched_module=patched_path,
        test_results=test_results,
        _workflow=wf_path,
    )


def _generate_and_run_test(
    patched_dir: Path,
    patched_path: Path,
    wf_path: Path,
    meta: dict[str, Any],
    observed: dict[str, Any],
    input: dict[str, Any],
    prev: dict[str, dict],
    run_id: str,
) -> str:
    test_path = patched_dir / f"test_repair_{run_id}.py"
    test_path.write_text(
        f'"""Generated repair test for run {run_id}."""\n'
        f"import json\nimport sys\n"
        f'sys.path.insert(0, "{patched_dir.resolve()}")\n'
        f'sys.path.insert(0, "{wf_path.parent.resolve()}")\n'
        f"import {patched_path.stem} as patched\n"
        f"import {wf_path.stem} as original\n"
        f"OBSERVED = json.loads({json.dumps(json.dumps(observed))})\n"
        f"INPUT = json.loads({json.dumps(json.dumps(input))})\n"
        f"PREV = json.loads({json.dumps(json.dumps(prev))})\n"
        f"def test_repaired_step_matches_original_output():\n"
        f"    raw = dict(OBSERVED)\n"
        f"    patched.{meta['guard']}.model_validate(raw)\n"
        f"    mapped = patched.{meta['map_fn']}(raw, dict(INPUT), PREV)\n"
        f"    original.{meta['output_model']}.model_validate(mapped)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_path), "-q"],
        capture_output=True,
        text=True,
        cwd=Path.cwd(),
    )
    summary = (proc.stdout + proc.stderr).strip().splitlines()[-2:]
    results = "\n".join(summary) if summary else ""
    if proc.returncode != 0:
        raise RepairError(f"repair test failed:\n{results}")
    return results
