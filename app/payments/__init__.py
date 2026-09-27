from __future__ import annotations

from app.config import get_settings
from app.core.settings_store import settings_store
from app.payments.cryptobot import CryptoBotGateway
from app.payments.fake import FakePaymentGateway
from app.payments.xrocket import XrocketGateway

_gateways: dict[str, object] = {}


def get_gateway(name: str | None = None):
    """Return a payment gateway by name (cryptobot | xrocket | fake)."""
    settings = get_settings()
    key = (name or settings.gateway or "fake").lower().strip()
    if key in {"crypto_direct", "direct"}:
        key = "fake"
    if key not in _gateways:
        if key == "cryptobot":
            _gateways[key] = CryptoBotGateway()
        elif key in {"xrocket", "rocket", "ton_rocket"}:
            _gateways[key] = XrocketGateway()
        else:
            _gateways[key] = FakePaymentGateway()
    return _gateways[key]


def set_gateway(gw, name: str | None = None) -> None:
    key = (name or getattr(gw, "name", None) or "default").lower()
    _gateways[key] = gw


def gateway_enabled(name: str) -> bool:
    """Whether a bot payment method is configured and switched on."""
    if name == "cryptobot":
        return bool(settings_store.bool("cryptobot_enabled") and str(settings_store.get("cryptobot_token") or "").strip())
    if name == "xrocket":
        return bool(settings_store.bool("xrocket_enabled") and str(settings_store.get("xrocket_token") or "").strip())
    return False


def payment_methods() -> list[dict]:
    """Methods shown in top-up picker (gateways first, then on-chain wallets)."""
    from app.payments.crypto import enabled_wallets

    methods: list[dict] = []
    if gateway_enabled("cryptobot"):
        methods.append(
            {
                "id": "CRYPTOBOT",
                "label": "CryptoBot",
                "kind": "gateway",
                "gateway": "cryptobot",
            }
        )
    if gateway_enabled("xrocket"):
        methods.append(
            {
                "id": "XROCKET",
                "label": "xRocket",
                "kind": "gateway",
                "gateway": "xrocket",
            }
        )
    for w in enabled_wallets():
        methods.append({**w, "kind": "wallet"})
    return methods
