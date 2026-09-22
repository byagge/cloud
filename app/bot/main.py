from __future__ import annotations

import asyncio

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from redis.asyncio import from_url as redis_from_url
from sqlalchemy import text

from app.bot import setup_routers
from app.bot.middlewares import BanMiddleware, DbUserMiddleware, MaintenanceMiddleware
from app.config import get_settings
from app.core.settings_store import DEFAULTS, settings_store
from app.db.models import Admin, Base, Setting
from app.db.session import get_engine, get_session_factory, session_scope
from app.infra.memory_redis import MemoryRedis
from app.logging import get_logger, setup_logging
from app.partner import get_partner, set_partner
from app.partner.fake import FakePartner
from app.payments import get_gateway
from app.payments.base import InvalidSignature
from app.payments.service import apply_invoice_state

log = get_logger("bot")


async def _migrate_and_seed() -> None:
    from decimal import Decimal

    from app.db.models import PromoCode

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    settings = get_settings()
    async with session_scope() as session:
        for key, value in DEFAULTS.items():
            existing = await session.get(Setting, key)
            if existing is None:
                session.add(Setting(key=key, value=value))
        if settings.owner_tg_id:
            admin = await session.get(Admin, settings.owner_tg_id)
            if admin is None:
                session.add(Admin(tg_id=settings.owner_tg_id, role="owner"))
        # demo promo
        from sqlalchemy import select

        exists = await session.scalar(select(PromoCode).where(PromoCode.code == "ARIX10"))
        if exists is None:
            session.add(
                PromoCode(code="ARIX10", amount_usd=Decimal("10.00"), max_uses=1000, used_count=0)
            )
        await settings_store.load(session)


async def health(request: web.Request) -> web.Response:
    redis = request.app["redis"]
    ok_db = False
    ok_redis = False
    try:
        async with get_session_factory()() as session:
            await session.execute(text("SELECT 1"))
            ok_db = True
    except Exception:
        pass
    try:
        ok_redis = bool(await redis.ping())
    except Exception:
        ok_redis = False
    partner_ok = await redis.get("partner:last_ping_ok")
    body = {
        "db": "ok" if ok_db else "fail",
        "redis": "ok" if ok_redis else "fail",
        "partner": {"last_ping_ok": bool(partner_ok)},
    }
    return web.json_response(body, status=200 if ok_db and ok_redis else 503)


async def pay_webhook(request: web.Request) -> web.Response:
    settings = get_settings()
    secret = request.match_info.get("secret")
    if secret != settings.webhook_path_secret:
        return web.Response(status=404)
    body = await request.read()
    if len(body) > 65536:
        return web.Response(status=413)
    gateway = get_gateway()
    try:
        event = gateway.verify_webhook(request.headers, body)
    except InvalidSignature:
        return web.Response(status=401)
    from app.db.models import Invoice

    async with session_scope() as session:
        try:
            invoice_id = int(event.order_ref)
        except ValueError:
            return web.Response(status=200)
        inv = await session.get(Invoice, invoice_id)
        if inv is None:
            return web.Response(status=200)
        if inv.gateway_invoice_id and inv.gateway_invoice_id != event.gateway_invoice_id:
            return web.Response(status=200)
        remote = await gateway.get_invoice(event.gateway_invoice_id)
        await apply_invoice_state(session, inv, remote)
    return web.Response(status=200)


def _make_redis(settings):
    if settings.redis_url.startswith("memory"):
        return MemoryRedis(), MemoryStorage()
    client = redis_from_url(settings.redis_url, decode_responses=True)
    return client, RedisStorage(redis=client)


async def _embedded_worker(bot: Bot, redis) -> None:
    """Process jobs in the same process (dev / single-box)."""
    from app.jobs.dispatcher import run_one
    from app.jobs.notify import deliver, drain_pending
    from app.jobs.scheduled import tasks

    settings = get_settings()
    factory = get_session_factory()
    # one-shot catalog already done; light loop
    while True:
        try:
            async with factory() as session:
                ran = await run_one(session, redis, paused=False)
                await session.commit()
            items = drain_pending()
            if items:
                async with factory() as session:
                    await deliver(bot, redis, settings, items, session)
                    await session.commit()
            if not ran:
                # occasional scheduled
                async with session_scope() as session:
                    await tasks.sync_servers(session, redis)
            await asyncio.sleep(0.5 if ran else 2.0)
        except Exception:
            log.exception("embedded_worker_error")
            await asyncio.sleep(2.0)


async def main_async() -> None:
    from pathlib import Path

    Path("data").mkdir(exist_ok=True)
    setup_logging(json_logs=not get_settings().is_dev)
    settings = get_settings()
    if not settings.bot_token:
        raise SystemExit("Set BOT_TOKEN in .env")

    redis, storage = _make_redis(settings)
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher(storage=storage)
    dp.update.outer_middleware(DbUserMiddleware())
    dp.update.outer_middleware(BanMiddleware())
    dp.update.outer_middleware(MaintenanceMiddleware())
    setup_routers(dp)

    @dp.update.outer_middleware()
    async def inject_redis(handler, event, data):
        data["redis"] = redis
        return await handler(event, data)

    await _migrate_and_seed()
    if settings.use_fake_partner:
        set_partner(FakePartner())
    else:
        get_partner()

    from app.jobs.scheduled.tasks import sync_catalog

    async with session_scope() as session:
        await sync_catalog(session, redis)

    worker_task = None
    if settings.embedded_worker:
        worker_task = asyncio.create_task(_embedded_worker(bot, redis))
        log.info("embedded_worker_on")

    app = web.Application()
    app["redis"] = redis
    app.router.add_get("/health", health)
    app.router.add_post("/webhooks/pay/{secret}", pay_webhook)

    if settings.bot_mode == "polling":
        await bot.delete_webhook(drop_pending_updates=False)
        log.info("bot_polling")
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, settings.http_host, settings.http_port)
        await site.start()
        try:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        finally:
            if worker_task:
                worker_task.cancel()
            await runner.cleanup()
            await bot.session.close()
            await redis.aclose()
        return

    webhook_path = f"/tg/{settings.tg_webhook_secret}"
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=settings.tg_webhook_secret).register(
        app, path=webhook_path
    )
    setup_application(app, dp, bot=bot)
    await bot.set_webhook(
        url=f"{settings.public_base_url.rstrip('/')}{webhook_path}",
        secret_token=settings.tg_webhook_secret,
    )
    log.info("bot_webhook", url=settings.public_base_url)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.http_host, settings.http_port)
    await site.start()
    await asyncio.Event().wait()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
