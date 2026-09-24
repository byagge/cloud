from __future__ import annotations

from decimal import Decimal, InvalidOperation
from html import escape
from uuid import uuid4

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from app.bot.keyboards import (
    AdmCB,
    admin_home_kb,
    admin_invoice_kb,
    admin_job_kb,
    admin_list_kb,
    admin_order_kb,
    admin_server_kb,
    admin_settings_kb,
    admin_user_kb,
    admin_wallet_kb,
    admin_wallets_kb,
)
from app.bot.render import safe_edit
from app.bot.texts import t
from app.bot.ui.screens import decorate
from app.core.ledger import LedgerKind, post_entry
from app.core.money import D, format_usd, money
from app.core.pricing import client_price
from app.core.settings_store import settings_store
from app.db.models import AuditLog, Invoice, Job, LedgerEntry, Order, Server, User
from app.db.session import get_session_factory
from app.payments.base import GatewayInvoice
from app.payments.crypto import find_wallet, get_wallets, set_wallet_enabled, upsert_wallet
from app.payments.service import apply_invoice_state
from app.partner import get_partner
from app.partner.base import PartnerError

router = Router()
PAGE = 8
ROLE_LEVEL = {"support": 1, "operator": 2, "owner": 3}


class AdminBal(StatesGroup):
    waiting = State()


class AdminAddr(StatesGroup):
    waiting = State()


class AdminBC(StatesGroup):
    waiting = State()


class AdminAttach(StatesGroup):
    waiting = State()


def _require(role: str | None, need: str) -> bool:
    if not role:
        return False
    return ROLE_LEVEL.get(role, 0) >= ROLE_LEVEL.get(need, 99)


def _page(arg: str) -> int:
    return int(arg) if arg.isdigit() else 0


async def _audit(
    session,
    *,
    actor_id: int,
    action: str,
    subject_type: str | None = None,
    subject_id: int | None = None,
    after: dict | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_kind="admin",
            actor_id=actor_id,
            action=action,
            subject_type=subject_type,
            subject_id=subject_id,
            after=after,
        )
    )


# ── Home ─────────────────────────────────────────────────────────────


@router.message(Command("admin"))
@router.callback_query(AdmCB.filter(F.section == "home"))
async def admin_home(event: Message | CallbackQuery, db_user, admin_role, redis, state: FSMContext) -> None:
    if state:
        await state.clear()
    if not _require(admin_role, "support"):
        if isinstance(event, CallbackQuery):
            await event.answer(t("not_found", db_user.lang), show_alert=True)
        return
    lang = db_user.lang or "ru"
    factory = get_session_factory()
    async with factory() as session:
        users = await session.scalar(select(func.count()).select_from(User)) or 0
        servers = await session.scalar(select(func.count()).select_from(Server)) or 0
        jobs = (
            await session.scalar(
                select(func.count()).select_from(Job).where(Job.status.in_(("pending", "running")))
            )
            or 0
        )
        review = (
            await session.scalar(
                select(func.count()).select_from(Order).where(Order.status == "needs_review")
            )
            or 0
        )
    partner_balance = await redis.get("partner:balance") or "—"
    if partner_balance not in {"—", None}:
        try:
            partner_balance = format_usd(partner_balance)
        except Exception:
            pass
    text = decorate(
        t(
            "admin_home",
            lang,
            users=users,
            servers=servers,
            jobs=jobs,
            review=review,
            partner_balance=partner_balance,
            crown="{crown}",
        )
    )
    await safe_edit(event, text, admin_home_kb(lang))


# ── Users ─────────────────────────────────────────────────────────────


