"""Affiliate partner panel (admin-granted resellers)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from app.bot.keyboards import (
    NavCB,
    PartnerCB,
    _ib,
    back_partner_row,
    partner_home_kb,
    partner_refs_kb,
    partner_stats_kb,
    partner_transfer_servers_kb,
    partner_withdraw_kb,
)
from app.bot.render import show_banner
from app.bot.texts import t
from app.bot.ui.screens import decorate
from app.core.affiliate import (
    ATTACH_INVITE,
    ATTACH_TRANSFER,
    attach_client,
    create_withdraw,
    rate_for_attach,
)
from app.core.money import D, format_usd, money
from app.core.settings_store import settings_store
from app.db.models import PartnerLedgerEntry, PartnerWithdraw, Server, User
from app.db.session import get_session_factory
from app.jobs.notify import enqueue_notify

router = Router()
PAGE = 8


def _rate_pct(kind: str) -> int:
    return int(rate_for_attach(kind) * 100)


class PartnerTransfer(StatesGroup):
    waiting_tg = State()


class PartnerWithdrawFSM(StatesGroup):
    waiting_amount = State()
    waiting_details = State()


def _partner_gate(db_user) -> bool:
    return bool(getattr(db_user, "is_partner", False))


async def _earned_total(session, partner_id: int) -> Decimal:
    val = await session.scalar(
        select(func.coalesce(func.sum(PartnerLedgerEntry.amount_usd), 0)).where(
            PartnerLedgerEntry.partner_user_id == partner_id,
            PartnerLedgerEntry.kind == "commission",
        )
    )
    return D(val or 0)


async def _earned_month(session, partner_id: int) -> Decimal:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    val = await session.scalar(
        select(func.coalesce(func.sum(PartnerLedgerEntry.amount_usd), 0)).where(
            PartnerLedgerEntry.partner_user_id == partner_id,
            PartnerLedgerEntry.kind == "commission",
            PartnerLedgerEntry.created_at >= start,
        )
    )
    return D(val or 0)


async def _clients_count(session, partner_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(User).where(User.referred_by_id == partner_id)
        )
        or 0
    )


async def show_partner_home(event: Message | CallbackQuery, db_user) -> None:
    lang = db_user.lang or "en"
    if not _partner_gate(db_user):
        if isinstance(event, CallbackQuery):
            await event.answer(t("partner_denied", lang), show_alert=True)
        return
    factory = get_session_factory()
    async with factory() as session:
        u = await session.get(User, db_user.id)
        if not u or not u.is_partner:
            if isinstance(event, CallbackQuery):
                await event.answer(t("partner_denied", lang), show_alert=True)
            return
        bal = format_usd(u.partner_balance_usd)
        earned = format_usd(await _earned_total(session, u.id))
        month = format_usd(await _earned_month(session, u.id))
        clients = await _clients_count(session, u.id)
        rate_inv = _rate_pct(ATTACH_INVITE)
        rate_tr = _rate_pct(ATTACH_TRANSFER)
        db_user.partner_balance_usd = u.partner_balance_usd
        db_user.is_partner = u.is_partner
    text = decorate(
        t(
            "partner_home",
            lang,
            balance=bal,
            earned=earned,
            month=month,
            clients=clients,
            rate_invite=rate_inv,
            rate_transfer=rate_tr,
            crown="{crown}",
            wallet="{wallet}",
        )
    )
    await show_banner(event, text, partner_home_kb(lang), banner="profile", lang=lang)


@router.callback_query(PartnerCB.filter(F.action == "home"))
@router.callback_query(NavCB.filter(F.to == "partner"))
async def partner_home_cb(query: CallbackQuery, state: FSMContext, db_user) -> None:
    await state.clear()
    await show_partner_home(query, db_user)


# ── Invite link ───────────────────────────────────────────────────────


@router.callback_query(PartnerCB.filter(F.action == "link"))
async def partner_link(query: CallbackQuery, db_user, bot) -> None:
    lang = db_user.lang or "en"
    if not _partner_gate(db_user):
        await query.answer(t("partner_denied", lang), show_alert=True)
        return
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{db_user.tg_id}"
    rate = _rate_pct(ATTACH_INVITE)
    text = decorate(
        t("partner_link", lang, link=escape(link), rate=rate, pin="{pin}", users="{users}")
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[back_partner_row(lang)])
    await show_banner(query, text, kb, banner="referral", lang=lang)


# ── Referrals ─────────────────────────────────────────────────────────


@router.callback_query(PartnerCB.filter(F.action == "refs"))
async def partner_refs(query: CallbackQuery, callback_data: PartnerCB, db_user) -> None:
    lang = db_user.lang or "en"
    if not _partner_gate(db_user):
        await query.answer(t("partner_denied", lang), show_alert=True)
        return
    page = int(callback_data.arg) if callback_data.arg.isdigit() else 0
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.scalars(
                select(User)
                .where(User.referred_by_id == db_user.id)
                .order_by(User.id.desc())
                .offset(page * PAGE)
                .limit(PAGE + 1)
            )
        ).all()
        has_next = len(rows) > PAGE
        rows = rows[:PAGE]
        items: list[tuple[str, str]] = []
        for c in rows:
            earned = await session.scalar(
                select(func.coalesce(func.sum(PartnerLedgerEntry.amount_usd), 0)).where(
                    PartnerLedgerEntry.partner_user_id == db_user.id,
                    PartnerLedgerEntry.client_user_id == c.id,
                    PartnerLedgerEntry.kind == "commission",
                )
            )
            kind = c.attach_kind or "?"
            uname = f"@{c.username}" if c.username else str(c.tg_id)
            label = f"{uname} · {kind} · {format_usd(earned or 0)}"
            items.append((str(c.id), label[:60]))
        total = await _clients_count(session, db_user.id)
    if not items:
        text = decorate(
            t("partner_refs_title", lang, users="{users}", count=total)
            + t("partner_refs_empty", lang)
        )
    else:
        text = decorate(t("partner_refs_title", lang, users="{users}", count=total))
    await show_banner(
        query,
        text,
        partner_refs_kb(items, page=page, has_next=has_next, lang=lang),
        banner="referral",
        lang=lang,
    )


@router.callback_query(PartnerCB.filter(F.action == "ref_open"))
async def partner_ref_open(query: CallbackQuery, callback_data: PartnerCB, db_user) -> None:
    lang = db_user.lang or "en"
    if not _partner_gate(db_user):
        await query.answer(t("partner_denied", lang), show_alert=True)
        return
    cid = int(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        c = await session.get(User, cid)
        if not c or c.referred_by_id != db_user.id:
            await query.answer(t("not_found", lang), show_alert=True)
            return
        earned = await session.scalar(
            select(func.coalesce(func.sum(PartnerLedgerEntry.amount_usd), 0)).where(
                PartnerLedgerEntry.partner_user_id == db_user.id,
                PartnerLedgerEntry.client_user_id == c.id,
                PartnerLedgerEntry.kind == "commission",
            )
        )
        srv_n = (
            await session.scalar(
                select(func.count()).select_from(Server).where(Server.user_id == c.id)
            )
            or 0
        )
    uname = f"@{escape(c.username)}" if c.username else "—"
    rate = _rate_pct(c.attach_kind or ATTACH_INVITE)
    text = decorate(
        t(
            "partner_ref_card",
            lang,
            id=c.id,
            tg_id=c.tg_id,
            username=uname,
            name=escape(c.first_name or "—"),
            kind=c.attach_kind or "—",
            rate=rate,
            earned=format_usd(earned or 0),
            servers=srv_n,
            users="{users}",
        )
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _ib(
                    t("partner_btn_detach", lang),
                    PartnerCB(action="detach", arg=str(c.id)).pack(),
                    "block",
                )
            ],
            [
                _ib(
                    t("btn_back", lang),
                    PartnerCB(action="refs", arg="0").pack(),
                    "down",
                )
            ],
            back_partner_row(lang),
        ]
    )
    await show_banner(query, text, kb, banner="referral", lang=lang)


@router.callback_query(PartnerCB.filter(F.action == "detach"))
async def partner_detach(query: CallbackQuery, callback_data: PartnerCB, db_user) -> None:
    lang = db_user.lang or "en"
    if not _partner_gate(db_user):
        await query.answer(t("partner_denied", lang), show_alert=True)
        return
    cid = int(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        c = await session.get(User, cid)
        if not c or c.referred_by_id != db_user.id:
            await query.answer(t("not_found", lang), show_alert=True)
            return
        c.referred_by_id = None
        c.attach_kind = None
        await session.commit()
    await query.answer(t("partner_detach_ok", lang))
    await partner_refs(query, PartnerCB(action="refs", arg="0"), db_user)


# ── Transfer server ───────────────────────────────────────────────────


@router.callback_query(PartnerCB.filter(F.action == "xfer"))
async def partner_transfer_list(query: CallbackQuery, callback_data: PartnerCB, db_user) -> None:
    lang = db_user.lang or "en"
    if not _partner_gate(db_user):
        await query.answer(t("partner_denied", lang), show_alert=True)
        return
    page = int(callback_data.arg) if callback_data.arg.isdigit() else 0
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.scalars(
                select(Server)
                .where(Server.user_id == db_user.id, Server.cancelled.is_(False))
                .order_by(Server.id.desc())
                .offset(page * PAGE)
                .limit(PAGE + 1)
            )
        ).all()
    has_next = len(rows) > PAGE
    rows = rows[:PAGE]
    items = [
        (s.id, f"#{s.id} · {s.display_name or s.ip or s.partner_id or 'server'}"[:48])
        for s in rows
    ]
    rate = _rate_pct(ATTACH_TRANSFER)
    if not items:
        text = decorate(
            t("partner_xfer_title", lang, rate=rate, monitor="{monitor}")
            + t("partner_xfer_empty", lang)
        )
    else:
        text = decorate(
            t("partner_xfer_title", lang, rate=rate, monitor="{monitor}")
            + t("partner_xfer_hint", lang)
        )
    await show_banner(
        query,
        text,
        partner_transfer_servers_kb(items, page=page, has_next=has_next, lang=lang),
        banner="servers",
        lang=lang,
    )


@router.callback_query(PartnerCB.filter(F.action == "xfer_pick"))
async def partner_transfer_pick(
    query: CallbackQuery, callback_data: PartnerCB, state: FSMContext, db_user
) -> None:
    lang = db_user.lang or "en"
    if not _partner_gate(db_user):
        await query.answer(t("partner_denied", lang), show_alert=True)
        return
    sid = int(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        s = await session.get(Server, sid)
        if not s or s.user_id != db_user.id or s.cancelled:
            await query.answer(t("not_found", lang), show_alert=True)
            return
    await state.set_state(PartnerTransfer.waiting_tg)
    await state.update_data(xfer_server_id=sid)
    await query.message.answer(t("partner_xfer_ask_tg", lang, sid=sid))
    await query.answer()


@router.message(PartnerTransfer.waiting_tg)
async def partner_transfer_do(message: Message, state: FSMContext, db_user) -> None:
    lang = db_user.lang or "en"
    if not _partner_gate(db_user):
        await state.clear()
        return
    data = await state.get_data()
    sid = int(data.get("xfer_server_id") or 0)
    await state.clear()
    raw = (message.text or "").strip().replace("@", "")
    if not raw.isdigit():
        await message.answer(t("partner_xfer_bad_tg", lang))
        return
    target_tg = int(raw)
    if target_tg == db_user.tg_id:
        await message.answer(t("partner_xfer_self", lang))
        return
    factory = get_session_factory()
    async with factory() as session:
        partner = await session.get(User, db_user.id)
        server = await session.get(Server, sid)
        if not partner or not partner.is_partner or not server or server.user_id != partner.id:
            await message.answer(t("not_found", lang))
            return
        client = await session.scalar(select(User).where(User.tg_id == target_tg))
        if client is None:
            await message.answer(t("partner_xfer_no_user", lang))
            return
        server.user_id = client.id
        ok = await attach_client(
            session, client=client, partner=partner, kind=ATTACH_TRANSFER, force=True
        )
        await session.commit()
    rate = _rate_pct(ATTACH_TRANSFER)
    if ok:
        await message.answer(
            t("partner_xfer_ok", lang, sid=sid, tg_id=target_tg, rate=rate)
        )
    else:
        await message.answer(t("partner_xfer_attach_fail", lang, sid=sid, tg_id=target_tg))


# ── Balance / withdraw ────────────────────────────────────────────────


@router.callback_query(PartnerCB.filter(F.action == "bal"))
async def partner_balance(query: CallbackQuery, state: FSMContext, db_user) -> None:
    await state.clear()
    lang = db_user.lang or "en"
    if not _partner_gate(db_user):
        await query.answer(t("partner_denied", lang), show_alert=True)
        return
    factory = get_session_factory()
    async with factory() as session:
        u = await session.get(User, db_user.id)
        bal = format_usd(u.partner_balance_usd if u else 0)
        earned = format_usd(await _earned_total(session, db_user.id))
        pending = (
            await session.scalar(
                select(func.count())
                .select_from(PartnerWithdraw)
                .where(
                    PartnerWithdraw.partner_user_id == db_user.id,
                    PartnerWithdraw.status == "pending",
                )
            )
            or 0
        )
        min_w = settings_store.get("partner_min_withdraw", "10") or "10"
    text = decorate(
        t(
            "partner_balance",
            lang,
            balance=bal,
            earned=earned,
            pending=pending,
            min_withdraw=min_w,
            wallet="{wallet}",
        )
    )
    await show_banner(query, text, partner_withdraw_kb(lang), banner="balance", lang=lang)


@router.callback_query(PartnerCB.filter(F.action == "wd"))
async def partner_withdraw_start(
    query: CallbackQuery, state: FSMContext, db_user
) -> None:
    lang = db_user.lang or "en"
    if not _partner_gate(db_user):
        await query.answer(t("partner_denied", lang), show_alert=True)
        return
    min_w = settings_store.get("partner_min_withdraw", "10") or "10"
    await state.set_state(PartnerWithdrawFSM.waiting_amount)
    await query.message.answer(t("partner_wd_ask_amount", lang, min=min_w))
    await query.answer()


@router.message(PartnerWithdrawFSM.waiting_amount)
async def partner_wd_amount(message: Message, state: FSMContext, db_user) -> None:
    lang = db_user.lang or "en"
    try:
        amount = money(D((message.text or "").replace("$", "").replace(",", ".").strip()))
    except (InvalidOperation, ValueError):
        await message.answer(t("partner_wd_bad_amount", lang))
        return
    min_w = settings_store.decimal("partner_min_withdraw")
    if amount < min_w:
        await message.answer(t("partner_wd_min", lang, min=format_usd(min_w)))
        return
    factory = get_session_factory()
    async with factory() as session:
        u = await session.get(User, db_user.id)
        bal = D(u.partner_balance_usd) if u else Decimal("0")
    if amount > bal:
        await message.answer(t("partner_wd_insufficient", lang, balance=format_usd(bal)))
        return
    await state.update_data(wd_amount=str(amount))
    await state.set_state(PartnerWithdrawFSM.waiting_details)
    await message.answer(t("partner_wd_ask_details", lang))


@router.message(PartnerWithdrawFSM.waiting_details)
async def partner_wd_details(message: Message, state: FSMContext, db_user) -> None:
    lang = db_user.lang or "en"
    data = await state.get_data()
    await state.clear()
    details = (message.text or "").strip()
    if len(details) < 5:
        await message.answer(t("partner_wd_bad_details", lang))
        return
    try:
        amount = money(D(data.get("wd_amount") or "0"))
    except (InvalidOperation, ValueError):
        await message.answer(t("partner_wd_bad_amount", lang))
        return
    factory = get_session_factory()
    async with factory() as session:
        partner = await session.get(User, db_user.id)
        if not partner or not partner.is_partner:
            await message.answer(t("partner_denied", lang))
            return
        try:
            w = await create_withdraw(
                session, partner=partner, amount=amount, details=details
            )
        except ValueError as e:
            code = str(e)
            if code == "insufficient":
                await message.answer(
                    t(
                        "partner_wd_insufficient",
                        lang,
                        balance=format_usd(partner.partner_balance_usd),
                    )
                )
            else:
                await message.answer(t("partner_wd_bad_amount", lang))
            return
        uname = f"@{partner.username}" if partner.username else "—"
        await enqueue_notify(
            session,
            user_id=None,
            key=f"partner_wd_admin:{w.id}",
            ref=str(w.id),
            text=(
                f"💸 Заявка на вывод партнёра #{w.id}\n"
                f"Партнёр: {uname} (tg <code>{partner.tg_id}</code>, id {partner.id})\n"
                f"Сумма: <b>{format_usd(w.amount_usd)}</b>\n"
                f"Реквизиты: {escape(details)}"
            ),
            admin=True,
            immediate=True,
        )
        await session.commit()
        db_user.partner_balance_usd = partner.partner_balance_usd
        wid = w.id
    await message.answer(t("partner_wd_ok", lang, id=wid, amount=format_usd(amount)))


# ── Stats / ledger ────────────────────────────────────────────────────


@router.callback_query(PartnerCB.filter(F.action == "stats"))
async def partner_stats(query: CallbackQuery, callback_data: PartnerCB, db_user) -> None:
    lang = db_user.lang or "en"
    if not _partner_gate(db_user):
        await query.answer(t("partner_denied", lang), show_alert=True)
        return
    page = int(callback_data.arg) if callback_data.arg.isdigit() else 0
    factory = get_session_factory()
    async with factory() as session:
        total = format_usd(await _earned_total(session, db_user.id))
        month = format_usd(await _earned_month(session, db_user.id))
        clients = await _clients_count(session, db_user.id)
        u = await session.get(User, db_user.id)
        bal = format_usd(u.partner_balance_usd if u else 0)
        rows = (
            await session.scalars(
                select(PartnerLedgerEntry)
                .where(PartnerLedgerEntry.partner_user_id == db_user.id)
                .order_by(PartnerLedgerEntry.id.desc())
                .offset(page * PAGE)
                .limit(PAGE + 1)
            )
        ).all()
    has_next = len(rows) > PAGE
    rows = rows[:PAGE]
    lines = []
    for e in rows:
        sign = "+" if D(e.amount_usd) >= 0 else ""
        when = e.created_at.strftime("%d.%m %H:%M") if e.created_at else "—"
        lines.append(f"{when} · {e.kind} · <b>{sign}{format_usd(e.amount_usd)}</b>")
    body = "\n".join(lines) if lines else t("partner_stats_empty", lang)
    text = decorate(
        t(
            "partner_stats",
            lang,
            balance=bal,
            earned=total,
            month=month,
            clients=clients,
            chart="{chart}",
        )
        + "\n\n"
        + body
    )
    await show_banner(
        query,
        text,
        partner_stats_kb(page=page, has_next=has_next, lang=lang),
        banner="profile",
        lang=lang,
    )
