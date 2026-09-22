from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import money
from app.core.orders import confirm_renewal
from app.core.pricing import client_price
from app.core.settings_store import settings_store
from app.db.models import Order, Server
from app.jobs.notify import enqueue_notify
from app.jobs.results import now_utc
from app.logging import get_logger
from app.partner import get_partner
from app.partner.fake import LOCATIONS

log = get_logger("jobs.scheduled")


async def sync_catalog(session: AsyncSession, redis) -> None:
    partner = get_partner()
    for loc in LOCATIONS:
        try:
            plans = await partner.plans(loc)
            payload = {
                "fetched_at": now_utc().isoformat(),
                "plans": [p.model_dump(mode="json") for p in plans],
            }
            await redis.set(f"catalog:plans:{loc}", json.dumps(payload), ex=3600)
        except Exception:
            log.exception("sync_catalog_failed", location=loc)


async def sync_servers(session: AsyncSession, redis) -> None:
    partner = get_partner()
    try:
        remote = await partner.list_servers()
    except Exception:
        log.exception("sync_servers_list_failed")
        return

    by_id = {s.id: s for s in remote}
    by_name = {s.name: s for s in remote}
    local = (await session.scalars(select(Server))).all()
    seen: set[int] = set()

    for server in local:
        remote_s = None
        if server.partner_id and server.partner_id in by_id:
            remote_s = by_id[server.partner_id]
        elif server.partner_name in by_name:
            remote_s = by_name[server.partner_name]
            server.partner_id = remote_s.id

        if remote_s is None:
            miss_key = f"miss:{server.id}"
            count = int(await redis.get(miss_key) or 0) + 1
            await redis.set(miss_key, count, ex=3600)
            if count >= 2 and not server.missing:
                server.missing = True
                await enqueue_notify(
                    session,
                    user_id=None,
                    key="server_missing",
                    ref=str(server.id),
                    text=f"Сервер #{server.id} пропал у партнёра",
                    admin=True,
                )
            continue

        await redis.delete(f"miss:{server.id}")
        server.missing = False
        server.partner_state = remote_s.state
        server.ip = remote_s.ip or server.ip
        server.cpu = remote_s.cpu or server.cpu
        server.ram_mb = remote_s.ram_mb or server.ram_mb
        server.disk_gb = remote_s.disk_gb or server.disk_gb
        server.os_label = remote_s.os or server.os_label
        if remote_s.rent_expires_at:
            server.rent_expires_at = remote_s.rent_expires_at
        if remote_s.renew_prices:
            server.renew_prices = {str(k): str(v) for k, v in remote_s.renew_prices.items()}
        server.synced_at = now_utc()
        seen.add(server.id)

        if (
            (remote_s.state or "").lower() in {"running", "active"}
            and remote_s.ip
            and server.ready_notified_at is None
        ):
            server.ready_notified_at = now_utc()
            await enqueue_notify(
                session,
                user_id=server.user_id,
                key="server_ready",
                ref=str(server.id),
                text=(
                    f"Сервер <b>{server.display_name}</b> готов.\n"
                    f"IP: <code>{remote_s.ip}</code>\n"
                    f"Логин: <code>{server.login or 'root'}</code>"
                ),
            )

        if server.reinstall_pending and (remote_s.state or "").lower() in {"running", "active"}:
            from app.db.models import Job

            session.add(
                Job(
                    kind="reset_password",
                    class_="manage",
                    payload={"server_id": server.id},
                    status="pending",
                    idem_key=f"reinstall-pw-{server.id}-{int(now_utc().timestamp())}",
                    server_id=server.id,
                )
            )
            server.reinstall_pending = False


async def renewals(session: AsyncSession, redis) -> None:
    horizon = now_utc() + timedelta(hours=24)
    servers = (
        await session.scalars(
            select(Server).where(
                Server.auto_renew.is_(True),
                Server.cancelled.is_(False),
                Server.frozen.is_(False),
                Server.missing.is_(False),
                Server.rent_expires_at <= horizon,
            )
        )
    ).all()
    for server in servers:
        pending = await session.scalar(
            select(Order).where(
                Order.server_id == server.id,
                Order.kind == "renew",
                Order.status.in_(("queued", "provisioning", "waiting_partner_funds")),
            )
        )
        if pending:
            continue
        prices = server.renew_prices or {}
        raw = prices.get(str(server.renew_days)) or prices.get(server.renew_days)
        if raw is None:
            continue
        from decimal import Decimal

        from app.core.money import D
        from app.db.models import User

        partner_cost = D(raw)
        user = await session.get(User, server.user_id)
        if user is None:
            continue
        price = client_price(
            partner_cost,
            settings_store.decimal("markup"),
            settings_store.decimal("gateway_fee"),
            settings_store.decimal("price_step"),
        )
        if user.balance_usd < price:
            await enqueue_notify(
                session,
                user_id=user.id,
                key="renew_failed_no_funds",
                ref=f"{server.id}:{now_utc().date()}",
                text=f"Не хватает средств для автопродления {server.display_name}.",
            )
            continue
        try:
            await confirm_renewal(
                session,
                user_id=user.id,
                server=server,
                days=server.renew_days,
                partner_cost=partner_cost,
            )
        except Exception:
            log.exception("auto_renew_failed", server_id=server.id)


