from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ledger import LedgerKind, post_entry
from app.db.models import Job, Order, Server
from app.jobs.notify import enqueue_notify
from app.jobs.results import Done, Fail, now_utc
from app.partner.base import PartnerError, PartnerErrorCategory


async def handle(session: AsyncSession, redis, partner, job: Job):
    order = await session.get(Order, job.order_id)
    server = await session.get(Server, job.server_id or (order.server_id if order else None))
    if order is None or server is None or not server.partner_id:
        return Fail(reason="missing")
    days = int(job.payload.get("days") or order.renew_days or 30)
    try:
        result = await partner.renew(server.partner_id, days, job.idem_key or f"renew-{order.id}")
    except PartnerError as e:
        if e.category == PartnerErrorCategory.EXPIRED:
            await post_entry(
                session,
                user_id=order.user_id,
                kind=LedgerKind.REFUND,
                amount=order.price_usd or 0,
                uniq_key=f"order:{order.id}:refund",
                ref_type="order",
                ref_id=order.id,
                reason="rent_expired",
            )
            order.status = "refunded"
            await enqueue_notify(
                session,
                user_id=order.user_id,
                key="rent_expired",
                ref=str(server.id),
                text="Срок истёк, напишите в поддержку.",
            )
            return Fail(reason=e.code)
        raise

    server.rent_expires_at = result.rent_expires_at
    server.reminded_3d_at = None
    server.reminded_1d_at = None
    order.status = "active"
    order.charged_by_partner_usd = result.charged_usd
    order.finished_at = now_utc()
    await enqueue_notify(
        session,
        user_id=order.user_id,
        key="renewed",
        ref=str(server.id),
        text=f"Продлено до {result.rent_expires_at:%d.%m.%Y %H:%M} UTC",
    )
    return Done()
