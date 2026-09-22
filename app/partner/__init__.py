from __future__ import annotations

from app.config import Settings, get_settings
from app.db.session import get_session_factory
from app.partner.fake import FakePartner
from app.partner.tihost.client import TihostClient

_partner = None


def get_partner(settings: Settings | None = None):
    global _partner
    if _partner is not None:
        return _partner
    settings = settings or get_settings()
    if settings.use_fake_partner:
        _partner = FakePartner()
        return _partner
    _partner = TihostClient(settings, get_session_factory())
    return _partner


def set_partner(adapter) -> None:
    global _partner
    _partner = adapter
