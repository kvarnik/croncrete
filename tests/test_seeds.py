"""Seed data covers tiers and urgency; every sender resolves."""

import json
from pathlib import Path

SEED = json.loads(
    (Path(__file__).resolve().parents[1] / "examples" / "support_triage" / "seed.json").read_text()
)


def test_ten_emails_all_senders_resolve():
    emails = SEED["emails"]
    assert len(emails) == 10
    by_email = {c["email"]: c for c in SEED["customers"]}
    for e in emails:
        assert set(e) == {"id", "sender", "subject", "body"}
        assert e["sender"] in by_email


def test_tiers_and_urgency_covered():
    by_email = {c["email"]: c for c in SEED["customers"]}
    tiers = {by_email[e["sender"]]["tier"] for e in SEED["emails"]}
    assert tiers == {"enterprise", "pro", "free"}
    subjects = " ".join(e["subject"].lower() for e in SEED["emails"])
    assert "urgent" in subjects
    assert any(w in subjects for w in ("question", "request", "how", "update"))
