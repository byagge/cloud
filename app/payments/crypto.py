from __future__ import annotations

import io
from decimal import Decimal
from typing import Any

import httpx

from app.config import get_settings
from app.core.money import D, money
from app.core.settings_store import settings_store
from app.logging import get_logger

log = get_logger("payments.crypto")

# Default empty wallets — owner fills via /set_wallet
DEFAULT_WALLETS: list[dict[str, Any]] = [
    {"id": "USDT_TRC20", "label": "USDT TRC-20", "asset": "USDT", "network": "TRC20", "address": "", "enabled": False},
    {"id": "USDT_BEP20", "label": "USDT BEP-20", "asset": "USDT", "network": "BEP20", "address": "", "enabled": False},
    {"id": "TON", "label": "TON", "asset": "TON", "network": "TON", "address": "", "enabled": False},
    {"id": "USDT_TON", "label": "USDT TON", "asset": "USDT", "network": "TON", "address": "", "enabled": False},
    {"id": "BTC", "label": "BTC", "asset": "BTC", "network": "BTC", "address": "", "enabled": False},
]


def get_wallets() -> list[dict[str, Any]]:
    raw = settings_store.get("crypto_wallets")
    if isinstance(raw, list) and raw:
        return raw
    return [dict(w) for w in DEFAULT_WALLETS]


def enabled_wallets() -> list[dict[str, Any]]:
    return [w for w in get_wallets() if w.get("enabled") and w.get("address")]


def find_wallet(wallet_id: str) -> dict[str, Any] | None:
    for w in get_wallets():
        if w.get("id") == wallet_id:
            return w
    return None


def upsert_wallet(wallet_id: str, address: str, *, enabled: bool | None = None) -> list[dict[str, Any]]:
    wallets = [dict(w) for w in get_wallets()]
    addr = address.strip()
    found = False
    for w in wallets:
        if w.get("id") == wallet_id:
            w["address"] = addr
            w["enabled"] = bool(addr) if enabled is None else (bool(enabled) and bool(addr))
            found = True
            break
    if not found:
        base = next((dict(x) for x in DEFAULT_WALLETS if x["id"] == wallet_id), None)
        if base is None:
            base = {
                "id": wallet_id,
                "label": wallet_id,
                "asset": wallet_id.split("_")[0],
                "network": wallet_id.split("_")[-1],
            }
        base["address"] = addr
        base["enabled"] = bool(addr) if enabled is None else (bool(enabled) and bool(addr))
        wallets.append(base)
    return wallets


def set_wallet_enabled(wallet_id: str, enabled: bool) -> list[dict[str, Any]]:
    """Toggle payment method without clearing the address."""
    wallets = [dict(w) for w in get_wallets()]
    for w in wallets:
        if w.get("id") == wallet_id:
            addr = (w.get("address") or "").strip()
            w["enabled"] = bool(enabled) and bool(addr)
            break
    return wallets


def pay_amount_for(asset: str, amount_usd: Decimal) -> Decimal:
    """Stablecoins 1:1. Others: placeholder rate until admin rate feed."""
    asset = asset.upper()
    if asset in {"USDT", "USDC", "USD"}:
        return money(amount_usd)
    if asset == "TON":
        # ASSUMPTION: ~$5/TON for display; admin can override via setting ton_usd
        rate = D(settings_store.get("ton_usd_rate") or "5")
        return money(amount_usd / rate)
    if asset == "BTC":
        rate = D(settings_store.get("btc_usd_rate") or "90000")
        return (amount_usd / rate).quantize(Decimal("0.00000001"))
    return money(amount_usd)


