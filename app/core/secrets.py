from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet
from redis.asyncio import Redis

from app.config import get_settings


def _fernet() -> Fernet:
    settings = get_settings()
    return Fernet(settings.ensure_fernet().encode())


async def put_secret(redis: Redis, key: str, payload: dict[str, Any], ttl: int = 2_592_000) -> None:
    """Store Fernet-encrypted secret. Default TTL 30 days (server passwords for SSH/AI)."""
    token = _fernet().encrypt(json.dumps(payload).encode()).decode()
    if ttl and ttl > 0:
        await redis.setex(key, ttl, token)
    else:
        await redis.set(key, token)


async def get_secret(redis: Redis, key: str) -> dict[str, Any] | None:
    raw = await redis.get(key)
    if not raw:
        return None
    data = _fernet().decrypt(raw.encode() if isinstance(raw, str) else raw)
    return json.loads(data)


async def delete_secret(redis: Redis, key: str) -> None:
    await redis.delete(key)
