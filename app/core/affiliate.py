"""Affiliate partner (reseller) commissions — separate from Tihost provider."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import D, format_usd, money
from app.core.settings_store import settings_store
from app.db.models import PartnerLedgerEntry, PartnerWithdraw, User
from app.jobs.notify import enqueue_notify
from app.logging import get_logger

log = get_logger("core.affiliate")

ATTACH_INVITE = "invite"
ATTACH_TRANSFER = "transfer"


def rate_for_attach(kind: str | None) -> Decimal:
    if kind == ATTACH_TRANSFER:
        return D(str(settings_store.get("partner_rate_transfer", "0.10") or "0.10"))
    if kind == ATTACH_INVITE:
        return D(str(settings_store.get("partner_rate_invite", "0.15") or "0.15"))
    return Decimal("0")


async def attach_client(
    session: AsyncSession,
    *,
    client: User,
    partner: User,
    kind: str,
    force: bool = False,
) -> bool:
    """Bind client to partner once. Returns True if attached (or already bound to same)."""
    if not partner.is_partner:
        return False
    if client.id == partner.id:
        return False
    if client.referred_by_id and not force:
        return client.referred_by_id == partner.id
    client.referred_by_id = partner.id
    client.attach_kind = kind if kind in {ATTACH_INVITE, ATTACH_TRANSFER} else ATTACH_INVITE
    if not client.ref_source:
        client.ref_source = f"ref_{partner.tg_id}"
    await session.flush()
    return True


async def credit_commission_on_topup(
    session: AsyncSession,
    *,
    client_user_id: int,
    topup_usd: Decimal,
    invoice_id: int,
) -> PartnerLedgerEntry | None:
    """Credit partner % of client top-up. Idempotent per invoice."""
    client = await session.get(User, client_user_id)
    if client is None or not client.referred_by_id:
        return None
    partner = await session.get(User, client.referred_by_id)
    if partner is None or not partner.is_partner:
        return None
    rate = rate_for_attach(client.attach_kind)
    if rate <= 0:
        return None
    base = money(topup_usd)
    amount = money(base * rate)
    if amount <= 0:
        return None
    uniq = f"partner:comm:invoice:{invoice_id}"
    existing = await session.scalar(
        select(PartnerLedgerEntry).where(PartnerLedgerEntry.uniq_key == uniq)
    )
    if existing:
        return existing

    partner = await session.scalar(
        select(User).where(User.id == partner.id).with_for_update()
    )
    assert partner is not None
    partner.partner_balance_usd = money(D(partner.partner_balance_usd) + amount)
    entry = PartnerLedgerEntry(
        partner_user_id=partner.id,
        client_user_id=client.id,
        kind="commission",
        amount_usd=amount,
        rate=rate,
        base_usd=base,
        attach_kind=client.attach_kind,
        uniq_key=uniq,
        reason=f"topup client#{client.id}",
        ref_type="invoice",
        ref_id=invoice_id,
    )
    session.add(entry)
    await session.flush()

    pct = int(rate * 100)
    await enqueue_notify(
        session,
        user_id=partner.id,
        key=f"partner_earn:{invoice_id}",
        ref=str(invoice_id),
        text=(
            f"💰 Партнёрский доход +{format_usd(amount)} "
            f"({pct}% с пополнения {format_usd(base)})"
            if (partner.lang or "en") == "ru"
            else (
                f"💰 Partner earnings +{format_usd(amount)} "
                f"({pct}% of {format_usd(base)} top-up)"
            )
        ),
        immediate=True,
    )
    log.info(
        "partner_commission",
        partner_id=partner.id,
        client_id=client.id,
        amount=str(amount),
        invoice_id=invoice_id,
    )
    return entry


async def create_withdraw(
    session: AsyncSession,
    *,
    partner: User,
    amount: Decimal,
    details: str,
) -> PartnerWithdraw:
    amount = money(amount)
    if amount <= 0:
        raise ValueError("amount")
    partner = await session.scalar(
        select(User).where(User.id == partner.id).with_for_update()
    )
    assert partner is not None
    if D(partner.partner_balance_usd) < amount:
        raise ValueError("insufficient")
    partner.partner_balance_usd = money(D(partner.partner_balance_usd) - amount)
    w = PartnerWithdraw(
        partner_user_id=partner.id,
        amount_usd=amount,
        details=(details or "")[:500],
        status="pending",
    )
    session.add(w)
    await session.flush()
    entry = PartnerLedgerEntry(
        partner_user_id=partner.id,
        kind="withdraw",
        amount_usd=money(-amount),
        uniq_key=f"partner:withdraw:{w.id}",
        reason="withdraw_request",
        ref_type="partner_withdraw",
        ref_id=w.id,
    )
    session.add(entry)
    await session.flush()
    return w


async def settle_withdraw(
    session: AsyncSession,
    *,
    withdraw: PartnerWithdraw,
    paid: bool,
    admin_tg_id: int,
    note: str = "",
) -> None:
    if withdraw.status != "pending":
        return
    from app.jobs.results import now_utc

    partner = await session.scalar(
        select(User).where(User.id == withdraw.partner_user_id).with_for_update()
    )
    if partner is None:
        return
    if paid:
        withdraw.status = "paid"
    else:
        # return funds
        partner.partner_balance_usd = money(
            D(partner.partner_balance_usd) + D(withdraw.amount_usd)
        )
        session.add(
            PartnerLedgerEntry(
                partner_user_id=partner.id,
                kind="adjust",
                amount_usd=money(withdraw.amount_usd),
                uniq_key=f"partner:withdraw_reject:{withdraw.id}",
                reason="withdraw_rejected",
                ref_type="partner_withdraw",
                ref_id=withdraw.id,
            )
        )
        withdraw.status = "rejected"
    withdraw.processed_by = admin_tg_id
    withdraw.admin_note = (note or "")[:300]
    withdraw.processed_at = now_utc()
    await session.flush()
    lang = partner.lang or "en"
    if paid:
        text = (
            f"✅ Заявка на вывод #{withdraw.id} оплачена: {format_usd(withdraw.amount_usd)}"
            if lang == "ru"
            else f"✅ Withdrawal #{withdraw.id} paid: {format_usd(withdraw.amount_usd)}"
        )
    else:
        text = (
            f"❌ Заявка на вывод #{withdraw.id} отклонена. Сумма возвращена на партнёрский баланс."
            if lang == "ru"
            else f"❌ Withdrawal #{withdraw.id} rejected. Amount returned to partner balance."
        )
    await enqueue_notify(
        session,
        user_id=partner.id,
        key=f"partner_wd:{withdraw.id}:{withdraw.status}",
        ref=str(withdraw.id),
        text=text,
        immediate=True,
    )
