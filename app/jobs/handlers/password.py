from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.secrets import put_secret
from app.db.models import Job, Server
from app.jobs.notify import enqueue_notify
from app.jobs.results import Done, Fail, Review


async def handle(session: AsyncSession, redis, partner, job: Job):
    server = await session.get(Server, job.server_id or job.payload.get("server_id"))
    if server is None or not server.partner_id:
        return Fail(reason="missing")
    try:
        pwd = await partner.reset_password(
            server.partner_id, None, job.idem_key or f"job-{job.id}"
        )
    except Exception as e:
        return Review(reason=str(e))
    if not pwd:
        return Review(reason="password_missing")
    await put_secret(
        redis,
        f"cred:{server.id}",
        {"login": server.login or "root", "password": pwd},
        ttl=2_592_000,
    )
    await enqueue_notify(
        session,
        user_id=server.user_id,
        key="creds_password",
        ref=str(server.id),
        text="creds_password",
        payload={"server_id": server.id},
    )
    return Done()
