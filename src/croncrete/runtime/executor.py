"""Executor: run a compiled workflow with sqlite checkpoints.

Step order per step: build args, tools.call, guard(raw), map, output(mapped),
checkpoint. A failed guard halts with expected + observed shapes. End checks
run after the last step; a validate failure halts (failed_step None) and is
never repaired. Workflow source passes the fence before every import.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from pydantic import ValidationError

from croncrete.compiler import checks

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, workflow TEXT, input_json TEXT,
    status TEXT, output_json TEXT, failed_step INTEGER, error_json TEXT);
CREATE TABLE IF NOT EXISTS step_runs (
    run_id TEXT, step_index INTEGER, attempt INTEGER, tool TEXT,
    args_json TEXT, result_json TEXT, status TEXT, latency_ms INTEGER,
    PRIMARY KEY (run_id, step_index, attempt));
CREATE TABLE IF NOT EXISTS calls (
    run_id TEXT, step_index INTEGER, attempt INTEGER, endpoint TEXT,
    args_json TEXT, response_json TEXT, latency_ms INTEGER);
"""


@dataclass
class RunResult:
    run_id: str
    status: str  # completed | halted
    output: dict[str, Any] | None
    failed_step: int | None
    error: dict[str, Any] | None
    workflow: Path


class _Halt(Exception):
    def __init__(self, result: RunResult) -> None:
        self.result = result
        super().__init__(result.status)


def load_workflow(path: str | Path) -> ModuleType:
    """Fence-check then import a workflow module under a unique name."""
    wf_path = Path(path)
    checks.check_source(wf_path.read_text())
    name = f"{wf_path.stem}_{uuid.uuid4().hex[:8]}"
    spec = importlib.util.spec_from_file_location(name, wf_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {wf_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _connect(state_db: str | Path) -> sqlite3.Connection:
    db = Path(state_db)
    if str(db) != ":memory:":
        db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    return con


def _dump(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str)


def run(
    workflow: str | Path,
    input: dict[str, Any],
    tools: Any,
    state_db: str | Path = ".croncrete/state.db",
) -> RunResult:
    wf_path = Path(workflow)
    mod = load_workflow(wf_path)
    run_id = uuid.uuid4().hex[:12]
    con = _connect(state_db)
    try:
        con.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, str(wf_path), _dump(input), "running", None, None, None),
        )
        con.commit()
        return _execute(con, mod, wf_path, run_id, dict(input), tools)
    except _Halt as h:
        return h.result
    except Exception as e:  # record-then-raise boundary
        con.execute(
            "UPDATE runs SET status=?, error_json=? WHERE run_id=?",
            ("halted", _dump({"stage": "tool", "message": str(e)}), run_id),
        )
        con.commit()
        raise
    finally:
        con.close()


def _record_halt(
    con: sqlite3.Connection,
    run_id: str,
    wf_path: Path,
    failed_step: int | None,
    error: dict[str, Any],
) -> RunResult:
    con.execute(
        "UPDATE runs SET status=?, failed_step=?, error_json=? WHERE run_id=?",
        ("halted", failed_step, _dump(error), run_id),
    )
    con.commit()
    return RunResult(
        run_id=run_id,
        status="halted",
        output=None,
        failed_step=failed_step,
        error=error,
        workflow=wf_path,
    )


def _execute(
    con: sqlite3.Connection,
    mod: ModuleType,
    wf_path: Path,
    run_id: str,
    input: dict[str, Any],
    tools: Any,
) -> RunResult:
    prev: dict[str, dict[str, Any]] = {}
    step_args: list[dict[str, Any]] = []
    priority: Any = None
    for i, meta in enumerate(mod.STEPS):
        args = mod.build_args(i, dict(input), prev, run_id, priority=priority)
        if "priority" in args:
            priority = args["priority"]
        step_args.append(dict(args))
        t0 = time.monotonic()
        raw = tools.call(meta["endpoint"], **args)
        latency_ms = int((time.monotonic() - t0) * 1000)
        con.execute(
            "INSERT INTO calls VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, i, 0, meta["endpoint"], _dump(args), _dump(raw), latency_ms),
        )
        guard = getattr(mod, meta["guard"])
        try:
            guard.model_validate(raw)
        except ValidationError as e:
            con.execute(
                "INSERT INTO step_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, i, 0, meta["tool"], _dump(args), _dump(raw), "halted", latency_ms),
            )
            raise _Halt(
                _record_halt(
                    con,
                    run_id,
                    wf_path,
                    i,
                    {
                        "stage": "guard",
                        "tool": meta["tool"],
                        "expected": guard.model_json_schema(),
                        "observed": raw,
                    },
                )
            ) from e
        mapped = getattr(mod, meta["map_fn"])(dict(raw), dict(input), prev)
        out_cls = getattr(mod, meta["output_model"])
        try:
            out_cls.model_validate(mapped)
        except ValidationError as e:
            con.execute(
                "INSERT INTO step_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, i, 0, meta["tool"], _dump(args), _dump(mapped), "halted", latency_ms),
            )
            raise _Halt(
                _record_halt(
                    con,
                    run_id,
                    wf_path,
                    i,
                    {"stage": "output", "tool": meta["tool"], "message": str(e)},
                )
            ) from e
        prev[meta["tool"]] = dict(mapped)
        con.execute(
            "INSERT INTO step_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, i, 0, meta["tool"], _dump(args), _dump(mapped), "completed", latency_ms),
        )
        con.commit()

    to = next((a["to"] for a in reversed(step_args) if "to" in a), None)
    try:
        mod.validate_run(priority, to, input["sender"])
    except ValueError as e:
        raise _Halt(
            _record_halt(con, run_id, wf_path, None, {"stage": "validate", "message": str(e)})
        ) from e

    ticket_id = next((m["ticket_id"] for m in prev.values() if "ticket_id" in m), None)
    last_send = next((a for a in reversed(step_args) if "to" in a), {})
    output = {
        "ticket_id": ticket_id,
        "priority": str(priority),
        "to": to,
        "subject": last_send.get("subject"),
        "body": last_send.get("body"),
    }
    con.execute(
        "UPDATE runs SET status=?, output_json=? WHERE run_id=?",
        ("completed", _dump(output), run_id),
    )
    con.commit()
    return RunResult(
        run_id=run_id,
        status="completed",
        output=output,
        failed_step=None,
        error=None,
        workflow=wf_path,
    )
