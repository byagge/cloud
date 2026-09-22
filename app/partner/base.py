from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.compat import StrEnum


class PartnerErrorCategory(StrEnum):
    RETRY = "RETRY"
    WAIT_FUNDS = "WAIT_FUNDS"
    RATE_LIMITED = "RATE_LIMITED"
    FATAL_SERVICE = "FATAL_SERVICE"
    FATAL_REQUEST = "FATAL_REQUEST"
    NOT_FOUND = "NOT_FOUND"
    STATE_CONFLICT = "STATE_CONFLICT"
    BUSY = "BUSY"
    EXPIRED = "EXPIRED"
    BUG = "BUG"


class PartnerError(Exception):
    def __init__(
        self,
        *,
        code: str,
        http: int,
        category: PartnerErrorCategory,
        message: str = "",
        details: dict | None = None,
        request_id: str | None = None,
        ambiguous: bool = False,
        retry_after: int | None = None,
    ) -> None:
        self.code = code
        self.http = http
        self.category = category
        self.message = message
        self.details = details or {}
        self.request_id = request_id
        self.ambiguous = ambiguous
        self.retry_after = retry_after
        super().__init__(f"{code} [{category}] {message}")


class Ping(BaseModel):
    model_config = ConfigDict(extra="allow")
    account_id: str | int | None = None
    server_time: datetime | str | None = None


class Plan(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str = ""
    cpu: int = 1
    ram_mb: int = 1024
    disk_gb: int = 20
    # months -> partner cost USD
    prices: dict[int, Decimal] = Field(default_factory=dict)
    available: bool = True
    tier: int = 1
    cpu_model: str = "AMD Ryzen 9 5950X"
    bandwidth: str = "1 Гбит/с"


class OsImage(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str


class CreatedServer(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    os: str | None = None
    location: str | None = None
    login: str | None = None
    password: str | None = None
    rent_expires_at: datetime
    charged_usd: Decimal
    balance_usd: Decimal | None = None


class PartnerServer(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    state: str | None = None
    ip: str | None = None
    cpu: int | None = None
    ram_mb: int | None = None
    disk_gb: int | None = None
    rent_expires_at: datetime | None = None
    renew_prices: dict[int, Decimal] = Field(default_factory=dict)
    os: str | None = None
    location: str | None = None
    login: str | None = None


class Renewed(BaseModel):
    model_config = ConfigDict(extra="allow")
    rent_expires_at: datetime
    charged_usd: Decimal
    balance_usd: Decimal | None = None


class Script(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    description: str | None = None


class PartnerAdapter(Protocol):
    async def ping(self) -> Ping: ...
    async def balance(self) -> Decimal: ...
    async def plans(self, location: str) -> list[Plan]: ...
    async def os_list(self, location: str, plan_id: str) -> list[OsImage]: ...
    async def create_server(
        self,
        *,
        location: str,
        plan_id: str,
        os_id: str,
        months: int,
        name: str,
        idem_key: str,
    ) -> CreatedServer: ...
    async def list_servers(self) -> list[PartnerServer]: ...
    async def get_server(self, partner_id: str) -> PartnerServer: ...
    async def renew(self, partner_id: str, days: int, idem_key: str) -> Renewed: ...
    async def set_auto_renew(self, partner_id: str, enabled: bool, idem_key: str) -> None: ...
    async def power(
        self, partner_id: str, action: Literal["start", "stop", "restart"], idem_key: str
    ) -> None: ...
    async def reset_password(
        self, partner_id: str, password: str | None, idem_key: str
    ) -> str: ...
    async def os_for_reinstall(self, partner_id: str) -> list[OsImage]: ...
    async def reinstall(self, partner_id: str, os_id: str, idem_key: str) -> None: ...
    async def scripts(self, partner_id: str) -> list[Script]: ...
    async def run_script(self, partner_id: str, script_id: str, idem_key: str) -> None: ...
