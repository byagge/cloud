from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.core.servers import DisplayStatus, display_status, is_expiring


def test_display_status_priority():
    now = datetime.now(timezone.utc)
    s = SimpleNamespace(
        frozen=True,
        missing=False,
        rent_expires_at=now + timedelta(days=10),
        partner_state="running",
        cancelled=False,
    )
    assert display_status(s, now) == DisplayStatus.FROZEN


def test_expiring():
    now = datetime.now(timezone.utc)
    s = SimpleNamespace(
        rent_expires_at=now + timedelta(days=2),
        cancelled=False,
    )
    assert is_expiring(s, now) is True
