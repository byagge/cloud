from __future__ import annotations

import time
from typing import Any


class MemoryRedis:
    """Minimal async Redis subset for local ENV=dev without Redis server."""

    def __init__(self) -> None:
        self._kv: dict[str, tuple[str, float | None]] = {}
        self._zsets: dict[str, dict[str, float]] = {}

    def _alive(self, key: str) -> str | None:
        item = self._kv.get(key)
        if not item:
            return None
        val, exp = item
        if exp is not None and time.time() > exp:
            self._kv.pop(key, None)
            return None
        return val

    async def get(self, key: str) -> str | None:
        return self._alive(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        exp = time.time() + ex if ex else None
        self._kv[key] = (str(value), exp)
        return True

    async def setex(self, key: str, ttl: int, value: str) -> bool:
        return await self.set(key, value, ex=ttl)

    async def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if k in self._kv:
                del self._kv[k]
                n += 1
        return n

    async def incr(self, key: str) -> int:
        cur = int(self._alive(key) or 0) + 1
        await self.set(key, str(cur))
        return cur

    async def expire(self, key: str, ttl: int) -> bool:
        val = self._alive(key)
        if val is None:
            return False
        self._kv[key] = (val, time.time() + ttl)
        return True

    async def ping(self) -> bool:
        return True

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        z = self._zsets.setdefault(key, {})
        z.update(mapping)
        return len(mapping)

    async def zrangebyscore(self, key: str, min_s: float, max_s: float) -> list[str]:
        z = self._zsets.get(key, {})
        return [m for m, s in z.items() if min_s <= s <= max_s]

    async def zrem(self, key: str, member: str) -> int:
        z = self._zsets.get(key, {})
        if member in z:
            del z[member]
            return 1
        return 0

    async def aclose(self) -> None:
        return None
