from __future__ import annotations

from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from app.bot.keyboards import (
    NavCB,
    ProfCB,
    autorenew_kb,
    history_kb,
    profile_kb,
    promo_kb,
    referral_kb,
)
from app.bot.render import show_banner
from app.bot.texts import t
from app.bot.ui.screens import autorenew_text, decorate, profile_text, promo_text, referral_text
from app.core.ledger import LedgerKind, post_entry
from app.core.money import format_usd, money
from app.db.models import LedgerEntry, Order, PromoCode, PromoRedemption, Server, User
from app.db.session import get_session_factory

router = Router()
PAGE = 8


class PromoInput(StatesGroup):
    waiting = State()


async def show_profile(event: Message | CallbackQuery, db_user) -> None:
    lang = db_user.lang or "ru"
    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, db_user.id)
        servers = (await session.scalars(select(Server).where(Server.user_id == db_user.id))).all()
        refs = (
            await session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.ref_source == f"ref_{db_user.tg_id}")
            )
            or 0
        )
        auto_on = sum(1 for s in servers if s.auto_renew and not s.cancelled)
        text = profile_text(
            user or db_user,
            servers_count=len(servers),
            refs_count=int(refs),
            auto_renew_on=auto_on,
        )
    await show_banner(event, text, profile_kb(lang), banner="profile", lang=lang)


@router.callback_query(NavCB.filter(F.to == "profile"))
async def nav_profile(query: CallbackQuery, state: FSMContext, db_user) -> None:
    await state.clear()
    await show_profile(query, db_user)


@router.callback_query(ProfCB.filter(F.action == "ref"))
async def profile_ref(query: CallbackQuery, db_user, bot) -> None:
    lang = db_user.lang or "ru"
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{db_user.tg_id}"
    factory = get_session_factory()
    async with factory() as session:
        refs = (
            await session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.ref_source == f"ref_{db_user.tg_id}")
            )
            or 0
        )
        earned = (
            await session.scalar(
                select(func.coalesce(func.sum(LedgerEntry.amount_usd), 0)).where(
                    LedgerEntry.user_id == db_user.id,
                    LedgerEntry.kind == "promo",
                    LedgerEntry.reason.like("referral:%"),
                )
            )
            or Decimal("0")
        )
    text = referral_text(link=link, refs=int(refs), earned=format_usd(earned), lang=lang)
    await show_banner(query, text, referral_kb(lang), banner="referral", lang=lang)


@router.callback_query(ProfCB.filter(F.action == "orders"))
async def profile_orders(query: CallbackQuery, callback_data: ProfCB, db_user) -> None:
    lang = db_user.lang or "ru"
    page = int(callback_data.arg) if callback_data.arg.isdigit() else 0
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.scalars(
                select(Order)
                .where(Order.user_id == db_user.id, Order.status != "draft")
                .order_by(Order.id.desc())
                .offset(page * PAGE)
                .limit(PAGE + 1)
            )
        ).all()
    has_next = len(rows) > PAGE
    rows = rows[:PAGE]
    if not rows:
        text = decorate(t("orders_title", lang, bag="{bag}") + t("orders_empty", lang))
    else:
        lines = [
            f"#{o.id} · {o.kind} · <b>{o.status}</b> · {format_usd(o.price_usd or 0)}"
            for o in rows
        ]
        text = decorate(t("orders_title", lang, bag="{bag}") + "\n\n" + "\n".join(lines))
    await show_banner(
        query,
        text,
        history_kb(page=page, has_next=has_next, action="orders"),
        banner="profile",
        lang=lang,
    )


