"""Croncrete: compile recorded agent traces into checked Python."""

from enum import StrEnum

from croncrete import llm
from croncrete.compiler.codegen import compile
from croncrete.repair.splice import RepairProposal, repair
from croncrete.report import Report, report
from croncrete.runtime.executor import RunResult, resume, run
from croncrete.tracing import Recorder, TraceRecord


class Priority(StrEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


__all__ = [
    "Priority",
    "Recorder",
    "RepairProposal",
    "Report",
    "RunResult",
    "TraceRecord",
    "compile",
    "llm",
    "repair",
    "report",
    "resume",
    "run",
]
