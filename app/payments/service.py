from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ledger import LedgerKind, post_entry
from app.core.money import money
from app.db.models import Invoice
from app.jobs.notify import enqueue_notify
from app.payments.base import GatewayInvoice


async def apply_invoice_state(
    session: AsyncSession, invoice: Invoice, remote: GatewayInvoice
) -> None:
    if invoice.status == "paid":
        return
    if remote.status == "paid":
        paid = remote.paid_usd if remote.paid_usd is not None else invoice.amount_usd
        # cap at 1.5x requested
        cap = money(invoice.amount_usd * Decimal("1.5"))
        credited = min(money(paid), cap)
        await post_entry(
            session,
            user_id=invoice.user_id,
            kind=LedgerKind.TOPUP,
            amount=credited,
            uniq_key=f"topup:{invoice.id}",
            ref_type="invoice",
            ref_id=invoice.id,
        )
        invoice.status = "paid"
        invoice.credited_usd = credited
        invoice.paid_at = datetime.now(timezone.utc)
        invoice.txid = remote.txid
        await enqueue_notify(
            session,
            user_id=invoice.user_id,
            key="topup_ok",
            ref=str(invoice.id),
            text=f"Баланс пополнен на ${credited}.",
        )
    elif remote.status == "partial":
        invoice.status = "partial"
    elif remote.status in {"expired", "failed"}:
        invoice.status = remote.status
