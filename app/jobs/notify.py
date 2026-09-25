from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Notification, User
from app.logging import get_logger

log = get_logger("jobs.notify")

# In-memory queue flushed by worker notify loop
_PENDING: list[dict[str, Any]] = []


def _build_agent_markup(payload: dict[str, Any]):
    """Optional stop/back / plan keyboard for agent notifications."""
    job_id = int(payload.get("agent_job_id") or 0)
    server_id = int(payload.get("server_id") or 0)
    if not job_id and not server_id:
        return None
    lang = payload.get("lang") or "en"
    if payload.get("show_plan"):
        from app.bot.keyboards import agent_plan_kb

        return agent_plan_kb(job_id=job_id, server_id=server_id, lang=lang)
    show_stop = bool(payload.get("show_stop", False))
    from app.bot.keyboards import agent_control_kb

    return agent_control_kb(
        job_id=job_id,
        server_id=server_id,
        lang=lang,
        show_stop=show_stop,
    )


async def _send_now(
    *,
    user_id: int | None,
    key: str,
    text: str,
    admin: bool,
    payload: dict[str, Any],
) -> None:
    """Send via a short-lived Bot session (for long-running jobs that would stall _PENDING)."""
    from aiogram import Bot

    from app.config import get_settings
    from app.db.session import get_session_factory

    settings = get_settings()
    if not (settings.bot_token or "").strip():
        log.warning("immediate_notify_no_token", key=key)
        return

    bot = Bot(token=settings.bot_token)
    factory = get_session_factory()
    try:
        if admin:
            if settings.admin_chat_id:
                await bot.send_message(settings.admin_chat_id, text)
            return
        if user_id is None:
            return
        async with factory() as session:
            user = await session.get(User, user_id)
            if user is None or user.bot_blocked:
                return
            kwargs: dict[str, Any] = {"parse_mode": "HTML"}
            thread_id = payload.get("message_thread_id")
            if thread_id:
                kwargs["message_thread_id"] = int(thread_id)
            markup = _build_agent_markup(payload)
            if markup is not None:
                kwargs["reply_markup"] = markup
            try:
                await bot.send_message(user.tg_id, text, **kwargs)
            except Exception:
                # Retry without topic if thread invalid
                if "message_thread_id" in kwargs:
                    kwargs.pop("message_thread_id", None)
                    await bot.send_message(user.tg_id, text, **kwargs)
                else:
                    raise
    except Exception:
        log.exception("immediate_notify_failed", key=key, payload_keys=list((payload or {}).keys()))
    finally:
        await bot.session.close()


async def enqueue_notify(
    session: AsyncSession,
    *,
    user_id: int | None,
    key: str,
    ref: str,
    text: str,
    admin: bool = False,
    payload: dict | None = None,
    immediate: bool = False,
) -> bool:
    """Queue a Telegram notification.

    When ``immediate=True``, send now via Bot and do NOT append to ``_PENDING``
    (still writes Notification for dedupe). Use for long agent runs so ask/done/fail
    are not stuck until the job finishes.

    Agent payload extras:
      - message_thread_id: forum topic
      - agent_job_id / show_stop / lang: attach Stop keyboard
    """
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
        try:
            await session.flush()
        except Exception:
            pass

    item = {
        "user_id": user_id,
        "key": key,
        "ref": ref,
        "text": text,
        "admin": admin,
        "payload": payload or {},
    }
    if immediate:
        await _send_now(
            user_id=user_id,
            key=key,
            text=text,
            admin=admin,
            payload=payload or {},
        )
        return True

    _PENDING.append(item)
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
            payload = item.get("payload") or {}
            if key in {"creds", "creds_password"}:
                server_id = payload.get("server_id")
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
                import time

                await redis.zadd("tg:delete_queue", {f"{user.tg_id}:{msg.message_id}": time.time() + 600})
            else:
                kwargs: dict[str, Any] = {"parse_mode": "HTML"}
                thread_id = payload.get("message_thread_id")
                if thread_id:
                    kwargs["message_thread_id"] = int(thread_id)
                markup = _build_agent_markup(payload)
                if markup is not None:
                    kwargs["reply_markup"] = markup
                try:
                    await bot.send_message(user.tg_id, item["text"], **kwargs)
                except Exception:
                    if "message_thread_id" in kwargs:
                        kwargs.pop("message_thread_id", None)
                        await bot.send_message(user.tg_id, item["text"], **kwargs)
                    else:
                        raise
        except Exception:
            log.exception("notify_failed", key=item.get("key"))
