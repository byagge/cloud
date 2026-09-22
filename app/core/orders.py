from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ledger import InsufficientBalance, LedgerKind, post_entry
from app.core.money import money
from app.core.pricing import client_price
from app.core.settings_store import settings_store
from app.db.models import Job, Order, Server


async def confirm_purchase(
    session: AsyncSession,
    *,
    order: Order,
    price: Decimal,
    partner_cost: Decimal,
) -> Job:
    """Charge balance and enqueue provision job. Caller commits."""
    markup = settings_store.decimal("markup")
    fee = settings_store.decimal("gateway_fee")
    order.partner_cost_usd = money(partner_cost)
    order.markup = markup
    order.fee = fee
    order.price_usd = money(price)
    order.status = "queued"
    order.updated_at = datetime.now(timezone.utc)

    await post_entry(
        session,
        user_id=order.user_id,
        kind=LedgerKind.PURCHASE,
        amount=-money(price),
        uniq_key=f"order:{order.id}:purchase",
        ref_type="order",
        ref_id=order.id,
    )

    job = Job(
        kind="provision",
        class_="purchase",
        payload={"order_id": order.id},
        status="pending",
        idem_key=f"order-{order.id}",
        order_id=order.id,
    )
    session.add(job)
    await session.flush()
    return job


async def confirm_renewal(
    session: AsyncSession,
    *,
    user_id: int,
    server: Server,
    days: int,
    partner_cost: Decimal,
) -> tuple[Order, Job]:
    price = client_price(
        partner_cost,
        settings_store.decimal("markup"),
        settings_store.decimal("gateway_fee"),
        settings_store.decimal("price_step"),
    )
    order = Order(
        user_id=user_id,
        kind="renew",
        status="queued",
        server_id=server.id,
        renew_days=days,
        partner_cost_usd=money(partner_cost),
        markup=settings_store.decimal("markup"),
        fee=settings_store.decimal("gateway_fee"),
        price_usd=price,
        snapshot={},
    )
    session.add(order)
    await session.flush()

    try:
        await post_entry(
            session,
            user_id=user_id,
            kind=LedgerKind.RENEWAL,
            amount=-price,
            uniq_key=f"order:{order.id}:renewal",
            ref_type="order",
            ref_id=order.id,
        )
    except InsufficientBalance:
        order.status = "cancelled"
        raise

    job = Job(
        kind="renew",
        class_="renew",
        payload={"order_id": order.id, "server_id": server.id, "days": days},
        status="pending",
        idem_key=f"renew-{order.id}",
        order_id=order.id,
        server_id=server.id,
    )
    session.add(job)
    await session.flush()
    return order, job


async def get_active_server_count(session: AsyncSession, user_id: int) -> int:
    q = await session.scalars(
        select(Server).where(
            Server.user_id == user_id,
            Server.missing.is_(False),
        )
    )
    return len(list(q))
