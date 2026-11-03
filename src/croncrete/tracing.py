"""Recording: wrap tool calls, capture llm usage, write one trace JSON per run.

Filename is `{input[id]}.json` when the input carries an id (the support
example does), else `{run_id}.json`. Re-recording an email overwrites its
trace, which makes `make traces` resumable by email id.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from types import TracebackType
from typing import Any

from pydantic import BaseModel

from croncrete import llm


class StepRecord(BaseModel):
    index: int
    tool: str
    args: dict[str, Any]
    result: dict[str, Any]
    latency_ms: int


class LLMCallRecord(BaseModel):
    role: str
    tokens_in: int
    tokens_out: int
    latency_ms: int


class TotalsRecord(BaseModel):
    steps: int
    latency_ms: int
    tokens_in: int
    tokens_out: int
    cost_usd: float


class TraceRecord(BaseModel):
    run_id: str
    input: dict[str, Any]
    steps: list[StepRecord]
    llm_calls: list[LLMCallRecord]
    output: dict[str, Any] | None
    totals: TotalsRecord
    flagged: bool = False
    flag_reason: str | None = None


def _pricing_for(role: str) -> tuple[float, float]:
    cfg = llm.get_role_config(role)
    return cfg.price_in, cfg.price_out


class Run:
    def __init__(self, traces_dir: Path, input: dict[str, Any]) -> None:
        self._traces_dir = traces_dir
        self.input = input
        self.run_id = uuid.uuid4().hex[:12]
        self._steps: list[StepRecord] = []
        self._llm_calls: list[LLMCallRecord] = []
        self._output: dict[str, Any] | None = None

    def _hook(self, role: str, tokens_in: int, tokens_out: int, latency_ms: int) -> None:
        self._llm_calls.append(
            LLMCallRecord(
                role=role, tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency_ms
            )
        )

    def tool(self, name: str, fn: Any, **kwargs: Any) -> dict[str, Any]:
        t0 = time.monotonic()
        result = fn(**kwargs)
        latency_ms = int((time.monotonic() - t0) * 1000)
        self._steps.append(
            StepRecord(
                index=len(self._steps), tool=name, args=kwargs, result=result, latency_ms=latency_ms
            )
        )
        return result

    def output(self, result: dict[str, Any] | None) -> None:
        self._output = result

    def close(self) -> TraceRecord:
        tokens_in = sum(c.tokens_in for c in self._llm_calls)
        tokens_out = sum(c.tokens_out for c in self._llm_calls)
        cost_usd = 0.0
        for c in self._llm_calls:
            price_in, price_out = _pricing_for(c.role)
            cost_usd += (c.tokens_in * price_in + c.tokens_out * price_out) / 1_000_000
        trace = TraceRecord(
            run_id=self.run_id,
            input=self.input,
            steps=self._steps,
            llm_calls=self._llm_calls,
            output=self._output,
            totals=TotalsRecord(
                steps=len(self._steps),
                latency_ms=sum(s.latency_ms for s in self._steps)
                + sum(c.latency_ms for c in self._llm_calls),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost_usd,
            ),
        )
        self._traces_dir.mkdir(parents=True, exist_ok=True)
        name = self.input.get("id", self.run_id)
        (self._traces_dir / f"{name}.json").write_text(trace.model_dump_json(indent=2))
        return trace


class _RunContext:
    def __init__(self, traces_dir: Path, input: dict[str, Any]) -> None:
        self._run = Run(traces_dir, input)

    def __enter__(self) -> Run:
        llm.set_recorder_hook(self._run._hook)
        return self._run

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        llm.set_recorder_hook(None)
        self._run.close()


class Recorder:
    def __init__(self, traces_dir: str) -> None:
        self._traces_dir = Path(traces_dir)

    def run(self, input: dict[str, Any]) -> _RunContext:
        return _RunContext(self._traces_dir, input)

    @staticmethod
    def load(path: str | Path) -> TraceRecord:
        return TraceRecord.model_validate_json(Path(path).read_text())
