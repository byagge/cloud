from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from redis.asyncio import Redis

from app.config import get_settings
from app.logging import get_logger

log = get_logger("secrets")


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
    try:
        data = _fernet().decrypt(raw.encode() if isinstance(raw, str) else raw)
        out = json.loads(data)
        return out if isinstance(out, dict) else None
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as e:
        # Wrong FERNET_KEY / corrupted payload — treat as missing
        log.warning("secret_decrypt_failed", key=key, err=str(e)[:120])
        return None


async def delete_secret(redis: Redis, key: str) -> None:
    await redis.delete(key)
