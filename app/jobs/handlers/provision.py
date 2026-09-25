from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ledger import LedgerKind, post_entry
from app.core.secrets import put_secret
from app.db.models import Job, Order, Server, User
from app.jobs.notify import enqueue_notify
from app.jobs.results import Done, Fail, Review, Retry, now_utc
from app.partner.base import PartnerError, PartnerErrorCategory


async def handle(session: AsyncSession, redis, partner, job: Job):
    order = await session.get(Order, job.order_id or job.payload.get("order_id"))
    if order is None:
        return Fail(reason="order_missing")
    if order.status in {"active", "refunded", "cancelled"}:
        return Done()

    order.status = "provisioning"
    order.attempts += 1
    order.updated_at = now_utc()
    await session.flush()

    # Ambiguous timeout reconcile
    if order.attempts >= 3:
        servers = await partner.list_servers()
        name = f"arix-o{order.id}"
        found = next((s for s in servers if s.name == name), None)
        if found:
            return await _adopt(session, redis, partner, order, job, found)

    try:
        created = await partner.create_server(
            location=order.location or "",
            plan_id=order.plan_id or "",
            os_id=order.os_id or "",
            months=order.months or 1,
            name=f"arix-o{order.id}",
            idem_key=job.idem_key or f"order-{order.id}",
        )
    except PartnerError as e:
        if e.category == PartnerErrorCategory.WAIT_FUNDS:
            # Partner unpaid — never leave the client charged / waiting forever
            await _fail_create_for_user(session, order, reason="partner_funds")
            await enqueue_notify(
                session,
                user_id=None,
                key="admin_partner_funds",
                ref=str(order.id),
                text=(
                    f"[admin] Partner funds low — order #{order.id} cancelled, "
                    f"user refunded. Top up Tihost balance."
                ),
                admin=True,
            )
            return Fail(reason="partner_funds")
        if e.category == PartnerErrorCategory.RETRY and e.ambiguous:
            return Retry(after=timedelta(seconds=30), reason=e.code)
        if e.category == PartnerErrorCategory.FATAL_REQUEST:
            await _fail_create_for_user(session, order, reason=e.code)
            return Fail(reason=e.code)
        raise

    server = Server(
        user_id=order.user_id,
        partner_id=created.id,
        partner_name=created.name,
        display_name=f"server-pending",
        location=order.location or created.location or "",
        os_label=created.os,
        plan_label=(order.snapshot or {}).get("plan_label"),
        login=created.login,
        partner_state="creating",
        rent_expires_at=created.rent_expires_at,
        auto_renew=False,
        renew_days=30,
    )
    session.add(server)
    await session.flush()
    server.display_name = f"server-{server.id}"

    order.status = "active"
    order.server_id = server.id
    order.charged_by_partner_usd = created.charged_usd
    order.finished_at = now_utc()

    follow = Job(
        kind="set_auto_renew",
        class_="purchase",
        payload={"server_id": server.id, "enabled": False},
        status="pending",
        idem_key=f"job-autor-{job.id}",
        server_id=server.id,
        order_id=order.id,
    )
    session.add(follow)

    if created.password:
        await put_secret(
            redis,
            f"cred:{server.id}",
            {
                "login": created.login or "root",
                "password": created.password,
                "tg_id": None,
            },
            ttl=2_592_000,
        )
        await enqueue_notify(
            session,
            user_id=order.user_id,
            key="creds",
            ref=str(server.id),
            text="creds",
            payload={"server_id": server.id},
        )
    else:
        # Provider did not return password on create — fetch automatically
        session.add(
            Job(
                kind="reset_password",
                class_="manage",
                payload={"server_id": server.id, "silent": False},
                status="pending",
                idem_key=f"job-pw-create-{job.id}",
                server_id=server.id,
                order_id=order.id,
            )
        )

    await enqueue_notify(
        session,
        user_id=order.user_id,
        key="order_queued_ok",
        ref=str(order.id),
        text=f"Заказ #{order.id}: сервер создаётся.",
    )
    await session.flush()
    return Done()


async def _adopt(session, redis, partner, order, job, found) -> Done | Review:
    detail = await partner.get_server(found.id)
    server = Server(
        user_id=order.user_id,
        partner_id=detail.id,
        partner_name=detail.name,
        display_name="server-pending",
        location=order.location or detail.location or "",
        os_label=detail.os,
        login=detail.login,
        ip=detail.ip,
        cpu=detail.cpu,
        ram_mb=detail.ram_mb,
        disk_gb=detail.disk_gb,
        partner_state=detail.state,
        rent_expires_at=detail.rent_expires_at,
    )
    session.add(server)
    await session.flush()
    server.display_name = f"server-{server.id}"
    order.status = "active"
    order.server_id = server.id
    order.finished_at = now_utc()
    session.add(
        Job(
            kind="reset_password",
            class_="manage",
            payload={"server_id": server.id},
            status="pending",
            idem_key=f"job-pw-{job.id}",
            server_id=server.id,
        )
    )
    return Done()


async def _refund(session: AsyncSession, order: Order) -> None:
    """Return charged balance to the user (idempotent via uniq_key)."""
    if order.price_usd and order.payment_mode == "balance":
        await post_entry(
            session,
            user_id=order.user_id,
            kind=LedgerKind.REFUND,
            amount=order.price_usd,
            uniq_key=f"order:{order.id}:refund",
            ref_type="order",
            ref_id=order.id,
            reason="provision_failed",
        )
        order.status = "refunded"
    elif order.status not in {"refunded", "cancelled", "active"}:
        order.status = "failed"
    order.finished_at = now_utc()


async def _fail_create_for_user(session: AsyncSession, order: Order, *, reason: str) -> None:
    """Refund + friendly client message. Never expose partner/internal errors."""
    from app.bot.texts import t

    await _refund(session, order)
    user = await session.get(User, order.user_id)
    lang = (user.lang if user else None) or "en"
    await enqueue_notify(
        session,
        user_id=order.user_id,
        key=f"order_create_failed:{order.id}",
        ref=str(order.id),
        text=t("order_create_failed", lang),
        immediate=True,
    )
    order.last_error = (reason or "")[:200]
