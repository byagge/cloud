"""Telegram forum-topic shell for AI sessions (optional Threaded Mode)."""

from __future__ import annotations

from app.logging import get_logger

log = get_logger("agent.topics")

TOPIC_TTL = 30 * 86400


async def ensure_agent_topic(
    bot,
    *,
    chat_id: int,
    server_id: int,
    title: str,
    redis,
) -> int | None:
    """Create or reuse a private-chat forum topic for this server AI session.

    Requires BotFather → Threaded Mode enabled. Returns message_thread_id or None
    when topics are unavailable (fallback: main chat + inline controls).
    """
    key = f"agent:topic:{chat_id}:{server_id}"
    raw = await redis.get(key)
    if raw:
        try:
            return int(raw if isinstance(raw, str) else raw.decode())
        except Exception:
            pass
    name = (title or f"Server #{server_id}")[:128]
    try:
        topic = await bot.create_forum_topic(chat_id=chat_id, name=name)
        tid = int(getattr(topic, "message_thread_id", 0) or 0)
        if not tid:
            return None
        await redis.set(key, str(tid), ex=TOPIC_TTL)
        return tid
    except Exception as e:
        # Topics not enabled / old client / no permission — silent fallback
        log.info("agent_topic_unavailable", err=str(e)[:200], chat_id=chat_id)
        return None
