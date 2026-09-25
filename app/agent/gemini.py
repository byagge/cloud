"""Gemini client wrapper for tool-calling agent loop."""

from __future__ import annotations

import asyncio
from typing import Any

from app.agent.errors import ProviderQuotaError, is_quota_error
from app.agent.tools_def import TOOLS_BY_PHASE, max_output_tokens_for_phase, tools_for_phase
from app.config import get_settings
from app.logging import get_logger

log = get_logger("agent.gemini")

# Re-export for callers that imported TOOLS_BY_PHASE from here
__all__ = ["GeminiAgent", "TOOLS_BY_PHASE"]

_TYPE_MAP = {
    "object": "OBJECT",
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
}

_RETRY_ATTEMPTS = 5
_RETRY_BASE_SEC = 1.0
_RETRY_MAX_SEC = 30.0


def _schema_type(types_mod: Any, raw: str) -> Any:
    name = _TYPE_MAP.get((raw or "string").lower(), "STRING")
    return getattr(types_mod.Type, name)


def _declarations(phase: str) -> list[Any]:
    from google.genai import types

    tools = tools_for_phase(phase)
    out = []
    for t in tools:
        params = t["parameters"]
        props = {}
        for k, v in (params.get("properties") or {}).items():
            schema_kwargs: dict[str, Any] = {
                "type": _schema_type(types, v.get("type", "string")),
                "description": v.get("description"),
            }
            if v.get("type") == "array":
                items = v.get("items") or {"type": "string"}
                schema_kwargs["items"] = types.Schema(
                    type=_schema_type(types, items.get("type", "string"))
                )
            props[k] = types.Schema(**{k2: v2 for k2, v2 in schema_kwargs.items() if v2 is not None})
        schema = types.Schema(
            type=_schema_type(types, params.get("type", "object")),
            properties=props,
            required=list(params.get("required") or []),
        )
        out.append(
            types.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters=schema,
            )
        )
    return out


def _is_retryable(exc: BaseException) -> bool:
    # Hard billing/quota exhaustion must not be retried — surface to failover / oops UX.
    if is_quota_error(exc):
        return False
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return True
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "timeout" in msg or "timed out" in msg:
        return True
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if code in (429, 503, 502, 500):
        # 429 without quota wording = soft rate limit → retry
        return True
    if "503" in msg:
        return True
    if "rate" in msg and "limit" in msg and "quota" not in msg:
        return True
    if "unavailable" in msg or "overloaded" in msg:
        return True
    return False


class GeminiAgent:
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        phase: str = "analyze",
    ) -> None:
        settings = get_settings()
        self.api_key = (api_key or settings.gemini_api_key or "").strip()
        self.model = model or settings.gemini_model or "gemini-2.0-flash"
        self.phase = phase if phase in TOOLS_BY_PHASE else "analyze"
        self._max_out = max_output_tokens_for_phase(self.phase)
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY missing")
        from google import genai

        self._client = genai.Client(api_key=self.api_key)
        self._decls = _declarations(self.phase)

    def _chat_sync(self, contents: list[Any], *, system: str) -> Any:
        from google.genai import types

        config_kwargs: dict[str, Any] = {
            "system_instruction": system,
            "tools": [types.Tool(function_declarations=self._decls)],
            "temperature": 0.2,
            "max_output_tokens": self._max_out,
        }
        try:
            config_kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(
                disable=True
            )
        except Exception:
            pass
        config = types.GenerateContentConfig(**config_kwargs)
        return self._client.models.generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )

    def chat(self, contents: list[Any], *, system: str) -> Any:
        return self._chat_sync(contents, system=system)

    async def chat_async(self, contents: list[Any], *, system: str) -> Any:
        last: BaseException | None = None
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                return await asyncio.to_thread(self._chat_sync, contents, system=system)
            except Exception as e:
                last = e
                if is_quota_error(e):
                    raise ProviderQuotaError(self.name, str(e)) from e
                if attempt + 1 >= _RETRY_ATTEMPTS or not _is_retryable(e):
                    raise
                delay = min(_RETRY_MAX_SEC, _RETRY_BASE_SEC * (2**attempt))
                log.warning(
                    "gemini_retry",
                    attempt=attempt + 1,
                    delay=delay,
                    err=str(e)[:200],
                )
                await asyncio.sleep(delay)
        assert last is not None
        raise last
