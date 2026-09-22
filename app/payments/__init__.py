from __future__ import annotations

from app.config import get_settings
from app.payments.fake import FakePaymentGateway

_gateway = None


def get_gateway():
    global _gateway
    if _gateway is not None:
        return _gateway
    settings = get_settings()
    if settings.gateway == "fake" or settings.is_dev:
        _gateway = FakePaymentGateway()
    else:
        _gateway = FakePaymentGateway()  # placeholder until OPEN-11
    return _gateway


def set_gateway(gw) -> None:
    global _gateway
    _gateway = gw