@router.callback_query(ProfCB.filter(F.action == "ledger"))
async def profile_ledger(query: CallbackQuery, callback_data: ProfCB, db_user) -> None:
    lang = db_user.lang or "ru"
    page = int(callback_data.arg) if callback_data.arg.isdigit() else 0
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.scalars(
                select(LedgerEntry)
                .where(LedgerEntry.user_id == db_user.id)
                .order_by(LedgerEntry.id.desc())
                .offset(page * PAGE)
                .limit(PAGE + 1)
            )
        ).all()
    has_next = len(rows) > PAGE
    rows = rows[:PAGE]
    kind_map = {
        "topup": "topup" if lang == "en" else "пополнение",
        "purchase": "purchase" if lang == "en" else "покупка",
        "renewal": "renewal" if lang == "en" else "продление",
        "refund": "refund" if lang == "en" else "возврат",
        "promo": "promo" if lang == "en" else "промо",
        "adjust": "adjust" if lang == "en" else "правка",
    }
    if not rows:
        text = decorate(t("ledger_title", lang, chart="{chart}") + t("ledger_empty", lang))
    else:
        lines = [
            f"{r.created_at:%d.%m %H:%M} · {kind_map.get(r.kind, r.kind)} · "
            f"<b>{format_usd(r.amount_usd)}</b>"
            for r in rows
        ]
        text = decorate(t("ledger_title", lang, chart="{chart}") + "\n\n" + "\n".join(lines))
    await show_banner(
        query,
        text,
        history_kb(page=page, has_next=has_next, action="ledger"),
        banner="balance",
        lang=lang,
    )


@router.callback_query(ProfCB.filter(F.action == "promo"))
async def profile_promo(query: CallbackQuery, state: FSMContext, db_user) -> None:
    lang = db_user.lang or "ru"
    await state.clear()
    await show_banner(query, promo_text(lang), promo_kb(lang), banner="promo", lang=lang)


@router.callback_query(ProfCB.filter(F.action == "promo_in"))
async def profile_promo_in(query: CallbackQuery, state: FSMContext, db_user) -> None:
    lang = db_user.lang or "ru"
    await state.set_state(PromoInput.waiting)
    await query.message.answer(t("promo_ask", lang))
    await query.answer()


@router.message(PromoInput.waiting)
async def promo_apply(message: Message, state: FSMContext, db_user) -> None:
    lang = db_user.lang or "ru"
    code = (message.text or "").strip().upper()
    await state.clear()
    if not code or len(code) > 32:
        await message.answer(t("promo_bad", lang))
        return
    factory = get_session_factory()
    async with factory() as session:
        promo = await session.scalar(
            select(PromoCode).where(PromoCode.code == code, PromoCode.active.is_(True))
        )
        if promo is None:
            await message.answer(t("promo_bad", lang))
            return
        if promo.max_uses and promo.used_count >= promo.max_uses:
            await message.answer(t("promo_gone", lang))
            return
        used = await session.scalar(
            select(PromoRedemption).where(
                PromoRedemption.user_id == db_user.id,
                PromoRedemption.promo_id == promo.id,
            )
        )
        if used:
            await message.answer(t("promo_used", lang))
            return
        await post_entry(
            session,
            user_id=db_user.id,
            kind=LedgerKind.PROMO,
            amount=money(promo.amount_usd),
            uniq_key=f"promo:{promo.id}:{db_user.id}",
            reason=f"promo:{promo.code}",
        )
        session.add(PromoRedemption(user_id=db_user.id, promo_id=promo.id))
        promo.used_count += 1
        await session.commit()
        amount = promo.amount_usd
    await message.answer(t("promo_ok", lang, amount=format_usd(amount)))
    await show_profile(message, db_user)


@router.callback_query(ProfCB.filter(F.action == "autorenew"))
async def profile_autorenew(query: CallbackQuery, db_user) -> None:
    lang = db_user.lang or "ru"
    factory = get_session_factory()
    async with factory() as session:
        servers = (
            await session.scalars(
                select(Server).where(Server.user_id == db_user.id).order_by(Server.id.desc())
            )
        ).all()
        items = [
            (s.id, s.display_name[:28], bool(s.auto_renew and not s.cancelled)) for s in servers
        ]
    await show_banner(query, autorenew_text(lang), autorenew_kb(items), banner="servers", lang=lang)


@router.callback_query(ProfCB.filter(F.action == "ar_tog"))
async def profile_ar_toggle(query: CallbackQuery, callback_data: ProfCB, db_user) -> None:
    sid = int(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        server = await session.get(Server, sid)
        if not server or server.user_id != db_user.id:
            await query.answer(t("not_found", db_user.lang), show_alert=True)
            return
        if server.auto_renew and not server.cancelled:
            server.auto_renew = False
            server.cancelled = True
            await query.answer("off")
        else:
            server.auto_renew = True
            server.cancelled = False
            await query.answer("on")
        await session.commit()
    await profile_autorenew(query, db_user)
