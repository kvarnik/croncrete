"""Secrets stay local: .env ignored, example carries no values, cassettes safe."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_env_file_is_gitignored():
    proc = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=ROOT, capture_output=True)
    assert proc.returncode == 0, ".env must be gitignored"


def test_env_example_has_no_values():
    for line in (ROOT / ".env.example").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name in ("OPEN_MODEL", "OPEN_PRICE_IN", "OPEN_PRICE_CACHED", "OPEN_PRICE_OUT"):
            continue  # model id and prices are not secrets
        assert value.strip() == "", f"{name} must have no value in .env.example"


def test_role_config_holds_env_names_never_values(monkeypatch):
    from croncrete import llm

    monkeypatch.setenv("META_API_KEY", "secret-value-123")
    for role in ("agent", "compiler", "repair", "judge"):
        cfg = llm.get_role_config(role)
        assert "secret-value-123" not in cfg.model_dump_json()
        assert cfg.api_key_env in ("META_API_KEY", "OPEN_API_KEY")
