"""Static checks: clean modules pass, eval/banned imports/signatures fail."""

import pytest

from croncrete.compiler import checks

CLEAN = '''"""Module."""

from pydantic import BaseModel, ConfigDict
from croncrete import Priority, llm


class CrmGuard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier: str


def map_crm_lookup(response, input, prev):
    return {"tier": response["tier"]}


def judge():
    return llm, Priority.P1
'''


def test_clean_module_passes_source_checks():
    checks.check_source(CLEAN)


def test_eval_and_banned_import_rejected():
    bad = CLEAN + '\nimport os\nx = eval("1")\n'
    with pytest.raises(checks.CheckError):
        checks.check_source(bad)
    with pytest.raises(checks.CheckError, match="banned import"):
        checks.check_source(CLEAN + "\nimport subprocess\n")
    with pytest.raises(checks.CheckError, match="banned call"):
        checks.check_source(CLEAN + '\nx = eval("1")\n')


def test_map_signature_enforced():
    with pytest.raises(checks.CheckError, match=r"\(response, input, prev\)"):
        checks.check_source(CLEAN + "\ndef map_x(response):\n    return {}\n")


def test_check_file_runs_ruff(tmp_path):
    target = tmp_path / "mod.py"
    target.write_text(CLEAN)
    checks.check_file(target)  # formats + lints, raises on failure
