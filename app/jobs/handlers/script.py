from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, Server
from app.jobs.notify import enqueue_notify
from app.jobs.results import Done, Fail


async def handle(session: AsyncSession, redis, partner, job: Job):
    server = await session.get(Server, job.server_id or job.payload.get("server_id"))
    if server is None or not server.partner_id:
        return Fail(reason="missing")
    script_id = job.payload.get("script_id")
    if not script_id:
        return Fail(reason="no_script")
    await partner.run_script(server.partner_id, script_id, job.idem_key or f"job-{job.id}")
    await enqueue_notify(
        session,
        user_id=server.user_id,
        key="script_run",
        ref=f"{server.id}:{script_id}",
        text="Скрипт запущен.",
    )
    return Done()
