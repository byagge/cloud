from __future__ import annotations

import time
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.money import D, money
from app.db.models import PartnerCall
from app.logging import get_logger
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
from app.partner.tihost import parsers

log = get_logger("partner.tihost")


class TihostClient:
    def __init__(self, settings: Settings, session_factory) -> None:
        parsed = urlparse(settings.partner_base_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme == "http" and host not in {"localhost", "127.0.0.1"}:
            raise RuntimeError("PARTNER_BASE_URL http only allowed for localhost")
        self._settings = settings
        self._session_factory = session_factory
        self._client = httpx.AsyncClient(
            base_url=settings.partner_base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {settings.partner_api_key}",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(
                connect=settings.partner_timeout_connect,
                read=settings.partner_timeout_read,
                write=settings.partner_timeout_read,
                pool=settings.partner_timeout_connect,
            ),
        )
        self._job_id: int | None = None
        self._job_class: str | None = None

    def bind_job(self, job_id: int | None, job_class: str | None) -> None:
        self._job_id = job_id
        self._job_class = job_class

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        idem_key: str | None = None,
    ) -> Any:
        headers: dict[str, str] = {}
        if idem_key and method.upper() != "GET":
            headers["Idempotency-Key"] = idem_key[:128]

        started = time.perf_counter()
        status: int | None = None
        error_code: str | None = None
        request_id: str | None = None
        try:
            resp = await self._client.request(
                method, path, json=json, params=params, headers=headers
            )
            status = resp.status_code
            request_id = resp.headers.get("X-Request-Id")
            duration = int((time.perf_counter() - started) * 1000)
            await self._log_call(method, path, status, None, request_id, duration)

            if status == 429:
                retry_after = int(resp.headers.get("Retry-After") or "60")
                body = self._safe_json(resp)
                err = (body or {}).get("error") or {}
                raise parsers.map_error(
                    code=err.get("code") or "rate_limited",
                    http=429,
                    message=err.get("message") or "rate limited",
                    details=err.get("details"),
                    request_id=err.get("request_id") or request_id,
                    retry_after=retry_after,
                )

            if status >= 400:
                body = self._safe_json(resp) or {}
                err = body.get("error") or {}
                code = err.get("code") or "internal_error"
                error_code = code
                await self._log_call(method, path, status, code, request_id, duration)
                raise parsers.map_error(
                    code=code,
                    http=status,
                    message=err.get("message") or "",
                    details=err.get("details"),
                    request_id=err.get("request_id") or request_id,
                )

            if status == 204:
                return {}
            return resp.json()
        except PartnerError:
            raise
        except httpx.TimeoutException as e:
            duration = int((time.perf_counter() - started) * 1000)
            await self._log_call(method, path, status, "timeout", request_id, duration)
            raise PartnerError(
                code="timeout",
                http=0,
                category=PartnerErrorCategory.RETRY,
                message=str(e),
                ambiguous=method.upper() != "GET",
            ) from e
        except httpx.HTTPError as e:
            duration = int((time.perf_counter() - started) * 1000)
            await self._log_call(method, path, status, "network", request_id, duration)
            raise PartnerError(
                code="network",
                http=0,
                category=PartnerErrorCategory.RETRY,
                message=str(e),
                ambiguous=method.upper() != "GET",
            ) from e

    def _safe_json(self, resp: httpx.Response) -> dict | None:
        try:
            data = resp.json()
            return data if isinstance(data, dict) else {"data": data}
        except Exception:
            return None

    async def _log_call(
        self,
        method: str,
        path: str,
        status: int | None,
        error_code: str | None,
        request_id: str | None,
        duration_ms: int,
    ) -> None:
        try:
            async with self._session_factory() as session:
                session.add(
                    PartnerCall(
                        method=method.upper(),
                        path=path.split("?")[0],
                        status=status,
                        error_code=error_code,
                        request_id=request_id,
                        duration_ms=duration_ms,
                        job_id=self._job_id,
                        job_class=self._job_class,
                    )
                )
                await session.commit()
        except Exception:
            log.exception("partner_call_log_failed")

    async def ping(self) -> Ping:
        return parsers.parse_ping(await self._request("GET", "/ping"))

    async def balance(self) -> Decimal:
        data = await self._request("GET", "/balance")
        return money(D(data.get("balance_usd") or data.get("balance") or "0"))

    async def plans(self, location: str) -> list[Plan]:
        return parsers.parse_plans(
            await self._request("GET", "/catalog/plans", params={"location": location})
        )

    async def os_list(self, location: str, plan_id: str) -> list[OsImage]:
        return parsers.parse_os_list(
            await self._request(
                "GET", "/catalog/os", params={"location": location, "plan_id": plan_id}
            )
        )

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
        body = {
            "location": location,
            "plan_id": plan_id,
            "os_id": os_id,
            "months": months,
            "name": name,
        }
        data = await self._request("POST", "/servers", json=body, idem_key=idem_key)
        return parsers.parse_created(data)

    async def list_servers(self) -> list[PartnerServer]:
        return parsers.parse_servers(await self._request("GET", "/servers"))

    async def get_server(self, partner_id: str) -> PartnerServer:
        return parsers.parse_server(await self._request("GET", f"/servers/{partner_id}"))

    async def renew(self, partner_id: str, days: int, idem_key: str) -> Renewed:
        data = await self._request(
            "POST", f"/servers/{partner_id}/renew", json={"days": days}, idem_key=idem_key
        )
        return parsers.parse_renewed(data)

    async def set_auto_renew(self, partner_id: str, enabled: bool, idem_key: str) -> None:
        await self._request(
            "PATCH",
            f"/servers/{partner_id}/auto-renew",
            json={"enabled": enabled},
            idem_key=idem_key,
        )

    async def power(
        self, partner_id: str, action: Literal["start", "stop", "restart"], idem_key: str
    ) -> None:
        await self._request(
            "POST",
            f"/servers/{partner_id}/power",
            json={"action": action},
            idem_key=idem_key,
        )

    async def reset_password(
        self, partner_id: str, password: str | None, idem_key: str
    ) -> str:
        data = await self._request(
            "POST",
            f"/servers/{partner_id}/password",
            json={"password": password},
            idem_key=idem_key,
        )
        pwd = parsers.parse_password(data if isinstance(data, dict) else {})
        if not pwd:
            raise PartnerError(
                code="password_missing",
                http=200,
                category=PartnerErrorCategory.BUG,
                message="password not in response",
            )
        return pwd

    async def os_for_reinstall(self, partner_id: str) -> list[OsImage]:
        return parsers.parse_os_list(await self._request("GET", f"/servers/{partner_id}/os"))

    async def reinstall(self, partner_id: str, os_id: str, idem_key: str) -> None:
        await self._request(
            "POST",
            f"/servers/{partner_id}/reinstall",
            json={"os_id": os_id, "confirm": True},
            idem_key=idem_key,
        )

    async def scripts(self, partner_id: str) -> list[Script]:
        return parsers.parse_scripts(await self._request("GET", f"/servers/{partner_id}/scripts"))

    async def run_script(self, partner_id: str, script_id: str, idem_key: str) -> None:
        await self._request(
            "POST",
            f"/servers/{partner_id}/scripts/run",
            json={"script_id": script_id},
            idem_key=idem_key,
        )