def make_qr_png(data: str) -> bytes:
    import qrcode

    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def check_incoming(
    *,
    network: str,
    asset: str,
    address: str,
    min_amount: Decimal,
    since_ts: float,
) -> bool:
    """Return True if a qualifying transfer is found."""
    settings = get_settings()
    if settings.is_dev and (not address or address.upper().startswith("TEST")):
        # In dev, empty/TEST wallets: treat Check as success once
        return True

    network_u = network.upper()
    asset_u = asset.upper()
    try:
        if network_u == "TRC20" and asset_u == "USDT":
            return await _check_tron_usdt(address, min_amount, since_ts)
        if network_u == "BEP20" and asset_u == "USDT":
            return await _check_bsc_usdt(address, min_amount, since_ts)
        if network_u == "TON":
            return await _check_ton(address, asset_u, min_amount, since_ts)
        if network_u == "BTC" and asset_u == "BTC":
            return await _check_btc(address, min_amount, since_ts)
    except Exception:
        log.exception("crypto_check_failed", network=network, asset=asset)
    return False


async def _check_tron_usdt(address: str, min_amount: Decimal, since_ts: float) -> bool:
    # TronGrid public API — USDT TRC20 contract
    usdt = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    url = f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20"
    params = {"only_to": "true", "limit": 30, "contract_address": usdt}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    for tx in data.get("data") or []:
        ts = (tx.get("block_timestamp") or 0) / 1000
        if ts < since_ts - 60:
            continue
        raw = D(tx.get("value") or "0")
        decimals = int(tx.get("token_info", {}).get("decimals") or 6)
        amount = raw / (Decimal(10) ** decimals)
        if amount + Decimal("0.000001") >= min_amount:
            return True
    return False


async def _check_bsc_usdt(address: str, min_amount: Decimal, since_ts: float) -> bool:
    # Public BscScan-less fallback via public RPC is heavy; use moralis-free endpoint style
    # Simpler: eth_getLogs not here — use bscscan if key in settings, else False in prod
    api_key = str(settings_store.get("bscscan_api_key") or "")
    if not api_key:
        if get_settings().is_dev:
            return False
        return False
    usdt = "0x55d398326f99059fF775485246999027B3197955"
    url = "https://api.bscscan.com/api"
    params = {
        "module": "account",
        "action": "tokentx",
        "contractaddress": usdt,
        "address": address,
        "page": 1,
        "offset": 30,
        "sort": "desc",
        "apikey": api_key,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    for tx in data.get("result") or []:
        if (tx.get("to") or "").lower() != address.lower():
            continue
        ts = float(tx.get("timeStamp") or 0)
        if ts < since_ts - 60:
            continue
        amount = D(tx.get("value") or "0") / Decimal(10 ** int(tx.get("tokenDecimal") or 18))
        if amount + Decimal("0.000001") >= min_amount:
            return True
    return False


async def _check_ton(address: str, asset: str, min_amount: Decimal, since_ts: float) -> bool:
    url = f"https://toncenter.com/api/v2/getTransactions"
    params = {"address": address, "limit": 20}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, params=params)
        if r.status_code >= 400:
            return False
        data = r.json()
    for tx in data.get("result") or []:
        utime = float(tx.get("utime") or 0)
        if utime < since_ts - 60:
            continue
        in_msg = tx.get("in_msg") or {}
        value_nano = D(in_msg.get("value") or "0")
        amount = value_nano / Decimal("1000000000")
        if asset == "TON" and amount + Decimal("0.001") >= min_amount:
            return True
    return False


async def _check_btc(address: str, min_amount: Decimal, since_ts: float) -> bool:
    url = f"https://blockstream.info/api/address/{address}/txs"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url)
        if r.status_code >= 400:
            return False
        txs = r.json()
    for tx in txs[:20]:
        status = tx.get("status") or {}
        ts = float(status.get("block_time") or 0)
        if ts and ts < since_ts - 60:
            continue
        for vout in tx.get("vout") or []:
            if address not in (vout.get("scriptpubkey_address") or ""):
                # also check scriptpubkey_address field
                if vout.get("scriptpubkey_address") != address:
                    continue
            amount = D(vout.get("value") or 0) / Decimal("100000000")
            if amount + Decimal("0.00000001") >= min_amount:
                return True
    return False
