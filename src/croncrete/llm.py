"""Single gateway for every model call.

Assumes OpenAI-compatible `POST {base_url}/chat/completions` for both
providers; verified by the M0 live calls. Roles select model + credentials +
pricing. Record/replay via cassettes keeps committed tests keyless.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

Role = str  # agent | compiler | repair | judge

TIMEOUT_S = 60.0

# Keys that must never enter a model message (prompt stability).
STRIP_KEYS = frozenset(
    {
        "run_id",
        "latency_ms",
        "cost_usd",
        "tokens_in",
        "tokens_out",
        "tokens_cached",
        "cached_in",
        "timestamp",
        "created",
        "started_at",
        "ended_at",
    }
)


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = {}


class LLMResult(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = []
    tokens_in: int = 0
    tokens_out: int = 0
    cached_in: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0


class LLMError(RuntimeError):
    """Raised when a role's call fails after retries; names the role."""


class CassetteMiss(RuntimeError):
    """Replay mode with no matching cassette; never falls back to network."""


class RoleConfig(BaseModel):
    model: str
    base_url_env: str
    api_key_env: str
    price_in: float  # USD per million tokens
    price_cached: float
    price_out: float


def _open_price(env: str, default: float) -> float:
    try:
        return float(os.environ.get(env, default))
    except ValueError:
        return default


def get_role_config(role: Role) -> RoleConfig:
    """Per-role model, credentials, and pricing."""
    if role in ("agent", "compiler"):
        return RoleConfig(
            model="muse-spark-1.2",
            base_url_env="META_BASE_URL",
            api_key_env="META_API_KEY",
            price_in=1.25,
            price_cached=0.15,
            price_out=4.25,
        )
    if role in ("repair", "judge"):
        return RoleConfig(
            model=os.environ.get("OPEN_MODEL", "meta-models/muse-spark-open-1026"),
            base_url_env="OPEN_BASE_URL",
            api_key_env="OPEN_API_KEY",
            price_in=_open_price("OPEN_PRICE_IN", 0.0),
            price_cached=_open_price("OPEN_PRICE_CACHED", 0.0),
            price_out=_open_price("OPEN_PRICE_OUT", 0.0),
        )
    raise LLMError(f"unknown role: {role!r}")


def strip_for_prompt(obj: Any) -> Any:
    """Remove run-varying keys so prompts (and cassette keys) stay stable."""
    if isinstance(obj, dict):
        return {k: strip_for_prompt(v) for k, v in obj.items() if k not in STRIP_KEYS}
    if isinstance(obj, list):
        return [strip_for_prompt(v) for v in obj]
    return obj


def cassette_dir() -> Path:
    return Path(os.environ.get("CRONCRETE_CASSETTES", "examples/support_triage/cassettes"))


def _schema_key(response_schema: Any) -> Any:
    if response_schema is None:
        return None
    if isinstance(response_schema, type) and issubclass(response_schema, BaseModel):
        return {"name": response_schema.__name__, "schema": response_schema.model_json_schema()}
    return str(response_schema)


def cassette_key(role: Role, messages: list, tools: Any, response_schema: Any) -> str:
    canonical = json.dumps(
        {
            "role": role,
            "messages": messages,
            "tools": tools,
            "response_schema": _schema_key(response_schema),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


RecorderHook = Callable[[str, int, int, int], None]
_recorder_hook: RecorderHook | None = None


def set_recorder_hook(hook: RecorderHook | None) -> None:
    """Tracing (M1) registers here so live calls append `llm_calls`."""
    global _recorder_hook
    _recorder_hook = hook


def _cost(cfg: RoleConfig, tokens_in: int, cached_in: int, tokens_out: int) -> float:
    fresh_in = max(0, tokens_in - cached_in)
    return (
        fresh_in * cfg.price_in + cached_in * cfg.price_cached + tokens_out * cfg.price_out
    ) / 1_000_000


def _extract_text(message: dict[str, Any]) -> str:
    # Spark Open may return a reasoning channel beside the payload; use content only.
    content = message.get("content", "")
    if isinstance(content, list):  # some providers segment content blocks
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") != "reasoning"
        ]
        return "".join(parts)
    return content if isinstance(content, str) else ""


def _extract_tool_calls(message: dict[str, Any]) -> list[ToolCall]:
    calls = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function", {})
        raw_args = fn.get("arguments", "{}")
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except (json.JSONDecodeError, TypeError):
            args = {}
        calls.append(ToolCall(name=fn.get("name", ""), arguments=args))
    return calls


def _live_call(
    cfg: RoleConfig,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    response_schema: Any,
) -> dict[str, Any]:
    base_url = os.environ.get(cfg.base_url_env, "").rstrip("/")
    api_key = os.environ.get(cfg.api_key_env, "")
    if not base_url or not api_key:
        raise LLMError(f"missing {cfg.base_url_env} or {cfg.api_key_env}")
    payload: dict[str, Any] = {"model": model, "messages": messages}
    if tools:
        payload["tools"] = tools
    if response_schema is not None:
        payload["response_format"] = {"type": "json_object"}
    last_exc: Exception | None = None
    for _ in range(2):  # try + one retry on 429/5xx
        try:
            t0 = time.monotonic()
            resp = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=TIMEOUT_S,
            )
            latency_ms = int((time.monotonic() - t0) * 1000)
            if resp.status_code in (429,) or resp.status_code >= 500:
                last_exc = LLMError(f"HTTP {resp.status_code}")
                continue
            resp.raise_for_status()
            data = resp.json()
            data["_latency_ms"] = latency_ms
            return data
        except httpx.HTTPError as e:
            last_exc = e
            break
    raise LLMError(f"request failed: {last_exc}")


