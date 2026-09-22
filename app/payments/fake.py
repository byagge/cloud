from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Mapping

from app.core.money import money
from app.payments.base import GatewayInvoice, InvalidSignature, WebhookEvent, body_hash


class FakePaymentGateway:
    name = "fake"

    def __init__(self) -> None:
        self._invoices: dict[str, GatewayInvoice] = {}

    async def create_invoice(
        self,
        *,
        order_ref: str,
        amount_usd: Decimal,
        ttl_min: int,
        network: str | None,
    ) -> GatewayInvoice:
        gid = f"fake-{uuid.uuid4().hex[:12]}"
        inv = GatewayInvoice(
            gateway_invoice_id=gid,
            status="pending",
            pay_url=f"https://pay.local/fake/{gid}",
            address=f"TFake{gid[:24]}",
            asset="USDT",
            network=network or "TRC20",
            pay_amount=money(amount_usd),
            paid_usd=None,
            txid=None,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_min),
        )
        self._invoices[gid] = inv
        self._order_refs = getattr(self, "_order_refs", {})
        self._order_refs[gid] = order_ref
        return inv

    async def get_invoice(self, gateway_invoice_id: str) -> GatewayInvoice:
        inv = self._invoices.get(gateway_invoice_id)
        if inv is None:
            raise KeyError(gateway_invoice_id)
        return inv

    def mark_paid(self, gateway_invoice_id: str, paid_usd: Decimal | None = None) -> GatewayInvoice:
        inv = self._invoices[gateway_invoice_id]
        inv.status = "paid"
        inv.paid_usd = paid_usd if paid_usd is not None else money(inv.pay_amount or 0)
        inv.txid = f"tx-{gateway_invoice_id}"
        return inv

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> WebhookEvent:
        if headers.get("X-Fake-Sign") != "ok":
            raise InvalidSignature("bad signature")
        data = json.loads(body.decode())
        return WebhookEvent(
            gateway_invoice_id=data["gateway_invoice_id"],
            order_ref=str(data["order_ref"]),
            status=data["status"],
            paid_usd=money(data["paid_usd"]) if data.get("paid_usd") is not None else None,
            txid=data.get("txid"),
            raw_hash=body_hash(body),
        )
