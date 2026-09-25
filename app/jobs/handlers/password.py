from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.secrets import put_secret
from app.db.models import Job, Server
from app.jobs.notify import enqueue_notify
from app.jobs.results import Done, Fail, Review
from app.logging import get_logger

log = get_logger("jobs.password")


async def handle(session: AsyncSession, redis, partner, job: Job):
    server = await session.get(Server, job.server_id or (job.payload or {}).get("server_id"))
    if server is None or not server.partner_id:
        return Fail(reason="missing")
    try:
        pwd = await partner.reset_password(
            server.partner_id, None, job.idem_key or f"job-{job.id}"
        )
    except Exception as e:
        return Review(reason=str(e))
    if not pwd:
        return Review(reason="password_missing")
    await put_secret(
        redis,
        f"cred:{server.id}",
        {"login": server.login or "root", "password": pwd},
        ttl=2_592_000,
    )
    await redis.delete(f"cred:fetch:{server.id}")

    # Auto-update the open server card with the real password (no user click)
    silent = bool((job.payload or {}).get("silent"))
    try:
        from aiogram import Bot

        from app.bot.panel_card import refresh_server_card_password
        from app.config import get_settings

        settings = get_settings()
        if settings.bot_token:
            bot = Bot(token=settings.bot_token)
            try:
                await refresh_server_card_password(session, redis, bot, server)
            finally:
                await bot.session.close()
    except Exception:
        log.exception("panel_card_refresh_failed", server_id=server.id)

    # One-time DM with credentials (deduped by key+ref) — skip spam on silent backfill
    if not silent:
        await enqueue_notify(
            session,
            user_id=server.user_id,
            key="creds_password",
            ref=str(server.id),
            text="creds_password",
            payload={"server_id": server.id},
        )
    return Done()
