from __future__ import annotations

"""
Seed helper for local demos. Usage:
  python -m scripts.seed_dev
"""

import asyncio
from decimal import Decimal

from app.config import get_settings
from app.core.ledger import LedgerKind, post_entry
from app.core.settings_store import DEFAULTS
from app.db.models import Admin, Base, Setting, User
from app.db.session import get_engine, session_scope
from app.partner import set_partner
from app.partner.fake import FakePartner


async def main() -> None:
    from pathlib import Path

    Path("data").mkdir(exist_ok=True)
    settings = get_settings()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    set_partner(FakePartner())
    async with session_scope() as session:
        for k, v in DEFAULTS.items():
            if await session.get(Setting, k) is None:
                session.add(Setting(key=k, value=v))
        if settings.owner_tg_id:
            if await session.get(Admin, settings.owner_tg_id) is None:
                session.add(Admin(tg_id=settings.owner_tg_id, role="owner"))
            user = await session.scalar(
                __import__("sqlalchemy").select(User).where(User.tg_id == settings.owner_tg_id)
            )
            if user is None:
                user = User(
                    tg_id=settings.owner_tg_id,
                    username="owner",
                    first_name="Owner",
                    accepted_terms=True,
                    balance_usd=Decimal("0"),
                )
                session.add(user)
                await session.flush()
            await post_entry(
                session,
                user_id=user.id,
                kind=LedgerKind.PROMO,
                amount=Decimal("100.00"),
                uniq_key="seed:promo:100",
                reason="dev seed",
                admin_id=settings.owner_tg_id,
            )
    print("Seeded. Owner balance +$100 (idempotent).")


if __name__ == "__main__":
    asyncio.run(main())