@router.callback_query(AdmCB.filter((F.section == "usr") & (F.action == "list")))
async def admin_users_list(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "support"):
        await query.answer(t("not_found", db_user.lang), show_alert=True)
        return
    lang = db_user.lang or "ru"
    page = _page(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        total = await session.scalar(select(func.count()).select_from(User)) or 0
        users = (
            await session.scalars(
                select(User).order_by(User.id.desc()).offset(page * PAGE).limit(PAGE + 1)
            )
        ).all()
    has_next = len(users) > PAGE
    users = users[:PAGE]
    items = []
    for u in users:
        mark = "🚫 " if u.banned else ""
        uname = f"@{u.username}" if u.username else str(u.tg_id)
        items.append((str(u.id), f"{mark}#{u.id} {uname} · {format_usd(u.balance_usd)}"))
    text = decorate(
        t("adm_users_title", lang, count=total, users="{users}")
        + (t("adm_empty", lang) if not items else "")
    )
    await safe_edit(query, text, admin_list_kb("usr", items, page=page, has_next=has_next, lang=lang))


@router.callback_query(AdmCB.filter((F.section == "usr") & (F.action == "open")))
async def admin_user_open(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "support"):
        await query.answer(t("not_found", db_user.lang), show_alert=True)
        return
    lang = db_user.lang or "ru"
    uid = int(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        u = await session.get(User, uid)
        if not u:
            await query.answer(t("not_found", lang), show_alert=True)
            return
        srv_n = await session.scalar(
            select(func.count()).select_from(Server).where(Server.user_id == uid)
        ) or 0
        ord_n = await session.scalar(
            select(func.count()).select_from(Order).where(Order.user_id == uid)
        ) or 0
    uname = f"@{escape(u.username)}" if u.username else "—"
    text = (
        f"<b>Клиент #{u.id}</b>\n\n"
        f"├ TG: <code>{u.tg_id}</code>\n"
        f"├ Username: {uname}\n"
        f"├ Имя: {escape(u.first_name or '—')}\n"
        f"├ Баланс: <b>{format_usd(u.balance_usd)}</b>\n"
        f"├ Язык: {u.lang}\n"
        f"├ Banned: <b>{'да' if u.banned else 'нет'}</b>\n"
        f"├ Серверов: {srv_n}\n"
        f"├ Заказов: {ord_n}\n"
        f"╰ Реф: <code>{escape(u.ref_source or '—')}</code>"
    )
    await safe_edit(query, text, admin_user_kb(u.id, banned=u.banned, lang=lang))


@router.callback_query(AdmCB.filter((F.section == "usr") & (F.action.in_({"ban", "unban"}))))
async def admin_user_ban(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "operator"):
        await query.answer("operator+", show_alert=True)
        return
    uid = int(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        u = await session.get(User, uid)
        if not u:
            await query.answer(t("not_found", db_user.lang), show_alert=True)
            return
        u.banned = callback_data.action == "ban"
        await _audit(session, actor_id=db_user.tg_id, action=f"user:{callback_data.action}", subject_type="user", subject_id=uid)
        await session.commit()
    await query.answer("ok")
    await admin_user_open(query, AdmCB(section="usr", action="open", arg=str(uid)), db_user, admin_role)


@router.callback_query(AdmCB.filter((F.section == "usr") & (F.action == "bal")))
async def admin_user_bal(query: CallbackQuery, callback_data: AdmCB, state: FSMContext, db_user, admin_role) -> None:
    if not _require(admin_role, "owner"):
        await query.answer("owner", show_alert=True)
        return
    uid = int(callback_data.arg)
    await state.set_state(AdminBal.waiting)
    await state.update_data(adj_user_id=uid)
    await query.message.answer(t("adm_bal_ask", db_user.lang, id=uid))
    await query.answer()


@router.message(AdminBal.waiting)
async def admin_bal_apply(message: Message, state: FSMContext, db_user, admin_role) -> None:
    if not _require(admin_role, "owner"):
        await state.clear()
        return
    data = await state.get_data()
    uid = int(data.get("adj_user_id") or 0)
    await state.clear()
    try:
        amount = money(D((message.text or "").replace("$", "").replace(",", ".").strip()))
    except (InvalidOperation, ValueError):
        await message.answer("?")
        return
    if amount == 0:
        await message.answer("0")
        return
    factory = get_session_factory()
    async with factory() as session:
        await post_entry(
            session,
            user_id=uid,
            kind=LedgerKind.ADJUST,
            amount=amount,
            uniq_key=f"adj:{uid}:{uuid4().hex[:12]}",
            reason=f"admin_adjust:{db_user.tg_id}",
            admin_id=db_user.tg_id,
        )
        await _audit(session, actor_id=db_user.tg_id, action="user:balance", subject_type="user", subject_id=uid, after={"amount": str(amount)})
        await session.commit()
        u = await session.get(User, uid)
        bal = u.balance_usd if u else amount
    await message.answer(f"OK → {format_usd(bal)}")


@router.callback_query(AdmCB.filter((F.section == "usr") & (F.action == "attach")))
async def admin_user_attach(query: CallbackQuery, callback_data: AdmCB, state: FSMContext, db_user, admin_role) -> None:
    if not _require(admin_role, "operator"):
        await query.answer("operator+", show_alert=True)
        return
    uid = int(callback_data.arg)
    await state.set_state(AdminAttach.waiting)
    await state.update_data(attach_user_id=uid)
    await query.message.answer(t("adm_attach_ask", db_user.lang or "ru", id=uid))
    await query.answer()


@router.message(AdminAttach.waiting)
async def admin_attach_apply(message: Message, state: FSMContext, db_user, admin_role) -> None:
    if not _require(admin_role, "operator"):
        await state.clear()
        return
    data = await state.get_data()
    uid = int(data.get("attach_user_id") or 0)
    await state.clear()
    partner_id = (message.text or "").strip()
    if not partner_id or not partner_id.isdigit():
        await message.answer(t("adm_attach_fail", db_user.lang or "ru", err="нужен числовой Partner ID"))
        return
    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, uid)
        if not user:
            await message.answer(t("not_found", db_user.lang or "ru"))
            return
        existing = await session.scalar(select(Server).where(Server.partner_id == partner_id))
        if existing:
            await message.answer(
                t("adm_attach_fail", db_user.lang or "ru", err=f"уже привязан как #{existing.id}")
            )
            return
        try:
            remote = await get_partner().get_server(partner_id)
        except PartnerError as e:
            await message.answer(t("adm_attach_fail", db_user.lang or "ru", err=str(e)))
            return
        except Exception as e:
            await message.answer(t("adm_attach_fail", db_user.lang or "ru", err=str(e)))
            return
        renew = {str(k): str(v) for k, v in (remote.renew_prices or {}).items()}
        server = Server(
            user_id=uid,
            partner_id=str(remote.id),
            partner_name=remote.name or f"partner-{remote.id}",
            display_name=remote.name or f"server-{remote.id}",
            location=remote.location or "germany",
            os_label=remote.os,
            login=remote.login,
            ip=remote.ip,
            cpu=remote.cpu,
            ram_mb=remote.ram_mb,
            disk_gb=remote.disk_gb,
            partner_state=remote.state or "active",
            rent_expires_at=remote.rent_expires_at,
            renew_prices=renew,
            plan_label=(
                f"{remote.cpu or '?'} vCPU / {(remote.ram_mb or 0)//1024} GB / {remote.disk_gb or '?'}GB"
                if remote.cpu or remote.ram_mb
                else None
            ),
        )
        session.add(server)
        await session.flush()
        if not remote.name:
            server.display_name = f"server-{server.id}"
        # fetch SSH password into Redis so card/AI can use it
        session.add(
            Job(
                kind="reset_password",
                class_="manage",
                payload={"server_id": server.id},
                status="pending",
                idem_key=f"attach-pw-{server.id}-{uuid4().hex[:8]}",
                server_id=server.id,
            )
        )
        await _audit(
            session,
            actor_id=db_user.tg_id,
            action="server:attach",
            subject_type="server",
            subject_id=server.id,
            after={"partner_id": partner_id, "user_id": uid, "ip": remote.ip},
        )
        await session.commit()
        sid = server.id
        ip = remote.ip or "—"
    await message.answer(
        t("adm_attach_ok", db_user.lang or "ru", sid=sid, pid=partner_id, ip=ip)
    )


@router.callback_query(AdmCB.filter((F.section == "usr") & (F.action == "servers")))
async def admin_user_servers(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "support"):
        return
    lang = db_user.lang or "ru"
    uid = int(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        servers = (
            await session.scalars(select(Server).where(Server.user_id == uid).order_by(Server.id.desc()).limit(20))
        ).all()
    items = [(str(s.id), f"#{s.id} {s.display_name[:24]}") for s in servers]
    text = decorate(t("adm_servers_title", lang, count=len(items), monitor="{monitor}"))
    kb = admin_list_kb("srv", items, lang=lang)
    # override back to user
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    from app.bot.keyboards import _ib

    rows = list(kb.inline_keyboard[:-1]) if kb.inline_keyboard else []
    rows.append([_ib(t("btn_back", lang), AdmCB(section="usr", action="open", arg=str(uid)).pack(), "down")])
    await safe_edit(query, text, InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(AdmCB.filter((F.section == "usr") & (F.action == "orders")))
async def admin_user_orders(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "support"):
        return
    lang = db_user.lang or "ru"
    uid = int(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        orders = (
            await session.scalars(
                select(Order)
                .where(Order.user_id == uid, Order.status != "draft")
                .order_by(Order.id.desc())
                .limit(20)
            )
        ).all()
    items = [(str(o.id), f"#{o.id} {o.kind} · {o.status}") for o in orders]
    text = decorate(t("adm_orders_title", lang, count=len(items), bag="{bag}"))
    from aiogram.types import InlineKeyboardMarkup
    from app.bot.keyboards import _ib

    rows = [
        [_ib(label, AdmCB(section="ord", action="open", arg=iid).pack(), "bag")] for iid, label in items
    ]
    rows.append([_ib(t("btn_back", lang), AdmCB(section="usr", action="open", arg=str(uid)).pack(), "down")])
    await safe_edit(query, text, InlineKeyboardMarkup(inline_keyboard=rows))


# ── Servers ───────────────────────────────────────────────────────────


@router.callback_query(AdmCB.filter((F.section == "srv") & (F.action == "list")))
async def admin_servers_list(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "operator"):
        await query.answer("operator+", show_alert=True)
        return
    lang = db_user.lang or "ru"
    page = _page(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        total = await session.scalar(select(func.count()).select_from(Server)) or 0
        servers = (
            await session.scalars(
                select(Server).order_by(Server.id.desc()).offset(page * PAGE).limit(PAGE + 1)
            )
        ).all()
    has_next = len(servers) > PAGE
    servers = servers[:PAGE]
    items = [
        (str(s.id), f"#{s.id} u{s.user_id} {s.display_name[:18]} · {s.partner_state or '—'}")
        for s in servers
    ]
    text = decorate(
        t("adm_servers_title", lang, count=total, monitor="{monitor}")
        + (t("adm_empty", lang) if not items else "")
    )
    await safe_edit(query, text, admin_list_kb("srv", items, page=page, has_next=has_next, lang=lang))


@router.callback_query(AdmCB.filter((F.section == "srv") & (F.action == "open")))
async def admin_server_open(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "operator"):
        await query.answer("operator+", show_alert=True)
        return
    lang = db_user.lang or "ru"
    sid = int(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        s = await session.get(Server, sid)
        if not s:
            await query.answer(t("not_found", lang), show_alert=True)
            return
    exp = s.rent_expires_at.strftime("%d.%m.%Y %H:%M") if s.rent_expires_at else "—"
    text = (
        f"<b>Сервер #{s.id}</b>\n\n"
        f"├ Имя: {escape(s.display_name)}\n"
        f"├ User: <code>{s.user_id}</code>\n"
        f"├ Partner: <code>{escape(s.partner_id or '—')}</code>\n"
        f"├ State: <b>{escape(s.partner_state or '—')}</b>\n"
        f"├ Loc: {escape(s.location)}\n"
        f"├ Plan: {escape(s.plan_label or '—')}\n"
        f"├ OS: {escape(s.os_label or '—')}\n"
        f"├ IP: <code>{s.ip or '—'}</code>\n"
        f"├ Frozen: {s.frozen} · Cancelled: {s.cancelled}\n"
        f"╰ До: {exp}"
    )
    await safe_edit(query, text, admin_server_kb(s.id, s.user_id, lang=lang))


@router.callback_query(AdmCB.filter((F.section == "srv") & (F.action == "pwr")))
async def admin_server_pwr(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "operator"):
        await query.answer("operator+", show_alert=True)
        return
    try:
        sid_s, action = callback_data.arg.split("|", 1)
        sid = int(sid_s)
    except Exception:
        await query.answer("?", show_alert=True)
        return
    factory = get_session_factory()
    async with factory() as session:
        s = await session.get(Server, sid)
        if not s or not s.partner_id:
            await query.answer(t("not_found", db_user.lang), show_alert=True)
            return
        from app.partner import get_partner

        partner = get_partner()
        try:
            await partner.power(
                s.partner_id, action, f"admin-pwr:{sid}:{action}:{uuid4().hex[:8]}"
            )
            s.partner_state = "stopped" if action == "stop" else "running"
            await _audit(
                session,
                actor_id=db_user.tg_id,
                action=f"server:{action}",
                subject_type="server",
                subject_id=sid,
            )
            await session.commit()
            await query.answer(action)
        except Exception as e:
            await query.answer(str(e)[:100], show_alert=True)
            return
    await admin_server_open(query, AdmCB(section="srv", action="open", arg=str(sid)), db_user, admin_role)


@router.callback_query(AdmCB.filter((F.section == "srv") & (F.action.in_({"freeze", "unfreeze"}))))
async def admin_server_freeze(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "operator"):
        await query.answer("operator+", show_alert=True)
        return
    sid = int(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        s = await session.get(Server, sid)
        if not s:
            await query.answer(t("not_found", db_user.lang), show_alert=True)
            return
        s.frozen = callback_data.action == "freeze"
        await _audit(session, actor_id=db_user.tg_id, action=f"server:{callback_data.action}", subject_type="server", subject_id=sid)
        await session.commit()
    await query.answer("ok")
    await admin_server_open(query, AdmCB(section="srv", action="open", arg=str(sid)), db_user, admin_role)


# ── Orders ────────────────────────────────────────────────────────────


@router.callback_query(AdmCB.filter((F.section == "ord") & (F.action == "list")))
async def admin_orders_list(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "operator"):
        await query.answer("operator+", show_alert=True)
        return
    lang = db_user.lang or "ru"
    page = _page(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        total = await session.scalar(
            select(func.count()).select_from(Order).where(Order.status != "draft")
        ) or 0
        orders = (
            await session.scalars(
                select(Order)
                .where(Order.status != "draft")
                .order_by(Order.id.desc())
                .offset(page * PAGE)
                .limit(PAGE + 1)
            )
        ).all()
    has_next = len(orders) > PAGE
    orders = orders[:PAGE]
    items = [
        (str(o.id), f"#{o.id} {o.kind} · {o.status} · {format_usd(o.price_usd or 0)}")
        for o in orders
    ]
    text = decorate(
        t("adm_orders_title", lang, count=total, bag="{bag}")
        + (t("adm_empty", lang) if not items else "")
    )
    await safe_edit(query, text, admin_list_kb("ord", items, page=page, has_next=has_next, lang=lang))


@router.callback_query(AdmCB.filter((F.section == "ord") & (F.action == "open")))
async def admin_order_open(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "operator"):
        return
    lang = db_user.lang or "ru"
    oid = int(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        o = await session.get(Order, oid)
        if not o:
            await query.answer(t("not_found", lang), show_alert=True)
            return
    can_retry = o.status in {"failed", "needs_review", "waiting_partner_funds"}
    text = (
        f"<b>Заказ #{o.id}</b>\n\n"
        f"├ Kind: {o.kind}\n"
        f"├ Status: <b>{o.status}</b>\n"
        f"├ User: <code>{o.user_id}</code>\n"
        f"├ Server: {o.server_id or '—'}\n"
        f"├ Loc/Plan/OS: {escape(o.location or '—')} / {escape(o.plan_id or '—')} / {escape(o.os_id or '—')}\n"
        f"├ Price: {format_usd(o.price_usd or 0)} (cost {format_usd(o.partner_cost_usd or 0)})\n"
        f"├ Attempts: {o.attempts}\n"
        f"╰ Error: {escape((o.last_error or '—')[:200])}"
    )
    await safe_edit(query, text, admin_order_kb(o.id, o.user_id, can_retry=can_retry, lang=lang))


@router.callback_query(AdmCB.filter((F.section == "ord") & (F.action == "retry")))
async def admin_order_retry(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "operator"):
        return
    oid = int(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        o = await session.get(Order, oid)
        if not o:
            await query.answer(t("not_found", db_user.lang), show_alert=True)
            return
        o.status = "queued"
        o.last_error = None
        session.add(
            Job(
                kind="provision",
                class_="purchase",
                payload={"order_id": o.id},
                order_id=o.id,
                idem_key=f"retry:{o.id}:{uuid4().hex[:8]}",
            )
        )
        await _audit(session, actor_id=db_user.tg_id, action="order:retry", subject_type="order", subject_id=oid)
        await session.commit()
    await query.answer("queued")
    await admin_order_open(query, AdmCB(section="ord", action="open", arg=str(oid)), db_user, admin_role)


# ── Invoices ──────────────────────────────────────────────────────────


@router.callback_query(AdmCB.filter((F.section == "inv") & (F.action == "list")))
async def admin_inv_list(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "operator"):
        await query.answer("operator+", show_alert=True)
        return
    lang = db_user.lang or "ru"
    page = _page(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        total = await session.scalar(select(func.count()).select_from(Invoice)) or 0
        rows = (
            await session.scalars(
                select(Invoice).order_by(Invoice.id.desc()).offset(page * PAGE).limit(PAGE + 1)
            )
        ).all()
    has_next = len(rows) > PAGE
    rows = rows[:PAGE]
    items = [
        (str(i.id), f"#{i.id} {i.status} · {format_usd(i.amount_usd)} · {i.network or i.gateway}")
        for i in rows
    ]
    text = decorate(
        t("adm_invoices_title", lang, count=total, wallet="{wallet}")
        + (t("adm_empty", lang) if not items else "")
    )
    await safe_edit(query, text, admin_list_kb("inv", items, page=page, has_next=has_next, lang=lang))


@router.callback_query(AdmCB.filter((F.section == "inv") & (F.action == "open")))
async def admin_inv_open(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "operator"):
        return
    lang = db_user.lang or "ru"
    iid = int(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        inv = await session.get(Invoice, iid)
        if not inv:
            await query.answer(t("not_found", lang), show_alert=True)
            return
    text = (
        f"<b>Счёт #{inv.id}</b>\n\n"
        f"├ User: <code>{inv.user_id}</code>\n"
        f"├ Gateway: {escape(inv.gateway)}\n"
        f"├ Status: <b>{inv.status}</b>\n"
        f"├ Amount: {format_usd(inv.amount_usd)}\n"
        f"├ Network/Asset: {inv.network or '—'} / {inv.asset or '—'}\n"
        f"├ Pay: {inv.pay_amount or '—'} → <code>{escape(inv.address or '—')}</code>\n"
        f"╰ TX: <code>{escape(inv.txid or '—')}</code>"
    )
    await safe_edit(
        query,
        text,
        admin_invoice_kb(inv.id, inv.user_id, pending=inv.status == "pending", lang=lang),
    )


@router.callback_query(AdmCB.filter((F.section == "inv") & (F.action == "paid")))
async def admin_inv_paid(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "owner"):
        await query.answer("owner", show_alert=True)
        return
    iid = int(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        inv = await session.get(Invoice, iid)
        if not inv or inv.status == "paid":
            await query.answer("—", show_alert=True)
            return
        remote = GatewayInvoice(
            gateway_invoice_id=inv.gateway_invoice_id or str(inv.id),
            status="paid",
            paid_usd=inv.amount_usd,
            pay_amount=inv.pay_amount,
            address=inv.address,
            asset=inv.asset,
            network=inv.network,
            expires_at=inv.expires_at,
            txid=f"admin-{db_user.tg_id}-{iid}",
        )
        await apply_invoice_state(session, inv, remote)
        await _audit(session, actor_id=db_user.tg_id, action="invoice:paid", subject_type="invoice", subject_id=iid)
        await session.commit()
    await query.answer("paid")
    await admin_inv_open(query, AdmCB(section="inv", action="open", arg=str(iid)), db_user, admin_role)


# ── Jobs ──────────────────────────────────────────────────────────────


@router.callback_query(AdmCB.filter((F.section == "job") & (F.action == "list")))
async def admin_jobs_list(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "operator"):
        await query.answer("operator+", show_alert=True)
        return
    lang = db_user.lang or "ru"
    page = _page(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        total = await session.scalar(
            select(func.count()).select_from(Job).where(Job.status.in_(("pending", "running", "failed")))
        ) or 0
        jobs = (
            await session.scalars(
                select(Job)
                .where(Job.status.in_(("pending", "running", "failed")))
                .order_by(Job.id.desc())
                .offset(page * PAGE)
                .limit(PAGE + 1)
            )
        ).all()
    has_next = len(jobs) > PAGE
    jobs = jobs[:PAGE]
    items = [(str(j.id), f"#{j.id} {j.kind} · {j.status}") for j in jobs]
    text = decorate(
        t("adm_jobs_title", lang, count=total, robot="{robot}")
        + (t("adm_empty", lang) if not items else "")
    )
    await safe_edit(query, text, admin_list_kb("job", items, page=page, has_next=has_next, lang=lang))


@router.callback_query(AdmCB.filter((F.section == "job") & (F.action == "open")))
async def admin_job_open(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "operator"):
        return
    lang = db_user.lang or "ru"
    jid = int(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        j = await session.get(Job, jid)
        if not j:
            await query.answer(t("not_found", lang), show_alert=True)
            return
    text = (
        f"<b>Job #{j.id}</b>\n\n"
        f"├ Kind: {j.kind} / {j.class_}\n"
        f"├ Status: <b>{j.status}</b>\n"
        f"├ Order/Server: {j.order_id or '—'} / {j.server_id or '—'}\n"
        f"├ Attempts: {j.attempts}/{j.max_attempts}\n"
        f"╰ Error: {escape((j.last_error or '—')[:200])}"
    )
    await safe_edit(query, text, admin_job_kb(j.id, lang=lang))


@router.callback_query(AdmCB.filter((F.section == "job") & (F.action == "cancel")))
async def admin_job_cancel(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "operator"):
        return
    jid = int(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        j = await session.get(Job, jid)
        if not j:
            await query.answer(t("not_found", db_user.lang), show_alert=True)
            return
        j.status = "cancelled"
        await session.commit()
    await query.answer("cancelled")
    await admin_jobs_list(query, AdmCB(section="job", action="list", arg="0"), db_user, admin_role)


# ── Finance / Settings / Audit / Broadcast ────────────────────────────


@router.callback_query(AdmCB.filter(F.section == "fin"))
async def admin_fin(query: CallbackQuery, db_user, admin_role, redis) -> None:
    if not _require(admin_role, "owner"):
        await query.answer("owner", show_alert=True)
        return
    lang = db_user.lang or "ru"
    factory = get_session_factory()
    async with factory() as session:
        topups = await session.scalar(
            select(func.coalesce(func.sum(LedgerEntry.amount_usd), 0)).where(LedgerEntry.kind == "topup")
        )
        purchases = await session.scalar(
            select(func.coalesce(func.sum(LedgerEntry.amount_usd), 0)).where(
                LedgerEntry.kind.in_(("purchase", "renewal"))
            )
        )
    pb = await redis.get("partner:balance") or "—"
    text = (
        f"<b>Финансы</b>\n\n"
        f"├ Пополнения: {format_usd(topups or 0)}\n"
        f"├ Списания: {format_usd(purchases or 0)}\n"
        f"╰ Партнёр: {pb}"
    )
    await safe_edit(query, text, admin_home_kb(lang))


@router.callback_query(AdmCB.filter((F.section == "set") & (F.action == "open")))
async def admin_settings(query: CallbackQuery, db_user, admin_role) -> None:
    if not _require(admin_role, "owner"):
        await query.answer("owner", show_alert=True)
        return
    lang = db_user.lang or "ru"
    markup = settings_store.decimal("markup")
    fee = settings_store.decimal("gateway_fee")
    example = client_price(Decimal("4.00"), markup, fee)
    text = decorate(
        t(
            "adm_settings",
            lang,
            markup=markup,
            fee=fee,
            example=format_usd(example),
            maint="ON" if settings_store.bool("maintenance") else "OFF",
            min_topup=settings_store.get("min_topup"),
            max_servers=settings_store.get("max_servers_per_user"),
            hammer="{hammer}",
        )
    )
    await safe_edit(query, text, admin_settings_kb(lang))


@router.callback_query(AdmCB.filter((F.section == "set") & (F.action.in_({"mk+", "mk-", "fee+", "fee-", "maint"}))))
async def admin_settings_act(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "owner"):
        await query.answer("owner", show_alert=True)
        return
    factory = get_session_factory()
    async with factory() as session:
        if callback_data.action == "mk+":
            v = settings_store.decimal("markup") + Decimal("0.1")
            await settings_store.set(session, "markup", str(v), updated_by=db_user.id)
        elif callback_data.action == "mk-":
            v = max(Decimal("1.0"), settings_store.decimal("markup") - Decimal("0.1"))
            await settings_store.set(session, "markup", str(v), updated_by=db_user.id)
        elif callback_data.action == "fee+":
            v = min(Decimal("0.20"), settings_store.decimal("gateway_fee") + Decimal("0.01"))
            await settings_store.set(session, "gateway_fee", str(v), updated_by=db_user.id)
        elif callback_data.action == "fee-":
            v = max(Decimal("0"), settings_store.decimal("gateway_fee") - Decimal("0.01"))
            await settings_store.set(session, "gateway_fee", str(v), updated_by=db_user.id)
        elif callback_data.action == "maint":
            await settings_store.set(
                session, "maintenance", not settings_store.bool("maintenance"), updated_by=db_user.id
            )
        await session.commit()
    await admin_settings(query, db_user, admin_role)


@router.callback_query(AdmCB.filter((F.section == "aud") & (F.action == "list")))
async def admin_audit(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "operator"):
        await query.answer("operator+", show_alert=True)
        return
    lang = db_user.lang or "ru"
    page = _page(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.scalars(
                select(AuditLog).order_by(AuditLog.id.desc()).offset(page * PAGE).limit(PAGE + 1)
            )
        ).all()
    has_next = len(rows) > PAGE
    rows = rows[:PAGE]
    items = [
        (str(r.id), f"{r.at:%d.%m %H:%M} {r.action}"[:40])
        for r in rows
    ]
    text = f"<b>Аудит</b>" + (t("adm_empty", lang) if not items else "")
    await safe_edit(query, text, admin_list_kb("aud", items, page=page, has_next=has_next, lang=lang))


@router.callback_query(AdmCB.filter((F.section == "aud") & (F.action == "open")))
async def admin_audit_open(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "operator"):
        return
    lang = db_user.lang or "ru"
    aid = int(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        r = await session.get(AuditLog, aid)
        if not r:
            await query.answer(t("not_found", lang), show_alert=True)
            return
    text = (
        f"<b>Аудит #{r.id}</b>\n\n"
        f"├ {r.at:%d.%m.%Y %H:%M:%S}\n"
        f"├ {r.actor_kind}:{r.actor_id}\n"
        f"├ {escape(r.action)}\n"
        f"╰ {escape(str(r.after or r.before or '')[:300])}"
    )
    from aiogram.types import InlineKeyboardMarkup
    from app.bot.keyboards import _ib

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [_ib(t("btn_back", lang), AdmCB(section="aud", action="list", arg="0").pack(), "down")]
        ]
    )
    await safe_edit(query, text, kb)


@router.callback_query(AdmCB.filter(F.section == "bc"))
async def admin_broadcast(query: CallbackQuery, state: FSMContext, db_user, admin_role) -> None:
    if not _require(admin_role, "owner"):
        await query.answer("owner", show_alert=True)
        return
    lang = db_user.lang or "ru"
    await state.set_state(AdminBC.waiting)
    await query.message.answer(t("adm_bc_ask", lang))
    await query.answer()


@router.message(AdminBC.waiting)
async def admin_bc_send(message: Message, state: FSMContext, db_user, admin_role, bot) -> None:
    """Relay whatever the admin sent (text/markdown/photo/media) via copy_message."""
    if not _require(admin_role, "owner"):
        await state.clear()
        return
    await state.clear()
    if not (
        message.text
        or message.caption
        or message.photo
        or message.video
        or message.document
        or message.animation
        or message.audio
        or message.voice
        or message.sticker
        or message.video_note
    ):
        await message.answer("?")
        return
    factory = get_session_factory()
    async with factory() as session:
        users = (await session.scalars(select(User).where(User.banned.is_(False)))).all()
        ids = [u.tg_id for u in users]
    sent = 0
    fail = 0
    for tg_id in ids:
        if tg_id == message.from_user.id:
            continue
        try:
            await bot.copy_message(
                chat_id=tg_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            sent += 1
        except Exception:
            fail += 1
    await message.answer(f"Sent {sent}, fail {fail}")


# ── Wallets ───────────────────────────────────────────────────────────


@router.callback_query(AdmCB.filter((F.section == "wal") & (F.action == "list")))
@router.callback_query(AdmCB.filter((F.section == "wal") & (F.action == "open") & (F.arg == "-")))
async def admin_wallets_list(query: CallbackQuery, db_user, admin_role) -> None:
    if not _require(admin_role, "owner"):
        await query.answer("owner", show_alert=True)
        return
    lang = db_user.lang or "ru"
    wallets = get_wallets()
    text = decorate(
        t(
            "admin_wallets",
            lang,
            list="Нажмите сеть, чтобы включить/выключить или задать адрес.",
            wallet="{wallet}",
        )
    )
    await safe_edit(query, text, admin_wallets_kb(wallets, lang))


@router.callback_query(AdmCB.filter((F.section == "wal") & (F.action == "open")))
async def admin_wallet_open(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "owner"):
        return
    lang = db_user.lang or "ru"
    wid = callback_data.arg
    if wid == "-":
        await admin_wallets_list(query, db_user, admin_role)
        return
    w = find_wallet(wid)
    if not w:
        await query.answer(t("not_found", lang), show_alert=True)
        return
    on = bool(w.get("enabled") and w.get("address"))
    text = decorate(
        t(
            "adm_wallet_card",
            lang,
            label=escape(str(w.get("label") or wid)),
            wid=escape(wid),
            network=escape(str(w.get("network") or "—")),
            asset=escape(str(w.get("asset") or "—")),
            address=escape(str(w.get("address") or "(пусто)")),
            status="ON" if on else "OFF",
            wallet="{wallet}",
        )
    )
    await safe_edit(
        query,
        text,
        admin_wallet_kb(wid, enabled=on, has_addr=bool(w.get("address")), lang=lang),
    )


@router.callback_query(AdmCB.filter((F.section == "wal") & (F.action == "tog")))
async def admin_wallet_tog(query: CallbackQuery, callback_data: AdmCB, db_user, admin_role) -> None:
    if not _require(admin_role, "owner"):
        return
    wid = callback_data.arg
    w = find_wallet(wid)
    if not w:
        await query.answer(t("not_found", db_user.lang), show_alert=True)
        return
    if not (w.get("address") or "").strip():
        await query.answer("Сначала задайте адрес", show_alert=True)
        return
    new_state = not bool(w.get("enabled"))
    wallets = set_wallet_enabled(wid, new_state)
    factory = get_session_factory()
    async with factory() as session:
        await settings_store.set(session, "crypto_wallets", wallets, updated_by=db_user.id)
        await _audit(
            session,
            actor_id=db_user.tg_id,
            action="wallet:toggle",
            subject_type="wallet",
            after={"id": wid, "enabled": new_state},
        )
        await session.commit()
    await query.answer("ON" if new_state else "OFF")
    await admin_wallet_open(query, AdmCB(section="wal", action="open", arg=wid), db_user, admin_role)


@router.callback_query(AdmCB.filter((F.section == "wal") & (F.action == "addr")))
async def admin_wallet_addr(query: CallbackQuery, callback_data: AdmCB, state: FSMContext, db_user, admin_role) -> None:
    if not _require(admin_role, "owner"):
        return
    await state.set_state(AdminAddr.waiting)
    await state.update_data(wallet_id=callback_data.arg)
    await query.message.answer(t("adm_addr_ask", db_user.lang, network=callback_data.arg))
    await query.answer()


@router.message(AdminAddr.waiting)
async def admin_addr_save(message: Message, state: FSMContext, db_user, admin_role) -> None:
    if not _require(admin_role, "owner"):
        await state.clear()
        return
    data = await state.get_data()
    wid = data.get("wallet_id") or ""
    await state.clear()
    address = (message.text or "").strip()
    wallets = upsert_wallet(wid, address)
    factory = get_session_factory()
    async with factory() as session:
        await settings_store.set(session, "crypto_wallets", wallets, updated_by=db_user.id)
        await session.commit()
    await message.answer(t("admin_wallet_set", db_user.lang, network=wid))


@router.message(Command("set_wallet"))
async def set_wallet_cmd(message: Message, db_user, admin_role) -> None:
    if not _require(admin_role, "owner"):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Usage: /set_wallet USDT_TRC20 TXxxxx…")
        return
    network_id, address = parts[1].upper(), parts[2].strip()
    wallets = upsert_wallet(network_id, address)
    factory = get_session_factory()
    async with factory() as session:
        await settings_store.set(session, "crypto_wallets", wallets, updated_by=db_user.id)
        await session.commit()
    await message.answer(t("admin_wallet_set", db_user.lang, network=network_id))


@router.message(Command("fake_pay"))
async def fake_pay(message: Message, db_user, admin_role) -> None:
    from app.config import get_settings
    from app.payments import get_gateway
    from app.payments.fake import FakePaymentGateway

    if not get_settings().is_dev or not _require(admin_role, "owner"):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Usage: /fake_pay <invoice_id>")
        return
    invoice_id = int(parts[1])
    gateway = get_gateway()
    factory = get_session_factory()
    async with factory() as session:
        inv = await session.get(Invoice, invoice_id)
        if not inv:
            await message.answer("Invoice not found")
            return
        if isinstance(gateway, FakePaymentGateway) and inv.gateway_invoice_id:
            remote = gateway.mark_paid(inv.gateway_invoice_id)
        else:
            remote = GatewayInvoice(
                gateway_invoice_id=inv.gateway_invoice_id or str(inv.id),
                status="paid",
                paid_usd=inv.amount_usd,
                pay_amount=inv.pay_amount,
                address=inv.address,
                asset=inv.asset,
                network=inv.network,
                expires_at=inv.expires_at,
                txid=f"fake-{invoice_id}",
            )
        await apply_invoice_state(session, inv, remote)
        await session.commit()
    await message.answer(f"Invoice #{invoice_id} marked paid")
