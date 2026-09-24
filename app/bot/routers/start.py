from __future__ import annotations

from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.keyboards import (
    BTN_PANEL,
    NavCB,
    TermsCB,
    lang_kb,
    main_menu,
    panel_keyboard,
    support_kb,
    terms_kb,
)
from app.bot.render import show_banner, show_home_banner
from app.bot.texts import t
from app.bot.ui.emoji import pe
from app.bot.ui.screens import decorate, home_text, support_text
from app.config import get_settings
from app.core.ledger import LedgerKind, post_entry
from app.core.money import money
from app.core.servers import is_expiring
from app.db.models import Server, User
from app.db.session import get_session_factory

router = Router()

REF_BONUS = Decimal("2.00")
REF_WELCOME = Decimal("1.00")


async def _counts(session, user_id: int) -> tuple[int, int]:
    servers = (await session.scalars(select(Server).where(Server.user_id == user_id))).all()
    exp = sum(1 for s in servers if is_expiring(s))
    return len(servers), exp


async def show_home(event: Message | CallbackQuery, user, admin_role: str | None) -> None:
    factory = get_session_factory()
    async with factory() as session:
        count, exp = await _counts(session, user.id)
        db_user = await session.get(User, user.id)
        text = home_text(db_user or user, count, exp)
        lang = (db_user or user).lang or "en"
    markup = main_menu(is_admin=bool(admin_role), lang=lang)
    await show_home_banner(event, text, markup, lang=lang)


async def _show_language_pick(event: Message | CallbackQuery, *, lang: str, onboarding: bool) -> None:
    title = t("choose_lang", lang)
    text = f"{pe('at')} <b>{title}</b>"
    kb = lang_kb(onboarding=onboarding, lang=lang)
    if isinstance(event, CallbackQuery):
        await show_banner(event, text, kb, banner="profile", lang=lang)
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")


async def _show_terms(event: Message | CallbackQuery, *, lang: str) -> None:
    settings = get_settings()
    text = decorate(t("welcome", lang, terms_url=settings.terms_url))
    if isinstance(event, CallbackQuery):
        await show_banner(event, text, terms_kb(lang), banner="profile", lang=lang)
    else:
        await event.answer(text, reply_markup=terms_kb(lang), parse_mode="HTML")


async def _grant_referral_bonuses(session, user: User) -> None:
    """Welcome bonus to new user + referrer bonus once, on terms accept."""
    if not user.ref_source or not user.ref_source.startswith("ref_"):
        return
    try:
        ref_tg = int(user.ref_source.removeprefix("ref_"))
    except ValueError:
        return
    if ref_tg == user.tg_id:
        return
    referrer = await session.scalar(select(User).where(User.tg_id == ref_tg))
    if referrer is None:
        return
    await post_entry(
        session,
        user_id=user.id,
        kind=LedgerKind.PROMO,
        amount=money(REF_WELCOME),
        uniq_key=f"referral:welcome:{user.id}",
        reason=f"referral:welcome:{ref_tg}",
    )
    await post_entry(
        session,
        user_id=referrer.id,
        kind=LedgerKind.PROMO,
        amount=money(REF_BONUS),
        uniq_key=f"referral:reward:{user.id}",
        reason=f"referral:{user.id}",
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, db_user, admin_role) -> None:
    await state.clear()
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith("ref_") and not db_user.ref_source:
        factory = get_session_factory()
        async with factory() as session:
            u = await session.get(User, db_user.id)
            if u and not u.ref_source and parts[1] != f"ref_{u.tg_id}":
                u.ref_source = parts[1]
                await session.commit()
                db_user.ref_source = parts[1]

    await message.answer(
        f"{pe('cube')} <b>ARIX Cloud</b>",
        reply_markup=panel_keyboard(),
        parse_mode="HTML",
    )
    lang = db_user.lang or "en"
    if not db_user.accepted_terms:
        # First start: language → terms → home
        await _show_language_pick(message, lang=lang, onboarding=True)
        return
    await show_home(message, db_user, admin_role)


@router.callback_query(TermsCB.filter())
async def accept_terms(query: CallbackQuery, db_user, admin_role) -> None:
    factory = get_session_factory()
    async with factory() as session:
        u = await session.get(User, db_user.id)
        if u and not u.accepted_terms:
            u.accepted_terms = True
            await _grant_referral_bonuses(session, u)
            await session.commit()
            db_user.accepted_terms = True
            await session.refresh(u)
            db_user.balance_usd = u.balance_usd
            db_user.lang = u.lang
    await show_home(query, db_user, admin_role)


@router.message(Command("menu"))
@router.message(F.text == BTN_PANEL)
async def cmd_menu(message: Message, state: FSMContext, db_user, admin_role) -> None:
    await state.clear()
    if not db_user.accepted_terms:
        await _show_language_pick(message, lang=db_user.lang or "en", onboarding=True)
        return
    await show_home(message, db_user, admin_role)


@router.callback_query(NavCB.filter(F.to == "home"))
async def nav_home(query: CallbackQuery, state: FSMContext, db_user, admin_role) -> None:
    await state.clear()
    if not db_user.accepted_terms:
        await _show_language_pick(query, lang=db_user.lang or "en", onboarding=True)
        return
    await show_home(query, db_user, admin_role)


def _manager_from_url(url: str) -> str:
    if "t.me/" in url:
        return "@" + url.rstrip("/").split("t.me/")[-1].split("?")[0]
    return "@arxixx"


@router.callback_query(NavCB.filter(F.to == "support"))
@router.callback_query(NavCB.filter(F.to == "help"))
async def nav_support(query: CallbackQuery, db_user) -> None:
    if not db_user.accepted_terms:
        await query.answer(t("terms_gate", db_user.lang or "en"), show_alert=True)
        return
    settings = get_settings()
    lang = db_user.lang or "en"
    url = settings.support_url or "https://t.me/arxixx"
    manager = _manager_from_url(url)
    text = support_text(url, manager=manager, lang=lang)
    await show_banner(query, text, support_kb(url, lang), banner="support", lang=lang)


@router.callback_query(NavCB.filter(F.to == "lang"))
async def nav_lang(query: CallbackQuery, db_user) -> None:
    lang = db_user.lang or "en"
    await _show_language_pick(query, lang=lang, onboarding=not db_user.accepted_terms)


@router.callback_query(NavCB.filter(F.to.in_({"lang_ru", "lang_en"})))
async def set_lang(query: CallbackQuery, callback_data: NavCB, db_user, admin_role) -> None:
    lang = "ru" if callback_data.to == "lang_ru" else "en"
    factory = get_session_factory()
    async with factory() as session:
        u = await session.get(User, db_user.id)
        if u:
            u.lang = lang
            await session.commit()
            db_user.lang = lang
    await query.answer(t("lang_set", lang))
    if not db_user.accepted_terms:
        await _show_terms(query, lang=lang)
        return
    await show_home(query, db_user, admin_role)


@router.message(Command("help"))
async def cmd_help(message: Message, db_user) -> None:
    if not db_user.accepted_terms:
        await _show_language_pick(message, lang=db_user.lang or "en", onboarding=True)
        return
    settings = get_settings()
    lang = db_user.lang or "en"
    url = settings.support_url or "https://t.me/arxixx"
    manager = _manager_from_url(url)
    await show_banner(
        message,
        support_text(url, manager=manager, lang=lang),
        support_kb(url, lang),
        banner="support",
        lang=lang,
    )
