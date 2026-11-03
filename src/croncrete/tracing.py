"""Recording (M1)."""

from __future__ import annotations

from typing import Any


class Recorder:
    def __init__(self, traces_dir: str) -> None:
        self.traces_dir = traces_dir

    def run(self, input: dict[str, Any]) -> Any:
        raise NotImplementedError