def _validate_schema(response_schema: Any, text: str) -> None:
    if isinstance(response_schema, type) and issubclass(response_schema, BaseModel):
        response_schema.model_validate_json(text)
    elif isinstance(response_schema, type) and issubclass(response_schema, Enum):
        payload = json.loads(text)
        if isinstance(payload, dict):
            response_schema(payload["priority"])  # reasoning channel ignored
        else:
            response_schema(payload)
    else:
        raise LLMError(f"unsupported response_schema: {response_schema!r}")


def _parse_result(data: dict[str, Any], cfg: RoleConfig) -> LLMResult:
    choices = data.get("choices") or [{}]
    message = choices[0].get("message", {})
    usage = data.get("usage", {})
    tokens_in = int(usage.get("prompt_tokens", 0))
    tokens_out = int(usage.get("completion_tokens", 0))
    cached_in = int(usage.get("cached_tokens", usage.get("prompt_cache_hit_tokens", 0) or 0))
    latency_ms = int(data.get("_latency_ms", 0))
    return LLMResult(
        text=_extract_text(message),
        tool_calls=_extract_tool_calls(message),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cached_in=cached_in,
        latency_ms=latency_ms,
        cost_usd=_cost(cfg, tokens_in, cached_in, tokens_out),
    )


def chat(
    role: Role,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    response_schema: type[BaseModel] | None = None,
) -> LLMResult:
    """Call `role`'s model. Replay serves cassettes; record saves them."""
    cfg = get_role_config(role)
    clean_messages = strip_for_prompt(messages)
    key = cassette_key(role, clean_messages, tools, response_schema)
    cpath = cassette_dir() / f"{key}.json"

    if os.environ.get("CRONCRETE_REPLAY") == "1":
        if not cpath.exists():
            raise CassetteMiss(f"no cassette for role={role} key={key}")
        result = LLMResult(**json.loads(cpath.read_text())["response"])
        if _recorder_hook is not None:
            _recorder_hook(role, result.tokens_in, result.tokens_out, result.latency_ms)
        return result

    if response_schema is None:
        data = _live_call(cfg, cfg.model, clean_messages, tools, None)
        result = _parse_result(data, cfg)
    else:
        # Schema miss retries once with the validation error attached.
        attempt_messages = list(clean_messages)
        last_err: Exception | None = None
        result = None
        for attempt in range(2):
            data = _live_call(cfg, cfg.model, attempt_messages, tools, response_schema)
            parsed = _parse_result(data, cfg)
            try:
                _validate_schema(response_schema, parsed.text)
            except Exception as e:
                last_err = e
                attempt_messages = [
                    *clean_messages,
                    {"role": "user", "content": f"Validation error, fix the JSON: {e}"},
                ]
                if attempt == 1:
                    raise LLMError(f"role={role} schema miss: {e}") from e
                continue
            result = parsed
            break
        if result is None:
            raise LLMError(f"role={role} schema miss: {last_err}")

    if _recorder_hook is not None:
        _recorder_hook(role, result.tokens_in, result.tokens_out, result.latency_ms)
    if os.environ.get("CRONCRETE_RECORD") == "1":
        cassette_dir().mkdir(parents=True, exist_ok=True)
        cpath.write_text(
            json.dumps(
                {
                    "request": {
                        "role": role,
                        "messages": clean_messages,
                        "tools": tools,
                        "response_schema": _schema_key(response_schema),
                    },
                    "response": result.model_dump(),
                },
                indent=2,
                sort_keys=True,
            )
        )
    return result
