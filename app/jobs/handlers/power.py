from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, Server
from app.jobs.notify import enqueue_notify
from app.jobs.results import Done, Fail


async def handle(session: AsyncSession, redis, partner, job: Job):
    server = await session.get(Server, job.server_id or job.payload.get("server_id"))
    if server is None or not server.partner_id:
        return Fail(reason="missing")
    action = job.payload.get("action")
    if action not in {"start", "stop", "restart"}:
        return Fail(reason="bad_action")
    await partner.power(server.partner_id, action, job.idem_key or f"job-{job.id}")
    await enqueue_notify(
        session,
        user_id=server.user_id,
        key="power",
        ref=f"{server.id}:{action}",
        text=f"Команда {action} отправлена. Статус обновится в течение минуты.",
    )
    await redis.set(f"fast_sync", "1", ex=600)
    return Done()
