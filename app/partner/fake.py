from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import uuid4

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

LOCATIONS = ("germany", "finland", "poland")

LOC_META = {
    "germany": {"cpu_model": "AMD Ryzen 9 5950X", "bandwidth": "1 Гбит/с", "flag": "🇩🇪"},
    "finland": {"cpu_model": "AMD EPYC 7543", "bandwidth": "1 Гбит/с", "flag": "🇫🇮"},
    "poland": {"cpu_model": "AMD Ryzen 9 5950X", "bandwidth": "1 Гбит/с", "flag": "🇵🇱"},
}

# Partner cost bases (USD/month) from API/catalog. Client = client_price(base, markup, fee).
# Cheapest partner ≈ $4 → with default markup 2.2 / fee 2% → client $9.
_PLANS: list[dict] = [
    {"id": "t1", "name": "Storm 1", "cpu": 1, "ram_mb": 2048, "disk_gb": 40, "base": "4.00", "tier": 1},
    {"id": "t2", "name": "Storm 2", "cpu": 2, "ram_mb": 4096, "disk_gb": 80, "base": "7.70", "tier": 1},
    {"id": "t3", "name": "Storm 4", "cpu": 4, "ram_mb": 4096, "disk_gb": 140, "base": "12.30", "tier": 1},
    {"id": "t4", "name": "Bolt 4", "cpu": 4, "ram_mb": 8192, "disk_gb": 180, "base": "15.40", "tier": 2},
    {"id": "t5", "name": "Bolt 6", "cpu": 6, "ram_mb": 12288, "disk_gb": 240, "base": "24.00", "tier": 2},
    {"id": "t6", "name": "Bolt 8", "cpu": 8, "ram_mb": 12288, "disk_gb": 260, "base": "28.00", "tier": 2},
    {"id": "t7", "name": "Fire 8", "cpu": 8, "ram_mb": 16384, "disk_gb": 300, "base": "32.00", "tier": 3},
    {"id": "t8", "name": "Fire 12", "cpu": 12, "ram_mb": 24576, "disk_gb": 350, "base": "49.00", "tier": 3},
]

_OS: list[OsImage] = [
    OsImage(id="win10", name="Windows 10"),
    OsImage(id="win10-rus", name="Windows 10 RUS"),
    OsImage(id="win11", name="Windows 11"),
    OsImage(id="wins2012", name="Windows Server 2012"),
    OsImage(id="wins2016", name="Windows Server 2016"),
    OsImage(id="wins2019", name="Windows Server 2019"),
    OsImage(id="wins2019-rus", name="Windows Server 2019 RUS"),
    OsImage(id="wins2022", name="Windows Server 2022"),
    OsImage(id="alma8", name="Alma Linux 8"),
    OsImage(id="alma9", name="Alma Linux 9"),
    OsImage(id="alma10", name="Alma Linux 10"),
    OsImage(id="debian11", name="Debian 11"),
    OsImage(id="debian12", name="Debian 12"),
    OsImage(id="debian13", name="Debian 13"),
    OsImage(id="rocky8", name="Rocky Linux 8"),
    OsImage(id="rocky9", name="Rocky Linux 9"),
    OsImage(id="rocky10", name="Rocky Linux 10"),
    OsImage(id="ubuntu-20.04", name="Ubuntu 20.04"),
    OsImage(id="ubuntu-22.04", name="Ubuntu 22.04"),
    OsImage(id="ubuntu-24.04", name="Ubuntu 24.04"),
    OsImage(id="oracle8", name="Oracle Linux 8"),
    OsImage(id="oracle9", name="Oracle Linux 9"),
    OsImage(id="centos9", name="CentOS Stream 9"),
    OsImage(id="freebsd13", name="FreeBSD 13"),
]

_MONTH_DISCOUNT = {1: Decimal("1"), 3: Decimal("0.95"), 6: Decimal("0.90"), 12: Decimal("0.80")}

TIER_EMOJI = {1: "🌪", 2: "⚡", 3: "🔥"}


def os_group(name: str) -> str:
    n = name.lower()
    if "windows" in n:
        return "Windows"
    if "alma" in n:
        return "Alma Linux"
    if "debian" in n:
        return "Debian"
    if "rocky" in n:
        return "Rocky"
    if "ubuntu" in n:
        return "Ubuntu"
    if "oracle" in n:
        return "Oracle"
    if "centos" in n:
        return "CentOS"
    if "freebsd" in n:
        return "FreeBSD"
    return "Other"


