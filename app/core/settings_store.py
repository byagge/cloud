from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import D
from app.db.models import Setting

DEFAULTS: dict[str, object] = {
    "markup": "2.2",
    "gateway_fee": "0.02",
    "price_step": "0.10",
    "price_tolerance": "0.02",
    "min_topup": "5",
    "max_topup": "500",
    "max_servers_per_user": 10,
    "new_user_daily_purchases": 2,
    "partner_min_balance": "20",
    "alert_lookahead_days": 7,
    "maintenance": False,
    "enabled_networks": [],
    "draft_ttl_min": 30,
    "invoice_ttl_min": 60,
    "default_renew_days": 30,
    "reminder_days": [3, 1],
    "stuck_order_min": 5,
    "provision_delay_notify_min": 15,
    "crypto_wallets": [
        {"id": "USDT_TRC20", "label": "USDT TRC-20", "asset": "USDT", "network": "TRC20", "address": "", "enabled": False},
        {"id": "USDT_BEP20", "label": "USDT BEP-20", "asset": "USDT", "network": "BEP20", "address": "", "enabled": False},
        {"id": "TON", "label": "TON", "asset": "TON", "network": "TON", "address": "", "enabled": False},
        {"id": "USDT_TON", "label": "USDT TON", "asset": "USDT", "network": "TON", "address": "", "enabled": False},
        {"id": "BTC", "label": "BTC", "asset": "BTC", "network": "BTC", "address": "", "enabled": False},
    ],
    "ton_usd_rate": "5",
    "btc_usd_rate": "90000",
    "bscscan_api_key": "",
}


class SettingsStore:
    def __init__(self) -> None:
        self._cache: dict[str, object] = {}

    async def load(self, session: AsyncSession) -> None:
        rows = (await session.scalars(select(Setting))).all()
        data = dict(DEFAULTS)
        for row in rows:
            data[row.key] = row.value
        self._cache = data

    def get(self, key: str, default: object | None = None) -> object:
        if key in self._cache:
            return self._cache[key]
        return DEFAULTS.get(key, default)

    def decimal(self, key: str) -> Decimal:
        return D(self.get(key))

    def bool(self, key: str) -> bool:
        v = self.get(key)
        if isinstance(v, bool):
            return v
        return str(v).lower() in {"1", "true", "yes"}

    def int(self, key: str) -> int:
        return int(self.get(key))  # type: ignore[arg-type]

    async def set(self, session: AsyncSession, key: str, value: object, updated_by: int | None = None) -> None:
        row = await session.get(Setting, key)
        if row is None:
            row = Setting(key=key, value=value, updated_by=updated_by)
            session.add(row)
        else:
            row.value = value
            row.updated_by = updated_by
        self._cache[key] = value
        await session.flush()


settings_store = SettingsStore()
