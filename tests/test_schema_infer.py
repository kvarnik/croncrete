"""Schema inference: required/optional/nested, drifted shapes fail guards."""

import pytest
from pydantic import BaseModel, ValidationError

from croncrete.compiler import schema_infer


def _exec_model(model):
    ns: dict = {}
    exec(
        "from pydantic import BaseModel, ConfigDict\nfrom typing import Any\n"
        + schema_infer.render_model(model),
        ns,
    )
    return ns[model.name]


def test_guard_required_optional_and_forbid_extra():
    model = schema_infer.infer_guard_model(
        "crm_lookup",
        [
            {"tier": "pro", "open_tickets": 1},
            {"tier": "free", "open_tickets": 0, "nick": "x"},
        ],
    )
    cls = _exec_model(model)
    assert issubclass(cls, BaseModel)
    assert set(cls.model_fields) == {"tier", "open_tickets", "nick"}
    assert cls.model_fields["nick"].is_required() is False
    with pytest.raises(ValidationError):
        cls.model_validate({"tier": "pro", "open_tickets": 1, "bogus": 1})


def test_drifted_response_fails_guard():
    model = schema_infer.infer_guard_model("crm_lookup", [{"customer_id": "c-1", "tier": "pro"}])
    cls = _exec_model(model)
    with pytest.raises(ValidationError):
        cls.model_validate({"customer_id": "c-1", "plan": {"plan_tier": "pro"}})


def test_nested_model_and_output_projection():
    guard = schema_infer.infer_guard_model(
        "crm_lookup", [{"plan": {"plan_tier": "pro", "renewal": "2027-01-01"}}]
    )
    assert guard.nested and guard.fields[0].type == guard.nested[0].name
    out = schema_infer.infer_output_model(
        "crm_lookup", [{"a": "x", "b": 1}, {"a": "y", "b": 2}], {"a"}
    )
    assert [f.name for f in out.fields] == ["a"]
    with pytest.raises(ValueError, match="missing in some"):
        schema_infer.infer_output_model("crm_lookup", [{"a": "x"}, {}], {"a"})


def test_mixed_types_rejected():
    with pytest.raises(ValueError, match="mixed types"):
        schema_infer.infer_guard_model("t", [{"v": "s"}, {"v": 1}])
