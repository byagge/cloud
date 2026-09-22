from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


async def write_audit(
    session: AsyncSession,
    *,
    actor_kind: str,
    action: str,
    actor_id: int | None = None,
    subject_type: str | None = None,
    subject_id: int | None = None,
    before: dict | None = None,
    after: dict | None = None,
    request_id: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_kind=actor_kind,
        actor_id=actor_id,
        action=action,
        subject_type=subject_type,
        subject_id=subject_id,
        before=before,
        after=after,
        request_id=request_id,
    )
    session.add(entry)
    await session.flush()
    return entry
