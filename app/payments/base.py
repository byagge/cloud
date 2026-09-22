from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Literal, Mapping, Protocol

from pydantic import BaseModel, ConfigDict


class GatewayInvoice(BaseModel):
    model_config = ConfigDict(extra="allow")
    gateway_invoice_id: str
    status: Literal["pending", "paid", "expired", "partial", "failed"]
    pay_url: str | None = None
    address: str | None = None
    asset: str | None = None
    network: str | None = None
    pay_amount: Decimal | None = None
    paid_usd: Decimal | None = None
    txid: str | None = None
    expires_at: datetime


class WebhookEvent(BaseModel):
    gateway_invoice_id: str
    order_ref: str
    status: str
    paid_usd: Decimal | None = None
    txid: str | None = None
    raw_hash: str


class InvalidSignature(Exception):
    pass


class PaymentGateway(Protocol):
    name: str

    async def create_invoice(
        self,
        *,
        order_ref: str,
        amount_usd: Decimal,
        ttl_min: int,
        network: str | None,
    ) -> GatewayInvoice: ...

    async def get_invoice(self, gateway_invoice_id: str) -> GatewayInvoice: ...

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> WebhookEvent: ...


def body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()
