from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, Server
from app.jobs.results import Done, Fail


async def handle(session: AsyncSession, redis, partner, job: Job):
    server = await session.get(Server, job.server_id or job.payload.get("server_id"))
    if server is None or not server.partner_id:
        return Fail(reason="missing")
    enabled = bool(job.payload.get("enabled", False))
    await partner.set_auto_renew(
        server.partner_id, enabled, job.idem_key or f"job-{job.id}"
    )
    if not enabled:
        server.partner_auto_renew_off = True
    return Done()
