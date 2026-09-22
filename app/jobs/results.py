from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass
class Done:
    pass


@dataclass
class Retry:
    after: timedelta
    reason: str
    count_attempt: bool = True


@dataclass
class Wait:
    after: timedelta
    reason: str


@dataclass
class Fail:
    reason: str


@dataclass
class Review:
    reason: str


JobResult = Done | Retry | Wait | Fail | Review

BACKOFF = [5, 15, 45, 120, 300, 600]


def backoff_seconds(attempts: int) -> int:
    idx = min(max(attempts - 1, 0), len(BACKOFF) - 1)
    return BACKOFF[idx]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
