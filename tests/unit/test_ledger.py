from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.ledger import InsufficientBalance, LedgerKind, post_entry
from app.db.models import Base, LedgerEntry, User


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        user = User(tg_id=1, balance_usd=Decimal("100.00"), accepted_terms=True)
        s.add(user)
        await s.commit()
        await s.refresh(user)
        yield s, user
    await engine.dispose()


@pytest.mark.asyncio
async def test_post_entry_idempotent(session):
    s, user = session
    e1 = await post_entry(
        s,
        user_id=user.id,
        kind=LedgerKind.PURCHASE,
        amount=Decimal("-10.00"),
        uniq_key="order:1:purchase",
    )
    await s.commit()
    e2 = await post_entry(
        s,
        user_id=user.id,
        kind=LedgerKind.PURCHASE,
        amount=Decimal("-10.00"),
        uniq_key="order:1:purchase",
    )
    await s.refresh(user)
    assert e1.id == e2.id
    assert user.balance_usd == Decimal("90.00")


@pytest.mark.asyncio
async def test_insufficient(session):
    s, user = session
    with pytest.raises(InsufficientBalance):
        await post_entry(
            s,
            user_id=user.id,
            kind=LedgerKind.PURCHASE,
            amount=Decimal("-200.00"),
            uniq_key="order:2:purchase",
        )
