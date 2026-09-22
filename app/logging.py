from __future__ import annotations

import logging
import re
from typing import Any

import structlog

SECRET_RE = re.compile(
    r"(password|passwd|pwd|token|api[_-]?key|secret|authorization|bearer)\s*[:=]\s*\S+",
    re.IGNORECASE,
)
LONG_SECRET_RE = re.compile(r"\b[A-Za-z0-9+/_-]{32,}\b")


def redact_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    redacted = SECRET_RE.sub(r"\1=[REDACTED]", value)
    # keep short codes; scrub obvious secrets
    if "password" in value.lower() or "Bearer " in value:
        return "[REDACTED]"
    return redacted


def redact_processor(
    _logger: logging.Logger, _method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    for key, value in list(event_dict.items()):
        k = key.lower()
        if k in {"password", "passwd", "token", "api_key", "authorization", "secret"}:
            event_dict[key] = "[REDACTED]"
        else:
            event_dict[key] = redact_value(value)
    return event_dict


def setup_logging(*, json_logs: bool = True) -> None:
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", key="ts"),
        redact_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if json_logs:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
