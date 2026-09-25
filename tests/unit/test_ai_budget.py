"""Unit tests for AI weekly token budget."""

from __future__ import annotations

from datetime import datetime, timezone

from app.agent.budget import (
    BUDGET_SENTINEL,
    next_monday_utc,
    reset_label,
    tokens_from_response,
    weekly_token_cap,
)


def test_budget_sentinel():
    assert BUDGET_SENTINEL == "AI_BUDGET"


def test_weekly_cap_default():
    assert weekly_token_cap() >= 10_000


def test_reset_label_ru():
    assert "Понедельник" in reset_label("ru")
    assert "Monday" in reset_label("en")


def test_next_monday_is_monday():
    now = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)  # Friday
    nxt = next_monday_utc(now)
    assert nxt.weekday() == 0
    assert nxt.hour == 0
    assert nxt > now


def test_tokens_from_response_fallback():
    class R:
        pass

    n = tokens_from_response(R(), prompt_chars=4000)
    assert n >= 100


def test_tokens_from_usage_meta():
    class Meta:
        prompt_token_count = 1000
        candidates_token_count = 200

    class R:
        usage_metadata = Meta()

    assert tokens_from_response(R()) == 1200
