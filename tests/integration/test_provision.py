from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.ledger import LedgerKind, post_entry
from app.core.orders import confirm_purchase
from app.core.settings_store import settings_store
from app.db.models import Base, Job, Order, Server, User
from app.infra.memory_redis import MemoryRedis
from app.jobs.dispatcher import run_one
from app.partner import set_partner
from app.partner.fake import FakePartner


@pytest.mark.asyncio
async def test_purchase_provision_flow():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_partner(FakePartner(starting_balance=Decimal("500")))
    redis = MemoryRedis()

    async with factory() as session:
        user = User(tg_id=42, accepted_terms=True, balance_usd=Decimal("0"))
        session.add(user)
        await session.flush()
        await post_entry(
            session,
            user_id=user.id,
            kind=LedgerKind.TOPUP,
            amount=Decimal("50.00"),
            uniq_key="topup:test",
        )
        order = Order(
            user_id=user.id,
            kind="purchase",
            status="draft",
            location="germany",
            plan_id="t1",
            os_id="ubuntu-22.04",
            months=1,
            snapshot={"plan_label": "1 vCPU · 2 ГБ · 40 ГБ"},
            partner_cost_usd=Decimal("4.00"),
            price_usd=Decimal("9.00"),
        )
        session.add(order)
        await session.flush()
        await confirm_purchase(
            session, order=order, price=Decimal("9.00"), partner_cost=Decimal("4.00")
        )
        await session.commit()
        order_id = order.id

    # run dispatcher until provision done
    for _ in range(5):
        async with factory() as session:
            ran = await run_one(session, redis, paused=False)
            await session.commit()
            if not ran:
                break

    async with factory() as session:
        order = await session.get(Order, order_id)
        assert order is not None
        assert order.status == "active"
        assert order.server_id is not None
        server = await session.get(Server, order.server_id)
        assert server is not None
        assert server.partner_id
        jobs = (await session.scalars(Job.__table__.select())).all()  # type: ignore
        # at least provision done
        from sqlalchemy import select

        done = (
            await session.scalars(select(Job).where(Job.kind == "provision", Job.status == "done"))
        ).all()
        assert done

    await engine.dispose()
