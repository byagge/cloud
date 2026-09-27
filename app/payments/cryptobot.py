"""Crypto Bot (Crypto Pay API) payment gateway."""

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

log = get_logger("payments.cryptobot")

MAINNET = "https://pay.crypt.bot/api"
TESTNET = "https://testnet-pay.crypt.bot/api"


def _token() -> str:
    return str(settings_store.get("cryptobot_token") or "").strip()


def _base_url() -> str:
    if settings_store.bool("cryptobot_testnet"):
        return TESTNET
    return MAINNET


def _num(value: object) -> Decimal:
    """Parse JSON number/str safely (D() rejects float)."""
    if value is None:
        return Decimal("0")
    return D(str(value))


def _map_status(raw: str | None) -> str:
    s = (raw or "").lower()
    if s == "paid":
        return "paid"
    if s == "expired":
        return "expired"
    if s in {"active", "pending"}:
        return "pending"
    return "pending"


def _parse_expires(data: dict[str, Any], ttl_min: int) -> datetime:
    exp = data.get("expiration_date") or data.get("expiry_date")
    if exp:
        try:
            return datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
        except Exception:
            pass
    return datetime.now(timezone.utc) + timedelta(minutes=ttl_min)


def _from_api(data: dict[str, Any], *, ttl_min: int = 60) -> GatewayInvoice:
    if not data or data.get("invoice_id") is None:
        raise KeyError("cryptobot invoice missing invoice_id")
    paid_amount = data.get("paid_amount") if data.get("paid_amount") is not None else data.get("amount")
    paid_usd = None
    if data.get("status") == "paid":
        if data.get("currency_type") == "fiat" or data.get("fiat"):
            paid_usd = money(_num(data.get("amount") if data.get("amount") is not None else paid_amount))
        elif data.get("paid_usd_rate") is not None and paid_amount is not None:
            paid_usd = money(_num(paid_amount) * _num(data["paid_usd_rate"]))
        else:
            paid_usd = money(_num(data.get("amount")))
    return GatewayInvoice(
        gateway_invoice_id=str(data["invoice_id"]),
        status=_map_status(data.get("status")),  # type: ignore[arg-type]
        pay_url=(
            data.get("bot_invoice_url")
            or data.get("pay_url")
            or data.get("mini_app_invoice_url")
        ),
        address=None,
        asset=data.get("asset") or data.get("fiat") or "USD",
        network="CryptoBot",
        pay_amount=money(_num(data["amount"])) if data.get("amount") is not None else None,
        paid_usd=paid_usd,
        txid=None,
        expires_at=_parse_expires(data, ttl_min),
    )


class CryptoBotGateway:
    name = "cryptobot"

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        token = _token()
        if not token:
            raise RuntimeError("cryptobot_token not configured")
        headers = {"Crypto-Pay-API-Token": token}
        async with httpx.AsyncClient(timeout=30.0, base_url=_base_url()) as client:
            resp = await client.request(method, path, headers=headers, **kwargs)
            resp.raise_for_status()
            payload = resp.json()
        if not payload.get("ok"):
            err = payload.get("error") or payload
            raise RuntimeError(f"CryptoBot API error: {err}")
        return payload.get("result")

    async def create_invoice(
        self,
        *,
        order_ref: str,
        amount_usd: Decimal,
        ttl_min: int,
        network: str | None,
    ) -> GatewayInvoice:
        body = {
            "currency_type": "fiat",
            "fiat": "USD",
            "amount": f"{money(amount_usd):.2f}",
            "description": f"ARIX Cloud top-up #{order_ref}",
            "payload": str(order_ref)[:4096],
            "expires_in": max(60, min(int(ttl_min) * 60, 2678400)),
            "allow_comments": False,
            "allow_anonymous": True,
        }
        result = await self._request("POST", "/createInvoice", json=body)
        if not isinstance(result, dict):
            raise RuntimeError("CryptoBot createInvoice: unexpected response")
        inv = _from_api(result, ttl_min=ttl_min)
        log.info("cryptobot_invoice_created", gid=inv.gateway_invoice_id, order_ref=order_ref)
        return inv

    async def get_invoice(self, gateway_invoice_id: str) -> GatewayInvoice:
        result = await self._request(
            "GET",
            "/getInvoices",
            params={"invoice_ids": str(gateway_invoice_id)},
        )
        items: list[Any] | None
        if isinstance(result, list):
            items = result
        elif isinstance(result, dict):
            raw = result.get("items")
            items = raw if isinstance(raw, list) else None
        else:
            items = None
        if not items:
            raise KeyError(gateway_invoice_id)
        first = items[0]
        if not isinstance(first, dict):
            raise KeyError(gateway_invoice_id)
        return _from_api(first)

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> WebhookEvent:
        token = _token()
        if not token:
            raise InvalidSignature("no token")
        sig = headers.get("crypto-pay-api-signature") or headers.get("Crypto-Pay-Api-Signature")
        if not sig:
            raise InvalidSignature("missing signature")
        secret = hashlib.sha256(token.encode()).digest()
        expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected.lower(), str(sig).lower()):
            raise InvalidSignature("bad signature")
        data = json.loads(body.decode())
        payload = data.get("payload") or data
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = data
        if not isinstance(payload, dict):
            payload = data if isinstance(data, dict) else {}
        invoice_id = payload.get("invoice_id") or data.get("invoice_id")
        if invoice_id is None:
            raise InvalidSignature("no invoice_id")
        order_ref = str(payload.get("payload") or "")
        status = (
            "paid"
            if data.get("update_type") == "invoice_paid"
            else _map_status(payload.get("status"))
        )
        paid_usd = None
        if payload.get("amount") is not None and (
            payload.get("currency_type") == "fiat" or payload.get("fiat")
        ):
            paid_usd = money(_num(payload["amount"]))
        return WebhookEvent(
            gateway_invoice_id=str(invoice_id),
            order_ref=order_ref,
            status=status,
            paid_usd=paid_usd,
            txid=None,
            raw_hash=body_hash(body),
        )
