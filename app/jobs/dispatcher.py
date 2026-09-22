from __future__ import annotations

from datetime import timedelta

from sqlalchemy import Select, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db.models import Job, PartnerCall
from app.jobs.handlers import get_handler
from app.jobs.results import Done, Fail, Review, Retry, Wait, backoff_seconds, now_utc
from app.logging import get_logger
from app.partner import get_partner
from app.partner.base import PartnerError, PartnerErrorCategory
from app.partner.tihost.client import TihostClient

log = get_logger("jobs.dispatcher")

CLASS_ORDER = ("purchase", "manage", "renew", "retry")


async def writes_in_window_by_class(session: AsyncSession) -> dict[str, int]:
    since = now_utc() - timedelta(seconds=60)
    rows = await session.execute(
        select(PartnerCall.job_class, func.count())
        .where(
            PartnerCall.at >= since,
            PartnerCall.method.in_(("POST", "PATCH", "PUT", "DELETE")),
        )
        .group_by(PartnerCall.job_class)
    )
    return {cls or "manage": int(cnt) for cls, cnt in rows.all()}


async def claim(session: AsyncSession, stmt: Select[tuple[Job]]) -> Job | None:
    result = await session.execute(
        stmt.order_by(Job.run_after.asc(), Job.id.asc()).limit(1).with_for_update(skip_locked=True)
    )
    return result.scalar_one_or_none()


async def pick_next_job(session: AsyncSession, *, paused: bool) -> Job | None:
    if paused:
        return None
    settings = get_settings()
    used = await writes_in_window_by_class(session)
    if sum(used.values()) >= settings.partner_write_limit:
        return None
    due = select(Job).where(Job.status == "pending", Job.run_after <= now_utc())
    quotas = settings.partner_quotas
    for cls in CLASS_ORDER:
        if used.get(cls, 0) < quotas.get(cls, 0):
            job = await claim(session, due.where(Job.class_ == cls))
            if job:
                return job
    for cls in CLASS_ORDER:
        job = await claim(session, due.where(Job.class_ == cls))
        if job:
            return job
    return None


async def run_one(session: AsyncSession, redis, *, paused: bool = False) -> bool:
    job = await pick_next_job(session, paused=paused)
    if job is None:
        return False

    job.status = "running"
    job.started_at = now_utc()
    await session.flush()

    partner = get_partner()
    if isinstance(partner, TihostClient):
        partner.bind_job(job.id, job.class_)

    handler = get_handler(job.kind)
    try:
        result = await handler(session, redis, partner, job)
    except PartnerError as e:
        result = _from_partner_error(e, job)
    except Exception as e:
        log.exception("job_failed", job_id=job.id, kind=job.kind)
        result = Retry(after=timedelta(seconds=backoff_seconds(job.attempts + 1)), reason=str(e))

    await _apply_result(session, job, result)
    return True


def _from_partner_error(e: PartnerError, job: Job):
    if e.category == PartnerErrorCategory.RATE_LIMITED:
        return Wait(after=timedelta(seconds=e.retry_after or 60), reason=e.code)
    if e.category == PartnerErrorCategory.WAIT_FUNDS:
        return Wait(after=timedelta(minutes=5), reason=e.code)
    if e.category == PartnerErrorCategory.BUSY:
        return Retry(after=timedelta(seconds=60), reason=e.code)
    if e.category == PartnerErrorCategory.RETRY:
        return Retry(
            after=timedelta(seconds=backoff_seconds(job.attempts + 1)),
            reason=e.code,
        )
    if e.category in {
        PartnerErrorCategory.FATAL_REQUEST,
        PartnerErrorCategory.NOT_FOUND,
        PartnerErrorCategory.STATE_CONFLICT,
        PartnerErrorCategory.EXPIRED,
    }:
        return Fail(reason=e.code)
    if e.category in {PartnerErrorCategory.BUG, PartnerErrorCategory.FATAL_SERVICE}:
        return Review(reason=e.code)
    return Retry(after=timedelta(seconds=30), reason=e.code)


async def _apply_result(session: AsyncSession, job: Job, result) -> None:
    if isinstance(result, Done):
        job.status = "done"
        job.finished_at = now_utc()
        job.last_error = None
    elif isinstance(result, Wait):
        job.status = "pending"
        job.run_after = now_utc() + result.after
        job.last_error = result.reason
    elif isinstance(result, Retry):
        if result.count_attempt:
            job.attempts += 1
        if job.attempts >= job.max_attempts:
            job.status = "failed"
            job.finished_at = now_utc()
            job.last_error = result.reason
            if job.order_id:
                from app.db.models import Order

                order = await session.get(Order, job.order_id)
                if order and order.status in {"queued", "provisioning", "waiting_partner_funds"}:
                    order.status = "needs_review"
                    order.last_error = result.reason
        else:
            job.status = "pending"
            job.run_after = now_utc() + result.after
            job.last_error = result.reason
    elif isinstance(result, Fail):
        job.status = "failed"
        job.finished_at = now_utc()
        job.last_error = result.reason
    elif isinstance(result, Review):
        job.status = "failed"
        job.finished_at = now_utc()
        job.last_error = result.reason
        if job.order_id:
            from app.db.models import Order

            order = await session.get(Order, job.order_id)
            if order:
                order.status = "needs_review"
                order.last_error = result.reason
    await session.flush()


async def try_acquire_leader(session: AsyncSession, lock_id: int = 42001) -> bool:
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "sqlite":
        return True
    got = await session.scalar(text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id})
    return bool(got)
