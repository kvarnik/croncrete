"""Generated judge parser accepts bare, quoted, and object verdicts."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples" / "support_triage" / "workflows"))
sys.path.insert(0, str(ROOT / "src"))

import triage  # noqa: E402

from croncrete import Priority  # noqa: E402


def test_priority_from_text_shapes():
    assert triage._priority_from_text("P1") == Priority.P1
    assert triage._priority_from_text('"P2"') == Priority.P2
    assert triage._priority_from_text('{"priority": "P3", "reasoning": "x"}') == Priority.P3
