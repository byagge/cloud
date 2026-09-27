"""xRocket Pay (legacy pay.xrocket.tg) payment gateway."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping

import httpx

from app.core.money import D, money
from app.core.settings_store import settings_store
from app.logging import get_logger
from app.payments.base import GatewayInvoice, InvalidSignature, WebhookEvent, body_hash

log = get_logger("payments.xrocket")

# Official legacy API root (paths: /tg-invoices, /app/info, …)
BASE = "https://pay.xrocket.tg/api"


def _token() -> str:
    return str(settings_store.get("xrocket_token") or "").strip()


def _num(value: object) -> Decimal:
    """Parse JSON number/str safely (D() rejects float)."""
    if value is None:
        return Decimal("0")
    return D(str(value))


def _map_status(raw: str | None) -> str:
    s = (raw or "").lower()
    if s in {"paid", "completed", "success"}:
        return "paid"
    if s in {"expired", "cancelled", "canceled"}:
        return "expired"
    if s in {"active", "pending", "created"}:
        return "pending"
    if s in {"partially_paid", "partial"}:
        return "partial"
    return "pending"


def _unwrap(payload: dict[str, Any]) -> dict[str, Any]:
    if "data" in payload and isinstance(payload["data"], dict):
        return payload["data"]
    return payload


def _from_api(data: dict[str, Any], *, ttl_min: int = 60) -> GatewayInvoice:
    gid = data.get("id") if data.get("id") is not None else data.get("invoiceId")
    if gid is None:
        raise KeyError("xrocket invoice missing id")
    link = data.get("link") or data.get("paymentUrl") or data.get("url")
    if isinstance(link, dict):
        link = link.get("telegramBotLink") or link.get("webLink")
    exp = data.get("expiredIn") or data.get("expiresIn")
    if data.get("expired"):
        try:
            expires_at = datetime.fromisoformat(str(data["expired"]).replace("Z", "+00:00"))
        except Exception:
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_min)
    elif exp is not None:
        try:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(exp))
        except Exception:
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_min)
    else:
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_min)

    amount = data.get("amount")
    paid_usd = None
    if _map_status(data.get("status")) == "paid" and amount is not None:
        paid_usd = money(_num(amount))

    return GatewayInvoice(
        gateway_invoice_id=str(gid),
        status=_map_status(data.get("status")),  # type: ignore[arg-type]
        pay_url=str(link) if link else None,
        address=None,
        asset=str(data.get("currency") or "USDT"),
        network="xRocket",
        pay_amount=money(_num(amount)) if amount is not None else None,
        paid_usd=paid_usd,
        txid=None,
        expires_at=expires_at,
    )


class XrocketGateway:
    name = "xrocket"

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        token = _token()
        if not token:
            raise RuntimeError("xrocket_token not configured")
        headers = {"Rocket-Pay-Key": token, "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=30.0, base_url=BASE) as client:
            resp = await client.request(method, path, headers=headers, **kwargs)
            resp.raise_for_status()
            payload = resp.json()
        if not isinstance(payload, dict):
            raise RuntimeError("xRocket API: unexpected response")
        if payload.get("success") is False:
            raise RuntimeError(f"xRocket API error: {payload.get('message') or payload}")
        return payload

    async def create_invoice(
        self,
        *,
        order_ref: str,
        amount_usd: Decimal,
        ttl_min: int,
        network: str | None,
    ) -> GatewayInvoice:
        currency = (network or "USDT").upper()
        if currency in {"TON", "TONCOIN"}:
            currency = "TONCOIN"
        else:
            currency = "USDT"
        body = {
            "amount": float(money(amount_usd)),
            "currency": currency,
            "description": f"ARIX Cloud top-up #{order_ref}",
            "payload": str(order_ref)[:1024],
            "expiredIn": max(60, int(ttl_min) * 60),
            "commentsEnabled": False,
        }
        payload = await self._request("POST", "/tg-invoices", json=body)
        data = _unwrap(payload)
        inv = _from_api(data, ttl_min=ttl_min)
        log.info("xrocket_invoice_created", gid=inv.gateway_invoice_id, order_ref=order_ref)
        return inv

    async def get_invoice(self, gateway_invoice_id: str) -> GatewayInvoice:
        payload = await self._request("GET", f"/tg-invoices/{gateway_invoice_id}")
        data = _unwrap(payload)
        return _from_api(data)

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> WebhookEvent:
        token = _token()
        if not token:
            raise InvalidSignature("no token")
        sig = headers.get("rocket-pay-signature") or headers.get("Rocket-Pay-Signature")
        if not sig:
            raise InvalidSignature("missing signature")
        secret = hashlib.sha256(token.encode()).digest()
        expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected.lower(), str(sig).lower()):
            raise InvalidSignature("bad signature")
        data = json.loads(body.decode())
        if not isinstance(data, dict):
            raise InvalidSignature("bad body")
        invoice = data.get("data") if isinstance(data.get("data"), dict) else data
        if isinstance(invoice, dict) and isinstance(invoice.get("data"), dict) and "id" in invoice["data"]:
            invoice = invoice["data"]
        if not isinstance(invoice, dict):
            raise InvalidSignature("no invoice")
        gid = invoice.get("id") if invoice.get("id") is not None else invoice.get("invoiceId")
        if gid is None:
            raise InvalidSignature("no invoice id")
        order_ref = str(invoice.get("payload") or data.get("payload") or "")
        status = _map_status(invoice.get("status") or data.get("status"))
        paid_usd = None
        if status == "paid" and invoice.get("amount") is not None:
            paid_usd = money(_num(invoice["amount"]))
        return WebhookEvent(
            gateway_invoice_id=str(gid),
            order_ref=order_ref,
            status=status,
            paid_usd=paid_usd,
            txid=None,
            raw_hash=body_hash(body),
        )
