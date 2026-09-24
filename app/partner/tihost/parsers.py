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


def _month_key(key: Any) -> int | None:
    if key is None:
        return None
    if isinstance(key, int):
        return key if key in {1, 3, 6, 12} or key > 0 else None
    s = str(key).strip().lower().replace(" ", "")
    aliases = {
        "1": 1,
        "1m": 1,
        "1mo": 1,
        "1month": 1,
        "month": 1,
        "monthly": 1,
        "3": 3,
        "3m": 3,
        "3mo": 3,
        "3month": 3,
        "quarter": 3,
        "quarterly": 3,
        "6": 6,
        "6m": 6,
        "6mo": 6,
        "6month": 6,
        "semi": 6,
        "12": 12,
        "12m": 12,
        "12mo": 12,
        "12month": 12,
        "year": 12,
        "yearly": 12,
        "annual": 12,
    }
    if s in aliases:
        return aliases[s]
    try:
        n = int(s)
        return n if n > 0 else None
    except ValueError:
        return None


def _ingest_price_dict(pm: dict[str, Any], prices: dict[int, Decimal]) -> None:
    for k, v in pm.items():
        months = _month_key(k)
        if months is None:
            continue
        if isinstance(v, dict):
            amount = (
                v.get("total_usd")
                or v.get("price_usd")
                or v.get("price")
                or v.get("cost")
                or v.get("amount")
                or v.get("usd")
            )
        else:
            amount = v
        val = _dec(amount)
        if val is not None:
            prices[months] = val


def _ingest_price_list(items: list[Any], prices: dict[int, Decimal]) -> None:
    for item in items:
        if not isinstance(item, dict):
            continue
        months = _month_key(
            item.get("months")
            or item.get("period")
            or item.get("term")
            or item.get("duration")
            or item.get("billing_cycle")
            or item.get("cycle")
        )
        amount = (
            item.get("total_usd")  # Tihost catalog: periods[].total_usd
            or item.get("price_usd")
            or item.get("price")
            or item.get("cost")
            or item.get("amount")
            or item.get("usd")
            or item.get("cost_usd")
        )
        if months is None or amount is None:
            continue
        val = _dec(amount)
        if val is not None:
            prices[months] = val


def _extract_plan_prices(raw: dict[str, Any]) -> dict[int, Decimal]:
    """Parse partner cost by months from many possible Tihost shapes."""
    prices: dict[int, Decimal] = {}

    # Nested maps / lists first
    for key in ("prices", "periods", "pricing", "tariffs", "costs", "price_list"):
        pm = raw.get(key)
        if isinstance(pm, dict):
            _ingest_price_dict(pm, prices)
        elif isinstance(pm, list):
            _ingest_price_list(pm, prices)

    # `price` as map
    if isinstance(raw.get("price"), dict):
        _ingest_price_dict(raw["price"], prices)
    elif isinstance(raw.get("price"), list):
        _ingest_price_list(raw["price"], prices)

    # Scalar monthly fields (do NOT use via `or` with dict — that broke parsing)
    if 1 not in prices:
        for key in (
            "price_monthly",
            "monthly_usd",
            "monthly",
            "price_usd",
            "cost_usd",
            "cost",
            "amount",
            "usd",
        ):
            if key in raw and not isinstance(raw[key], (dict, list)):
                val = _dec(raw[key])
                if val is not None:
                    prices[1] = val
                    break
        if 1 not in prices and "price" in raw and not isinstance(raw["price"], (dict, list)):
            val = _dec(raw["price"])
            if val is not None:
                prices[1] = val

    # Fill 3/6/12 from monthly if partner only sends 1 month (same discounts as FakePartner)
    if 1 in prices:
        monthly = prices[1]
        for months, disc in ((3, Decimal("0.95")), (6, Decimal("0.90")), (12, Decimal("0.80"))):
            if months not in prices:
                prices[months] = money(monthly * months * disc)

    return prices


def parse_plans(data: Any) -> list[Plan]:
    if isinstance(data, dict) and "data" in data and not (data.get("plans") or data.get("items")):
        data = data["data"]
    items = data if isinstance(data, list) else (data.get("plans") or data.get("items") or data.get("tariffs") or [])
    out: list[Plan] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        pid = str(raw.get("id") or raw.get("plan_id") or raw.get("slug") or "")
        if not pid:
            continue
        prices = _extract_plan_prices(raw)
        if not prices:
            log_msg = f"plan {pid} has no parseable prices; keys={list(raw.keys())}"
            try:
                from app.logging import get_logger

                get_logger("partner.tihost").warning(log_msg)
            except Exception:
                pass
        ram = raw.get("ram_mb") or raw.get("ram") or raw.get("memory_mb") or 1024
        if isinstance(ram, str) and ram.lower().endswith("gb"):
            try:
                ram = int(float(ram[:-2]) * 1024)
            except ValueError:
                ram = 1024
        disk = raw.get("disk_gb") or raw.get("disk") or raw.get("ssd_gb") or 20
        if isinstance(disk, str) and disk.lower().endswith("gb"):
            try:
                disk = int(float(disk[:-2]))
            except ValueError:
                disk = 20
        out.append(
            Plan(
                id=pid,
                name=str(raw.get("name") or raw.get("title") or pid),
                cpu=int(raw.get("cpu") or raw.get("vcpu") or raw.get("cores") or 1),
                ram_mb=int(ram),
                disk_gb=int(disk),
                prices=prices,
                available=bool(raw.get("available", True)),
                tier=int(raw.get("tier") or 1),
                cpu_model=str(raw.get("cpu_model") or raw.get("processor") or "AMD Ryzen 9 5950X"),
                bandwidth=str(raw.get("bandwidth") or raw.get("network") or "1 Гбит/с"),
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
    raw_prices = (
        data.get("renew_prices")
        or data.get("renewal_prices")
        or data.get("prices_renew")
        or {}
    )
    # Tihost: [{ "days": 30, "total_usd": "4.00" }, ...]
    if isinstance(raw_prices, list):
        for item in raw_prices:
            if not isinstance(item, dict):
                continue
            days = item.get("days") or item.get("period") or item.get("term")
            amount = item.get("total_usd") or item.get("price_usd") or item.get("price") or item.get("amount")
            if days is None or amount is None:
                continue
            try:
                renew_prices[int(days)] = money(D(amount))
            except (InvalidOperation, TypeError, ValueError):
                continue
    elif isinstance(raw_prices, dict):
        for k, v in raw_prices.items():
            try:
                if isinstance(v, dict):
                    amount = v.get("total_usd") or v.get("price_usd") or v.get("price") or v.get("amount")
                else:
                    amount = v
                renew_prices[int(k)] = money(D(amount))
            except (InvalidOperation, TypeError, ValueError):
                continue
    resources = data.get("resources") if isinstance(data.get("resources"), dict) else {}
    return PartnerServer(
        id=str(data.get("id") or data.get("server_id")),
        name=str(data.get("name") or ""),
        state=data.get("state") or data.get("status"),
        ip=data.get("ip") or data.get("ipv4"),
        cpu=data.get("cpu") or resources.get("cpu"),
        ram_mb=data.get("ram_mb") or data.get("ram") or resources.get("ram_mb"),
        disk_gb=data.get("disk_gb") or data.get("disk") or resources.get("disk_gb"),
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
