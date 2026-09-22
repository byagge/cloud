from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import D, money
from app.db.models import LedgerEntry, User


class LedgerKind(StrEnum):
    TOPUP = "topup"
    PURCHASE = "purchase"
    RENEWAL = "renewal"
    REFUND = "refund"
    PROMO = "promo"
    ADJUST = "adjust"


class InsufficientBalance(Exception):
    def __init__(self, *, balance: Decimal, needed: Decimal) -> None:
        self.balance = money(balance)
        self.needed = money(needed)
        super().__init__(f"need {self.needed}, have {self.balance}")


async def post_entry(
    session: AsyncSession,
    *,
    user_id: int,
    kind: LedgerKind | str,
    amount: Decimal,
    uniq_key: str,
    ref_type: str | None = None,
    ref_id: int | None = None,
    reason: str | None = None,
    admin_id: int | None = None,
) -> LedgerEntry:
    """Idempotent ledger post. Must run inside a transaction."""
    amount = money(amount)
    kind_s = kind.value if isinstance(kind, LedgerKind) else str(kind)

    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise ValueError(f"user {user_id} not found")

    existing = await session.scalar(
        select(LedgerEntry).where(LedgerEntry.uniq_key == uniq_key)
    )
    if existing:
        return existing

    new_balance = D(user.balance_usd) + amount
    if new_balance < 0:
        raise InsufficientBalance(balance=user.balance_usd, needed=-amount)

    entry = LedgerEntry(
        user_id=user_id,
        kind=kind_s,
        amount_usd=amount,
        uniq_key=uniq_key,
        ref_type=ref_type,
        ref_id=ref_id,
        reason=reason,
        admin_id=admin_id,
    )
    user.balance_usd = money(new_balance)
    session.add(entry)
    await session.flush()
    return entry
