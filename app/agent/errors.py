"""LLM provider errors."""

from __future__ import annotations


class ProviderQuotaError(Exception):
    """Billing / quota exhausted (429 RESOURCE_EXHAUSTED, insufficient_quota, …)."""

    def __init__(self, provider: str, detail: str = "") -> None:
        self.provider = provider
        self.detail = (detail or "")[:500]
        super().__init__(f"{provider} quota: {self.detail}")


class ProviderError(Exception):
    def __init__(self, provider: str, detail: str = "") -> None:
        self.provider = provider
        self.detail = (detail or "")[:500]
        super().__init__(f"{provider}: {self.detail}")


# Sentinel returned to the user via AgentFail.error
OOPS_SENTINEL = "PROVIDER_OOPS"


def is_quota_error(exc: BaseException) -> bool:
    """True for hard billing/quota exhaustion — not soft per-minute rate limits."""
    msg = str(exc).lower()
    needles = (
        "resource_exhausted",
        "exceeded your current quota",
        "insufficient_quota",
        "quota exceeded",
        "quota_exceeded",
        "billing details",
        "check your plan and billing",
        "credit balance",
        "outofcredit",
        "out of credit",
        "purchase credits",
    )
    return any(n in msg for n in needles)
