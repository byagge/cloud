from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, Order, Server
from app.jobs.results import Done, Fail


async def handle(session: AsyncSession, redis, partner, job: Job):
    server = await session.get(Server, job.server_id or job.payload.get("server_id"))
    order = await session.get(Order, job.order_id) if job.order_id else None
    if server is None or not server.partner_id:
        return Fail(reason="missing")
    os_id = (job.payload.get("os_id") or (order.os_id if order else None) or "")
    await partner.reinstall(server.partner_id, os_id, job.idem_key or f"reinstall-{job.id}")
    server.reinstall_pending = True
    if order:
        order.status = "active"
    await redis.set("fast_sync", "1", ex=600)
    return Done()
