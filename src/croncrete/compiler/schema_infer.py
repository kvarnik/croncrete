"""Guard + output-model inference from recorded responses.

Guards: every observed field becomes a typed member; required means present
in every response; extra fields forbidden; no enums or ranges. Output models
cover exactly the fields downstream steps, the judge, or the template read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FieldDef:
    name: str
    type: str
    required: bool


@dataclass
class ModelDef:
    name: str
    fields: list[FieldDef] = field(default_factory=list)
    nested: list[ModelDef] = field(default_factory=list)


def camel(*parts: str) -> str:
    out = ""
    for p in parts:
        for w in p.replace("-", "_").split("_"):
            out += w.capitalize()
    return out


def _scalar_type(key: str, values: list[Any]) -> str:
    nonnull = [v for v in values if v is not None]
    if not nonnull:
        return "Any"
    if any(isinstance(v, dict) for v in nonnull):
        raise ValueError(f"mixed dict/scalar for {key}")
    if any(isinstance(v, list) for v in nonnull):
        raise ValueError(f"lists unsupported for {key}")
    kinds = set()
    for v in nonnull:
        if isinstance(v, bool):
            kinds.add("bool")
        elif isinstance(v, int):
            kinds.add("int")
        elif isinstance(v, float):
            kinds.add("float")
        else:
            kinds.add("str")
    if kinds in ({"float"}, {"int", "float"}):
        return "float"
    if len(kinds) > 1:
        raise ValueError(f"mixed types for {key}: {sorted(kinds)}")
    return kinds.pop()


def infer_guard_model(tool: str, responses: list[dict[str, Any]]) -> ModelDef:
    """Guard over every observed field; required = present in all."""
    return _infer(camel(tool, "guard"), responses)


def _infer(name: str, observations: list[dict[str, Any]]) -> ModelDef:
    model = ModelDef(name=name)
    keys: set[str] = set()
    for o in observations:
        keys.update(o)
    for key in sorted(keys):
        present = [o for o in observations if key in o]
        values = [o[key] for o in present]
        nonnull = [v for v in values if v is not None]
        if nonnull and all(isinstance(v, dict) for v in nonnull):
            sub = _infer(camel(name, key), [v for v in nonnull if isinstance(v, dict)])
            model.nested.append(sub)
            model.fields.append(
                FieldDef(name=key, type=sub.name, required=len(present) == len(observations))
            )
        else:
            model.fields.append(
                FieldDef(
                    name=key,
                    type=_scalar_type(key, values),
                    required=len(present) == len(observations),
                )
            )
    return model


def infer_output_model(tool: str, responses: list[dict[str, Any]], fields: set[str]) -> ModelDef:
    """Output model over read fields only; all required (mapping provides them)."""
    for f in sorted(fields):
        if any(f not in o for o in responses):
            raise ValueError(f"{tool}: read field {f!r} missing in some responses")
    model = ModelDef(name=camel(tool, "out"))
    for key in sorted(fields):
        values = [o[key] for o in responses]
        nonnull = [v for v in values if v is not None]
        if nonnull and all(isinstance(v, dict) for v in nonnull):
            raise ValueError(f"{tool}: nested output {key!r} unsupported in MVP")
        model.fields.append(FieldDef(name=key, type=_scalar_type(key, values), required=True))
    return model


def uses_any(model: ModelDef) -> bool:
    return any(f.type == "Any" for f in model.fields) or any(uses_any(n) for n in model.nested)


def render_model(model: ModelDef) -> str:
    parts = [render_model(n) for n in model.nested]
    lines = [f"class {model.name}(BaseModel):", '    model_config = ConfigDict(extra="forbid")']
    if not model.fields:
        lines.append("    pass")
    for f in model.fields:
        if f.required:
            lines.append(f"    {f.name}: {f.type}")
        else:
            lines.append(f"    {f.name}: {f.type} | None = None")
    parts.append("\n".join(lines))
    return "\n\n\n".join(parts)
