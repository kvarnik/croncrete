"""LLM gateway: strip, pricing, cassette key, replay. No network."""

import json

import pytest

from croncrete import llm


def test_strip_removes_run_varying_keys():
    msg = {
        "role": "user",
        "content": "hi",
        "run_id": "abc",
        "latency_ms": 12,
        "cost_usd": 0.1,
        "tokens_in": 5,
        "nested": [{"tokens_out": 3, "keep": 1}],
    }
    clean = llm.strip_for_prompt(msg)
    assert clean == {"role": "user", "content": "hi", "nested": [{"keep": 1}]}


def test_cassette_key_stable_and_role_scoped():
    msgs = [{"role": "user", "content": "ok"}]
    k1 = llm.cassette_key("judge", msgs, None, None)
    k2 = llm.cassette_key("judge", msgs, None, None)
    k3 = llm.cassette_key("repair", msgs, None, None)
    assert k1 == k2
    assert len(k1) == 64
    assert k1 != k3


def test_pricing_standard_tier():
    cfg = llm.get_role_config("agent")
    assert cfg.model == "muse-spark-1.2"
    assert llm._cost(cfg, 1_000_000, 0, 0) == pytest.approx(1.25)
    assert llm._cost(cfg, 1_000_000, 1_000_000, 0) == pytest.approx(0.15)
    assert llm._cost(cfg, 0, 0, 1_000_000) == pytest.approx(4.25)


def test_validate_schema_accepts_enum_and_model():
    from croncrete import Priority
    from croncrete.compiler.codegen import JudgePrompt

    llm._validate_schema(Priority, '"P1"')
    llm._validate_schema(Priority, '{"priority": "P2", "reasoning": "why"}')
    llm._validate_schema(JudgePrompt, '{"judge_prompt": "x"}')
    with pytest.raises(Exception):
        llm._validate_schema(Priority, '"P9"')
    with pytest.raises(Exception):
        llm._validate_schema(Priority, '{"priority": "P9"}')


def test_replay_miss_raises_and_never_networks(tmp_path, monkeypatch):
    monkeypatch.setenv("CRONCRETE_REPLAY", "1")
    monkeypatch.setenv("CRONCRETE_CASSETTES", str(tmp_path))
    with pytest.raises(llm.CassetteMiss):
        llm.chat("judge", [{"role": "user", "content": "no cassette"}])


def test_replay_serves_cassette(tmp_path, monkeypatch):
    msgs = [{"role": "user", "content": "hello"}]
    key = llm.cassette_key("judge", msgs, None, None)
    (tmp_path / f"{key}.json").write_text(
        json.dumps(
            {
                "request": {
                    "role": "judge",
                    "messages": msgs,
                    "tools": None,
                    "response_schema": None,
                },
                "response": {
                    "text": "P1",
                    "tool_calls": [],
                    "tokens_in": 10,
                    "tokens_out": 2,
                    "cached_in": 0,
                    "latency_ms": 50,
                    "cost_usd": 0.0,
                },
            }
        )
    )
    monkeypatch.setenv("CRONCRETE_REPLAY", "1")
    monkeypatch.setenv("CRONCRETE_CASSETTES", str(tmp_path))
    result = llm.chat("judge", msgs)
    assert result.text == "P1"
    assert result.latency_ms == 50
