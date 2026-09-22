from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from app.db.models import Server


class DisplayStatus(StrEnum):
    FROZEN = "frozen"
    MISSING = "missing"
    EXPIRED = "expired"
    PROVISIONING = "provisioning"
    RESTARTING = "restarting"
    ERROR = "error"
    STOPPED = "stopped"
    RUNNING = "running"
    UNKNOWN = "unknown"


STATUS_RU: dict[DisplayStatus, str] = {
    DisplayStatus.FROZEN: "Заморожен",
    DisplayStatus.MISSING: "Не найден у партнёра",
    DisplayStatus.EXPIRED: "Срок истёк",
    DisplayStatus.PROVISIONING: "Создаётся",
    DisplayStatus.RESTARTING: "Перезапускается",
    DisplayStatus.ERROR: "Ошибка",
    DisplayStatus.STOPPED: "Остановлен",
    DisplayStatus.RUNNING: "Работает",
    DisplayStatus.UNKNOWN: "Неизвестно",
}

STATUS_EN: dict[DisplayStatus, str] = {
    DisplayStatus.FROZEN: "Frozen",
    DisplayStatus.MISSING: "Missing at partner",
    DisplayStatus.EXPIRED: "Expired",
    DisplayStatus.PROVISIONING: "Provisioning",
    DisplayStatus.RESTARTING: "Restarting",
    DisplayStatus.ERROR: "Error",
    DisplayStatus.STOPPED: "Stopped",
    DisplayStatus.RUNNING: "Running",
    DisplayStatus.UNKNOWN: "Unknown",
}

STATUS_DOT: dict[DisplayStatus, str] = {
    DisplayStatus.RUNNING: "🟢",
    DisplayStatus.STOPPED: "⚪",
    DisplayStatus.UNKNOWN: "⚪",
    DisplayStatus.PROVISIONING: "🟡",
    DisplayStatus.RESTARTING: "🟡",
    DisplayStatus.ERROR: "🔴",
    DisplayStatus.EXPIRED: "🔴",
    DisplayStatus.MISSING: "🔴",
    DisplayStatus.FROZEN: "🔴",
}

ACTIONS: dict[DisplayStatus, set[str]] = {
    DisplayStatus.RUNNING: {"stop", "restart", "renew", "password", "reinstall", "scripts"},
    DisplayStatus.STOPPED: {"start", "renew", "password", "reinstall"},
    DisplayStatus.PROVISIONING: set(),
    DisplayStatus.RESTARTING: {"renew"},
    DisplayStatus.ERROR: {"stop", "restart", "renew", "password", "reinstall"},
    DisplayStatus.EXPIRED: {"renew"},
    DisplayStatus.FROZEN: set(),
    DisplayStatus.MISSING: set(),
    DisplayStatus.UNKNOWN: {"renew"},
}


def display_status(server: Server, now: datetime | None = None) -> DisplayStatus:
    now = now or datetime.now(timezone.utc)
    if server.frozen:
        return DisplayStatus.FROZEN
    if server.missing:
        return DisplayStatus.MISSING
    if server.rent_expires_at and server.rent_expires_at <= now:
        return DisplayStatus.EXPIRED
    state = (server.partner_state or "").lower()
    if state in {"creating", "installing", "migrating"}:
        return DisplayStatus.PROVISIONING
    if state == "restarting":
        return DisplayStatus.RESTARTING
    if state == "error":
        return DisplayStatus.ERROR
    if state == "stopped":
        return DisplayStatus.STOPPED
    if state in {"active", "running"}:
        return DisplayStatus.RUNNING
    return DisplayStatus.UNKNOWN


def is_expiring(server: Server, now: datetime | None = None, days: int = 3) -> bool:
    now = now or datetime.now(timezone.utc)
    if not server.rent_expires_at or server.cancelled:
        return False
    delta = server.rent_expires_at - now
    return 0 <= delta.total_seconds() <= days * 86400


def action_allowed(server: Server, action: str, now: datetime | None = None) -> bool:
    return action in ACTIONS.get(display_status(server, now), set())