async def reminders(session: AsyncSession, redis) -> None:
    now = now_utc()
    servers = (
        await session.scalars(
            select(Server).where(Server.cancelled.is_(False), Server.rent_expires_at.is_not(None))
        )
    ).all()
    for server in servers:
        if not server.rent_expires_at:
            continue
        days_left = (server.rent_expires_at - now).total_seconds() / 86400
        if 2.5 <= days_left <= 3.5 and server.reminded_3d_at is None:
            server.reminded_3d_at = now
            await enqueue_notify(
                session,
                user_id=server.user_id,
                key="remind_3d",
                ref=str(server.id),
                text=f"Через 3 дня истекает аренда {server.display_name}.",
            )
        if 0.5 <= days_left <= 1.5 and server.reminded_1d_at is None:
            server.reminded_1d_at = now
            await enqueue_notify(
                session,
                user_id=server.user_id,
                key="remind_1d",
                ref=str(server.id),
                text=f"Завтра истекает аренда {server.display_name}.",
            )


async def partner_ping(session: AsyncSession, redis) -> None:
    partner = get_partner()
    try:
        await partner.ping()
        await redis.set("partner:last_ping_ok", "1", ex=120)
        await redis.set("partner:last_ping_at", now_utc().isoformat(), ex=300)
        await redis.delete("partner_down")
    except Exception:
        fails = int(await redis.incr("partner:ping_fails"))
        await redis.expire("partner:ping_fails", 300)
        if fails >= 3:
            await redis.set("partner_down", "1", ex=300)
            await enqueue_notify(
                session,
                user_id=None,
                key="partner_down",
                ref="ping",
                text="Партнёр недоступен (3 неудачных ping)",
                admin=True,
            )


async def partner_balance(session: AsyncSession, redis) -> None:
    partner = get_partner()
    try:
        bal = await partner.balance()
        await redis.set("partner:balance", str(bal), ex=600)
    except Exception:
        log.exception("partner_balance_failed")


async def invoices_watch(session: AsyncSession, redis) -> None:
    from app.db.models import Invoice
    from app.payments import get_gateway
    from app.payments.base import GatewayInvoice
    from app.payments.crypto import check_incoming
    from app.payments.service import apply_invoice_state
    from app.core.money import D

    gateway = get_gateway()
    now = now_utc()
    pending = (
        await session.scalars(
            select(Invoice).where(
                Invoice.status == "pending",
                Invoice.expires_at > now - timedelta(hours=2),
            )
        )
    ).all()
    for inv in pending[:20]:
        if not inv.gateway_invoice_id:
            continue
        try:
            if inv.gateway == "crypto_direct":
                since = inv.created_at.timestamp() if inv.created_at else now.timestamp()
                ok = await check_incoming(
                    network=inv.network or "",
                    asset=inv.asset or "USDT",
                    address=inv.address or "",
                    min_amount=D(inv.pay_amount or inv.amount_usd),
                    since_ts=since,
                )
                if ok:
                    remote = GatewayInvoice(
                        gateway_invoice_id=inv.gateway_invoice_id,
                        status="paid",
                        paid_usd=inv.amount_usd,
                        pay_amount=inv.pay_amount,
                        address=inv.address,
                        asset=inv.asset,
                        network=inv.network,
                        expires_at=inv.expires_at,
                        txid=f"poll-{inv.id}",
                    )
                    await apply_invoice_state(session, inv, remote)
                continue
            remote = await gateway.get_invoice(inv.gateway_invoice_id)
            await apply_invoice_state(session, inv, remote)
        except Exception:
            log.exception("invoice_poll_failed", invoice_id=inv.id)
    expired = (
        await session.scalars(
            select(Invoice).where(Invoice.status == "pending", Invoice.expires_at < now - timedelta(hours=2))
        )
    ).all()
    for inv in expired:
        inv.status = "expired"
