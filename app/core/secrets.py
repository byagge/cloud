from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet
from redis.asyncio import Redis

from app.config import get_settings


def _fernet() -> Fernet:
    settings = get_settings()
    return Fernet(settings.ensure_fernet().encode())


async def put_secret(redis: Redis, key: str, payload: dict[str, Any], ttl: int = 900) -> None:
    token = _fernet().encrypt(json.dumps(payload).encode()).decode()
    await redis.setex(key, ttl, token)


async def get_secret(redis: Redis, key: str) -> dict[str, Any] | None:
    raw = await redis.get(key)
    if not raw:
        return None
    data = _fernet().decrypt(raw.encode() if isinstance(raw, str) else raw)
    return json.loads(data)