def os_group_emoji(group: str) -> str:
    """Unicode fallback (text); prefer os_group_icon for button icons."""
    return {
        "Windows": "🪟",
        "Alma Linux": "🌈",
        "Debian": "🍥",
        "Rocky": "🟢",
        "Ubuntu": "🟠",
        "Oracle": "🔴",
        "CentOS": "💠",
        "FreeBSD": "😈",
        "Other": "💿",
    }.get(group, "💿")


def os_group_icon(group: str) -> str:
    """PACK key for custom emoji logo on inline buttons."""
    return {
        "Windows": "os_windows",
        "Alma Linux": "os_alma",
        "Debian": "os_debian",
        "Rocky": "os_rocky",
        "Ubuntu": "os_ubuntu",
        "Oracle": "os_oracle",
        "CentOS": "os_centos",
        "FreeBSD": "os_freebsd",
        "Other": "os_other",
    }.get(group, "os_other")


def _gen_password(n: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


class FakePartner:
    def __init__(self, *, starting_balance: Decimal = Decimal("1000.00")) -> None:
        self._balance = money(starting_balance)
        self._servers: dict[str, dict] = {}

    async def ping(self) -> Ping:
        return Ping(account_id="fake-arix", server_time=datetime.now(timezone.utc).isoformat())

    async def balance(self) -> Decimal:
        return self._balance

    async def plans(self, location: str) -> list[Plan]:
        if location not in LOCATIONS:
            raise PartnerError(
                code="invalid_request",
                http=400,
                category=PartnerErrorCategory.FATAL_REQUEST,
                message="unknown location",
            )
        out: list[Plan] = []
        meta = LOC_META.get(location, {})
        for p in _PLANS:
            base = D(p["base"])
            prices = {m: money(base * m * disc) for m, disc in _MONTH_DISCOUNT.items()}
            plan = Plan(
                id=p["id"],
                name=p["name"],
                cpu=p["cpu"],
                ram_mb=p["ram_mb"],
                disk_gb=p["disk_gb"],
                prices=prices,
                tier=p["tier"],
                cpu_model=meta.get("cpu_model", "AMD Ryzen 9 5950X"),
                bandwidth=meta.get("bandwidth", "1 Гбит/с"),
            )
            out.append(plan)
        return out

    async def os_list(self, location: str, plan_id: str) -> list[OsImage]:
        return list(_OS)

    async def create_server(
        self,
        *,
        location: str,
        plan_id: str,
        os_id: str,
        months: int,
        name: str,
        idem_key: str,
    ) -> CreatedServer:
        existing = next((s for s in self._servers.values() if s.get("idem_key") == idem_key), None)
        if existing:
            return self._to_created(existing)

        plans = {p.id: p for p in await self.plans(location)}
        plan = plans.get(plan_id)
        if plan is None:
            raise PartnerError(
                code="invalid_request",
                http=400,
                category=PartnerErrorCategory.FATAL_REQUEST,
                message="unknown plan",
            )
        cost = plan.prices.get(months)
        if cost is None:
            raise PartnerError(
                code="invalid_request",
                http=400,
                category=PartnerErrorCategory.FATAL_REQUEST,
                message="bad months",
            )
        if self._balance < cost:
            raise PartnerError(
                code="insufficient_funds",
                http=402,
                category=PartnerErrorCategory.WAIT_FUNDS,
                details={"missing_usd": str(cost - self._balance)},
            )
        self._balance = money(self._balance - cost)
        sid = str(uuid4())
        password = _gen_password()
        os_name = next((o.name for o in _OS if o.id == os_id), os_id)
        row = {
            "id": sid,
            "name": name,
            "os": os_name,
            "location": location,
            "login": "root",
            "password": password,
            "rent_expires_at": datetime.now(timezone.utc) + timedelta(days=30 * months),
            "charged_usd": cost,
            "balance_usd": self._balance,
            "state": "running",
            "ip": f"203.0.113.{(len(self._servers) % 250) + 1}",
            "cpu": plan.cpu,
            "ram_mb": plan.ram_mb,
            "disk_gb": plan.disk_gb,
            "plan_id": plan_id,
            "idem_key": idem_key,
            "auto_renew": False,
            "renew_prices": {
                2: money(plan.prices[1] * Decimal("2") / Decimal("30")),
                7: money(plan.prices[1] * Decimal("7") / Decimal("30")),
                30: plan.prices[1],
                90: plan.prices[3],
                180: plan.prices[6],
                365: plan.prices[12],
            },
        }
        self._servers[sid] = row
        return self._to_created(row)

    def _to_created(self, row: dict) -> CreatedServer:
        return CreatedServer(
            id=row["id"],
            name=row["name"],
            os=row.get("os"),
            location=row.get("location"),
            login=row.get("login"),
            password=row.get("password"),
            rent_expires_at=row["rent_expires_at"],
            charged_usd=row["charged_usd"],
            balance_usd=row.get("balance_usd"),
        )

    async def list_servers(self) -> list[PartnerServer]:
        return [self._to_server(s) for s in self._servers.values()]

    async def get_server(self, partner_id: str) -> PartnerServer:
        return self._to_server(self._require(partner_id))

    def _to_server(self, row: dict) -> PartnerServer:
        return PartnerServer(
            id=row["id"],
            name=row["name"],
            state=row.get("state"),
            ip=row.get("ip"),
            cpu=row.get("cpu"),
            ram_mb=row.get("ram_mb"),
            disk_gb=row.get("disk_gb"),
            rent_expires_at=row.get("rent_expires_at"),
            renew_prices=row.get("renew_prices") or {},
            os=row.get("os"),
            location=row.get("location"),
            login=row.get("login"),
        )

    async def renew(self, partner_id: str, days: int, idem_key: str) -> Renewed:
        row = self._require(partner_id)
        prices = row.get("renew_prices") or {}
        cost = prices.get(days)
        if cost is None:
            raise PartnerError(
                code="invalid_request",
                http=400,
                category=PartnerErrorCategory.FATAL_REQUEST,
                message="bad days",
            )
        if self._balance < cost:
            raise PartnerError(
                code="insufficient_funds",
                http=402,
                category=PartnerErrorCategory.WAIT_FUNDS,
                details={"missing_usd": str(cost - self._balance)},
            )
        self._balance = money(self._balance - cost)
        base = row["rent_expires_at"]
        if base < datetime.now(timezone.utc):
            base = datetime.now(timezone.utc)
        row["rent_expires_at"] = base + timedelta(days=days)
        return Renewed(
            rent_expires_at=row["rent_expires_at"],
            charged_usd=cost,
            balance_usd=self._balance,
        )

    async def set_auto_renew(self, partner_id: str, enabled: bool, idem_key: str) -> None:
        self._require(partner_id)["auto_renew"] = enabled

    async def power(
        self, partner_id: str, action: Literal["start", "stop", "restart"], idem_key: str
    ) -> None:
        row = self._require(partner_id)
        row["state"] = "stopped" if action == "stop" else "running"

    async def reset_password(self, partner_id: str, password: str | None, idem_key: str) -> str:
        row = self._require(partner_id)
        pwd = password or _gen_password()
        row["password"] = pwd
        return pwd

    async def os_for_reinstall(self, partner_id: str) -> list[OsImage]:
        self._require(partner_id)
        return list(_OS)

    async def reinstall(self, partner_id: str, os_id: str, idem_key: str) -> None:
        row = self._require(partner_id)
        row["os"] = next((o.name for o in _OS if o.id == os_id), os_id)
        row["state"] = "running"

    async def scripts(self, partner_id: str) -> list[Script]:
        self._require(partner_id)
        return [
            Script(id="docker", name="Install Docker", description="Docker CE"),
            Script(id="nginx", name="Install Nginx", description="Nginx web server"),
        ]

    async def run_script(self, partner_id: str, script_id: str, idem_key: str) -> None:
        self._require(partner_id)

    def _require(self, partner_id: str) -> dict:
        row = self._servers.get(partner_id)
        if not row:
            raise PartnerError(
                code="not_found",
                http=404,
                category=PartnerErrorCategory.NOT_FOUND,
                message="server not found",
            )
        return row
