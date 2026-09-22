from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Notification, User
from app.logging import get_logger

log = get_logger("jobs.notify")

# In-memory queue flushed by worker notify loop
_PENDING: list[dict[str, Any]] = []


async def enqueue_notify(
    session: AsyncSession,
    *,
    user_id: int | None,
    key: str,
    ref: str,
    text: str,
    admin: bool = False,
    payload: dict | None = None,
) -> bool:
    if user_id is not None:
        existing = await session.scalar(
            select(Notification).where(
                Notification.user_id == user_id,
                Notification.key == key,
                Notification.ref == ref,
            )
        )
        if existing:
            return False
        session.add(Notification(user_id=user_id, key=key, ref=ref))
    _PENDING.append(
        {
            "user_id": user_id,
            "key": key,
            "ref": ref,
            "text": text,
            "admin": admin,
            "payload": payload or {},
        }
    )
    return True


def drain_pending() -> list[dict[str, Any]]:
    items = list(_PENDING)
    _PENDING.clear()
    return items


async def deliver(bot, redis, settings, items: list[dict[str, Any]], session: AsyncSession) -> None:
    from app.bot.texts import t
    from app.core.secrets import get_secret

    for item in items:
        try:
            if item.get("admin"):
                if settings.admin_chat_id:
                    await bot.send_message(settings.admin_chat_id, item["text"])
                continue
            user = await session.get(User, item["user_id"]) if item.get("user_id") else None
            if user is None or user.bot_blocked:
                continue
            key = item["key"]
            if key in {"creds", "creds_password"}:
                server_id = item["payload"].get("server_id")
                secret = await get_secret(redis, f"cred:{server_id}")
                if not secret:
                    continue
                text = t(
                    "creds" if key == "creds" else "creds_password",
                    lang=user.lang,
                    login=secret.get("login", "root"),
                    password=secret.get("password", ""),
                    server_id=server_id,
                )
                msg = await bot.send_message(
                    user.tg_id, text, parse_mode="HTML", protect_content=True
                )
                # schedule delete in 10 min
                import time

                await redis.zadd("tg:delete_queue", {f"{user.tg_id}:{msg.message_id}": time.time() + 600})
            else:
                await bot.send_message(user.tg_id, item["text"], parse_mode="HTML")
        except Exception:
            log.exception("notify_failed", key=item.get("key"))
