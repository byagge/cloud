from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.money import D, money
from app.partner.base import (
    CreatedServer,
    OsImage,
    PartnerError,
    PartnerErrorCategory,
    PartnerServer,
    Ping,
    Plan,
    Renewed,
    Script,
)

CODE_CATEGORY: dict[str, PartnerErrorCategory] = {
    "invalid_request": PartnerErrorCategory.FATAL_REQUEST,
    "unauthorized": PartnerErrorCategory.FATAL_SERVICE,
    "token_invalid": PartnerErrorCategory.FATAL_SERVICE,
    "insufficient_funds": PartnerErrorCategory.WAIT_FUNDS,
    "forbidden": PartnerErrorCategory.FATAL_SERVICE,
    "account_banned": PartnerErrorCategory.FATAL_SERVICE,
    "not_found": PartnerErrorCategory.NOT_FOUND,
    "conflict": PartnerErrorCategory.STATE_CONFLICT,
    "server_busy": PartnerErrorCategory.BUSY,
    "rent_expired": PartnerErrorCategory.EXPIRED,
    "idempotency_key_reused": PartnerErrorCategory.BUG,
    "rate_limited": PartnerErrorCategory.RATE_LIMITED,
    "internal_error": PartnerErrorCategory.RETRY,
    "service_unavailable": PartnerErrorCategory.RETRY,
}


def map_error(
    *,
    code: str,
    http: int,
    message: str = "",
    details: dict | None = None,
    request_id: str | None = None,
    retry_after: int | None = None,
    ambiguous: bool = False,
) -> PartnerError:
    category = CODE_CATEGORY.get(code, PartnerErrorCategory.RETRY)
    return PartnerError(
        code=code,
        http=http,
        category=category,
        message=message,
        details=details,
        request_id=request_id,
        retry_after=retry_after,
        ambiguous=ambiguous,
    )


def _dec(value: Any, default: Decimal | None = None) -> Decimal | None:
    if value is None:
        return default
    try:
        return money(D(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=datetime.now().astimezone().tzinfo)
    s = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def parse_ping(data: dict[str, Any]) -> Ping:
    return Ping(account_id=data.get("account_id"), server_time=data.get("server_time"))


def parse_plans(data: Any) -> list[Plan]:
    # ASSUMPTION: response is list or {"plans": [...]}
    items = data if isinstance(data, list) else data.get("plans") or data.get("items") or []
    out: list[Plan] = []
    for raw in items:
        pid = str(raw.get("id") or raw.get("plan_id") or "")
        if not pid:
            continue
        prices: dict[int, Decimal] = {}
        # ASSUMPTION: prices as { "1": "9.60", ... } or periods list
        price_map = raw.get("prices") or raw.get("price") or raw.get("periods") or {}
        if isinstance(price_map, dict):
            for k, v in price_map.items():
                try:
                    prices[int(k)] = money(D(v))
                except (InvalidOperation, TypeError, ValueError):
                    continue
        elif isinstance(price_map, list):
            for item in price_map:
                months = item.get("months") or item.get("period")
                cost = item.get("price_usd") or item.get("price") or item.get("cost")
                if months is not None and cost is not None:
                    prices[int(months)] = money(D(cost))
        monthly = raw.get("price_monthly") or raw.get("monthly_usd")
        if monthly is not None and 1 not in prices:
            prices[1] = money(D(monthly))
        out.append(
            Plan(
                id=pid,
                name=str(raw.get("name") or raw.get("title") or pid),
                cpu=int(raw.get("cpu") or raw.get("vcpu") or 1),
                ram_mb=int(raw.get("ram_mb") or raw.get("ram") or 1024),
                disk_gb=int(raw.get("disk_gb") or raw.get("disk") or 20),
                prices=prices,
                available=bool(raw.get("available", True)),
            )
        )
    return out


def parse_os_list(data: Any) -> list[OsImage]:
    items = data if isinstance(data, list) else data.get("os") or data.get("items") or []
    out: list[OsImage] = []
    for raw in items:
        oid = str(raw.get("id") or raw.get("os_id") or "")
        if not oid:
            continue
        out.append(OsImage(id=oid, name=str(raw.get("name") or oid)))
    return out


def parse_created(data: dict[str, Any]) -> CreatedServer:
    expires = _dt(data.get("rent_expires_at") or data.get("expires_at"))
    if expires is None:
        raise ValueError("rent_expires_at missing")  # ASSUMPTION
    charged = _dec(data.get("charged_usd") or data.get("charged"))
    if charged is None:
        raise ValueError("charged_usd missing")
    return CreatedServer(
        id=str(data.get("id") or data.get("server_id")),
        name=str(data.get("name") or ""),
        os=data.get("os"),
        location=data.get("location"),
        login=data.get("login") or data.get("username"),
        password=data.get("password"),
        rent_expires_at=expires,
        charged_usd=charged,
        balance_usd=_dec(data.get("balance_usd")),
    )


def parse_server(data: dict[str, Any]) -> PartnerServer:
    renew_prices: dict[int, Decimal] = {}
    # ASSUMPTION OPEN-3: renew prices shape
    raw_prices = (
        data.get("renew_prices")
        or data.get("renewal_prices")
        or data.get("prices_renew")
        or {}
    )
    if isinstance(raw_prices, dict):
        for k, v in raw_prices.items():
            try:
                renew_prices[int(k)] = money(D(v))
            except (InvalidOperation, TypeError, ValueError):
                continue
    return PartnerServer(
        id=str(data.get("id") or data.get("server_id")),
        name=str(data.get("name") or ""),
        state=data.get("state") or data.get("status"),
        ip=data.get("ip") or data.get("ipv4"),
        cpu=data.get("cpu"),
        ram_mb=data.get("ram_mb") or data.get("ram"),
        disk_gb=data.get("disk_gb") or data.get("disk"),
        rent_expires_at=_dt(data.get("rent_expires_at") or data.get("expires_at")),
        renew_prices=renew_prices,
        os=data.get("os"),
        location=data.get("location"),
        login=data.get("login"),
    )


def parse_servers(data: Any) -> list[PartnerServer]:
    items = data if isinstance(data, list) else data.get("servers") or data.get("items") or []
    return [parse_server(x) for x in items]


def parse_renewed(data: dict[str, Any]) -> Renewed:
    expires = _dt(data.get("rent_expires_at") or data.get("expires_at"))
    if expires is None:
        raise ValueError("rent_expires_at missing")
    charged = _dec(data.get("charged_usd") or data.get("charged"))
    if charged is None:
        raise ValueError("charged_usd missing")
    return Renewed(rent_expires_at=expires, charged_usd=charged, balance_usd=_dec(data.get("balance_usd")))


def parse_password(data: dict[str, Any]) -> str | None:
    # ASSUMPTION OPEN-2
    for key in ("password", "new_password", "root_password"):
        val = data.get(key)
        if isinstance(val, str) and len(val) >= 8:
            return val
    for key, val in data.items():
        if "pass" in key.lower() and isinstance(val, str) and len(val) >= 8:
            return val
    return None


def parse_scripts(data: Any) -> list[Script]:
    items = data if isinstance(data, list) else data.get("scripts") or data.get("items") or []
    out: list[Script] = []
    for raw in items:
        sid = str(raw.get("id") or raw.get("script_id") or "")
        if not sid:
            continue
        out.append(
            Script(
                id=sid,
                name=str(raw.get("name") or sid),
                description=raw.get("description"),
            )
        )
    return out
