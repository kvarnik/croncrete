"""Croncrete: compile recorded agent traces into checked Python."""

from enum import StrEnum

from croncrete import llm
from croncrete.tracing import Recorder, TraceRecord


class Priority(StrEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


__all__ = ["Priority", "Recorder", "TraceRecord", "llm"]
