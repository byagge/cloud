"""Per-user weekly AI token caps (Redis). Protects provider bill from one client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.settings_store import settings_store
from app.logging import get_logger

log = get_logger("agent.budget")

BUDGET_SENTINEL = "AI_BUDGET"

# Fallback when API usage missing: ~4 chars / token
_CHARS_PER_TOKEN = 4.0

# Default weekly token budget (~10–20 typical economized sessions)
_DEFAULT_WEEKLY_TOKENS = 400_000


@dataclass
class BudgetStatus:
    ok: bool
    reason: str = ""  # week_tokens | user_busy
    used_tokens: int = 0
    weekly_cap: int = 0
    reset_label: str = ""  # e.g. "Понедельник 00:00"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _week_id(now: datetime | None = None) -> str:
    """ISO week id (Monday-based), UTC."""
    now = now or _now_utc()
    return now.strftime("%G-W%V")


def next_monday_utc(now: datetime | None = None) -> datetime:
    """Next Monday 00:00 UTC (if currently Monday after midnight → next week)."""
    now = now or _now_utc()
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # weekday: Mon=0 … Sun=6
    if now.weekday() == 0 and now == start_today:
        return start_today
    days = 7 - now.weekday()  # days until next Monday
    if now.weekday() == 0:
        days = 7
    return start_today + timedelta(days=days)


def seconds_until_week_reset(now: datetime | None = None) -> int:
    now = now or _now_utc()
    delta = next_monday_utc(now) - now
    return max(60, int(delta.total_seconds()))


def reset_label(lang: str = "ru") -> str:
    if lang == "ru":
        return "Понедельник 00:00"
    return "Monday 00:00"


def weekly_token_cap() -> int:
    try:
        raw = settings_store.get("ai_weekly_token_cap", _DEFAULT_WEEKLY_TOKENS)
        return max(10_000, int(raw or _DEFAULT_WEEKLY_TOKENS))
    except Exception:
        return _DEFAULT_WEEKLY_TOKENS


def _tokens_key(user_id: int) -> str:
    return f"ai:tokens:{user_id}:{_week_id()}"


def _user_lock_key(user_id: int) -> str:
    return f"ai:user_lock:{user_id}"


def _beta_ack_key(user_id: int) -> str:
    return f"ai:beta_ack:{user_id}"


def tokens_from_response(response: Any, *, prompt_chars: int = 0) -> int:
    """Total tokens (prompt+output) from provider usage, or char estimate."""
    meta = getattr(response, "usage_metadata", None) or getattr(response, "usage", None)
    if meta is not None:
        prompt_t = (
            getattr(meta, "prompt_token_count", None)
            or getattr(meta, "input_tokens", None)
            or getattr(meta, "prompt_tokens", None)
            or 0
        )
        out_t = (
            getattr(meta, "candidates_token_count", None)
            or getattr(meta, "output_tokens", None)
            or getattr(meta, "completion_tokens", None)
            or 0
        )
        try:
            total = int(prompt_t or 0) + int(out_t or 0)
            if total > 0:
                return total
        except Exception:
            pass
    # rough fallback
    est = int(prompt_chars / _CHARS_PER_TOKEN) + 400
    return max(100, est)


async def get_used_tokens(redis, user_id: int) -> int:
    raw = await redis.get(_tokens_key(user_id))
    try:
        return int(raw) if raw is not None else 0
    except Exception:
        return 0


async def check_can_start(redis, user_id: int) -> BudgetStatus:
    cap = weekly_token_cap()
    used = await get_used_tokens(redis, user_id)
    label = reset_label("en")
    if await redis.get(_user_lock_key(user_id)):
        return BudgetStatus(
            ok=False,
            reason="user_busy",
            used_tokens=used,
            weekly_cap=cap,
            reset_label=label,
        )
    if used >= cap:
        return BudgetStatus(
            ok=False,
            reason="week_tokens",
            used_tokens=used,
            weekly_cap=cap,
            reset_label=label,
        )
    return BudgetStatus(
        ok=True,
        used_tokens=used,
        weekly_cap=cap,
        reset_label=label,
    )


async def check_run_budget(redis, user_id: int, job_id: int) -> BudgetStatus:
    """Mid-run: weekly token cap only (user already holds the lock)."""
    _ = job_id
    return await _check_tokens_only(redis, user_id)


async def _check_tokens_only(redis, user_id: int) -> BudgetStatus:
    cap = weekly_token_cap()
    used = await get_used_tokens(redis, user_id)
    label = reset_label("en")
    if used >= cap:
        return BudgetStatus(
            ok=False,
            reason="week_tokens",
            used_tokens=used,
            weekly_cap=cap,
            reset_label=label,
        )
    return BudgetStatus(ok=True, used_tokens=used, weekly_cap=cap, reset_label=label)


async def acquire_user_lock(redis, user_id: int, job_id: int, *, ttl: int = 7200) -> bool:
    return bool(await redis.set(_user_lock_key(user_id), str(job_id), nx=True, ex=ttl))


async def release_user_lock(redis, user_id: int, job_id: int) -> None:
    try:
        cur = await redis.get(_user_lock_key(user_id))
        if cur is not None and str(cur) == str(job_id):
            await redis.delete(_user_lock_key(user_id))
    except Exception:
        log.exception("ai_user_lock_release_failed", user_id=user_id)


async def add_tokens(
    redis,
    user_id: int,
    *,
    tokens: int,
) -> BudgetStatus:
    """Record token usage; return whether weekly cap is now exceeded."""
    cap = weekly_token_cap()
    tokens = max(0, int(tokens))
    key = _tokens_key(user_id)
    ttl = seconds_until_week_reset() + 3600
    try:
        if tokens > 0:
            # incr by tokens; fallback get/set for memory redis without incrby
            try:
                used = await redis.incrby(key, tokens)
            except Exception:
                cur = await redis.get(key)
                used = (int(cur) if cur is not None else 0) + tokens
                await redis.set(key, str(used), ex=ttl)
            else:
                if used == tokens:
                    await redis.expire(key, ttl)
        else:
            used = await get_used_tokens(redis, user_id)
    except Exception:
        log.exception("ai_tokens_add_failed", user_id=user_id)
        used = tokens

    label = reset_label("en")
    over = used >= cap
    return BudgetStatus(
        ok=not over,
        reason="week_tokens" if over else "",
        used_tokens=used,
        weekly_cap=cap,
        reset_label=label,
    )


async def has_beta_ack(redis, user_id: int) -> bool:
    return bool(await redis.get(_beta_ack_key(user_id)))


async def set_beta_ack(redis, user_id: int) -> None:
    # Keep forever (1 year) — one-time disclaimer
    await redis.set(_beta_ack_key(user_id), "1", ex=365 * 24 * 3600)
