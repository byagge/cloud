from __future__ import annotations

import asyncio
import time

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from redis.asyncio import from_url as redis_from_url

from app.config import get_settings
from app.core.settings_store import settings_store
from app.db.session import get_session_factory, session_scope
from app.infra.memory_redis import MemoryRedis
from app.jobs.dispatcher import run_one, try_acquire_leader
from app.jobs.notify import deliver, drain_pending
from app.jobs.scheduled import tasks
from app.logging import get_logger, setup_logging
from app.partner import set_partner
from app.partner.fake import FakePartner
from pathlib import Path

log = get_logger("worker")


async def _process_delete_queue(bot: Bot, redis) -> None:
    now = time.time()
    items = await redis.zrangebyscore("tg:delete_queue", 0, now)
    for item in items:
        raw = item.decode() if isinstance(item, bytes) else item
        try:
            chat_id, msg_id = raw.split(":")
            await bot.delete_message(int(chat_id), int(msg_id))
        except Exception:
            pass
        await redis.zrem("tg:delete_queue", item)


async def main_async() -> None:
    Path("data").mkdir(exist_ok=True)
    setup_logging(json_logs=True)
    settings = get_settings()
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN required for worker notifications")

    if settings.redis_url.startswith("memory"):
        redis = MemoryRedis()
    else:
        redis = redis_from_url(settings.redis_url, decode_responses=True)
    bot = Bot(token=settings.bot_token)
    factory = get_session_factory()

    if settings.use_fake_partner:
        set_partner(FakePartner())

    async with session_scope() as session:
        ok = await try_acquire_leader(session)
        await settings_store.load(session)
        if not ok:
            log.error("another_worker_holds_lock")
            raise SystemExit("not leader")

    scheduler = AsyncIOScheduler()
    scheduler.add_job(lambda: asyncio.create_task(_safe(tasks.partner_ping, redis)), "interval", seconds=60)
    scheduler.add_job(lambda: asyncio.create_task(_safe(tasks.sync_servers, redis)), "interval", seconds=60)
    scheduler.add_job(lambda: asyncio.create_task(_safe(tasks.sync_catalog, redis)), "interval", minutes=10)
    scheduler.add_job(lambda: asyncio.create_task(_safe(tasks.partner_balance, redis)), "interval", minutes=5)
    scheduler.add_job(lambda: asyncio.create_task(_safe(tasks.renewals, redis)), "interval", hours=1)
    scheduler.add_job(lambda: asyncio.create_task(_safe(tasks.reminders, redis)), "interval", hours=1)
    scheduler.add_job(lambda: asyncio.create_task(_safe(tasks.invoices_watch, redis)), "interval", minutes=1)
    scheduler.start()

    # initial catalog
    async with session_scope() as session:
        await tasks.sync_catalog(session, redis)

    log.info("worker_started")
    try:
        while True:
            paused = bool(await redis.get("partner:paused_until"))
            async with factory() as session:
                try:
                    ran = await run_one(session, redis, paused=paused)
                    await session.commit()
                except Exception:
                    await session.rollback()
                    log.exception("dispatch_loop_error")
                    ran = False
            items = drain_pending()
            if items:
                async with factory() as session:
                    await deliver(bot, redis, settings, items, session)
                    await session.commit()
            await _process_delete_queue(bot, redis)
            await asyncio.sleep(0.2 if ran else 1.0)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await redis.aclose()


async def _safe(fn, redis) -> None:
    try:
        async with session_scope() as session:
            await fn(session, redis)
    except Exception:
        log.exception("scheduled_failed", fn=fn.__name__)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
