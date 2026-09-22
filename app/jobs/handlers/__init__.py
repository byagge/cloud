from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.handlers import password, power, provision, reinstall, renew, script, set_auto_renew

Handler = Callable[[AsyncSession, Any, Any, Any], Awaitable[Any]]

_HANDLERS: dict[str, Handler] = {
    "provision": provision.handle,
    "renew": renew.handle,
    "power": power.handle,
    "reset_password": password.handle,
    "reinstall": reinstall.handle,
    "run_script": script.handle,
    "set_auto_renew": set_auto_renew.handle,
}


def get_handler(kind: str) -> Handler:
    if kind not in _HANDLERS:
        raise KeyError(f"unknown job kind: {kind}")
    return _HANDLERS[kind]
