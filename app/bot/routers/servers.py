from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.keyboards import NavCB, SrvCB, renew_days_kb, server_kb, servers_kb
from app.bot.render import safe_edit, show_banner
from app.bot.texts import t
from app.bot.ui.screens import decorate, server_card_text
from app.core.money import D, format_usd
from app.core.orders import confirm_renewal
from app.core.pricing import client_price
from app.core.servers import ACTIONS, action_allowed, display_status
from app.core.settings_store import settings_store
from app.db.models import Job, Server
from app.db.session import get_session_factory

router = Router()
PAGE = 6


class RenameServer(StatesGroup):
    waiting = State()


class ReinstallConfirm(StatesGroup):
    waiting = State()


@router.callback_query(NavCB.filter(F.to == "servers"))
@router.callback_query(SrvCB.filter(F.action == "list"))
@router.message(F.text == "/servers")
async def list_servers(event: Message | CallbackQuery, db_user, callback_data: SrvCB | None = None) -> None:
    page = 0
    if isinstance(callback_data, SrvCB) and callback_data.arg.isdigit():
        page = int(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        servers = (
            await session.scalars(
                select(Server).where(Server.user_id == db_user.id).order_by(Server.id.desc())
            )
        ).all()
    if not servers:
        text = decorate(t("servers_empty", db_user.lang, monitor="{monitor}"))
        from app.bot.keyboards import back_home_row
        from aiogram.types import InlineKeyboardMarkup

        lang = db_user.lang or "ru"
        await show_banner(
            event,
            text,
            InlineKeyboardMarkup(inline_keyboard=[back_home_row(lang)]),
            banner="servers",
            lang=lang,
        )
        return
    total_pages = max(1, (len(servers) + PAGE - 1) // PAGE)
    page = min(page, total_pages - 1)
    chunk = servers[page * PAGE : (page + 1) * PAGE]
    items = []
    for s in chunk:
        st = display_status(s)
        from app.core.servers import STATUS_DOT

        items.append((s.id, f"{STATUS_DOT[st]} {s.display_name}"))
    lang = db_user.lang or "ru"
    text = decorate(t("servers_list", lang, count=len(servers), monitor="{monitor}"))
    await show_banner(event, text, servers_kb(items, page, total_pages, lang), banner="servers", lang=lang)


@router.callback_query(SrvCB.filter(F.action == "open"))
async def open_server(query: CallbackQuery, callback_data: SrvCB, db_user) -> None:
    factory = get_session_factory()
    async with factory() as session:
        server = await session.get(Server, callback_data.server_id)
        if not server or server.user_id != db_user.id:
            await query.answer(t("not_found", db_user.lang), show_alert=True)
            return
        st = display_status(server)
        actions = ACTIONS.get(st, set())
        text = server_card_text(db_user, server)
        lang = db_user.lang or "ru"
        await show_banner(
            query,
            text,
            server_kb(server.id, actions, auto_renew=server.auto_renew, cancelled=server.cancelled),
            banner="servers",
            lang=lang,
        )


@router.callback_query(SrvCB.filter(F.action == "pwr"))
async def power(query: CallbackQuery, callback_data: SrvCB, db_user) -> None:
    action = callback_data.arg
    factory = get_session_factory()
    async with factory() as session:
        server = await session.get(Server, callback_data.server_id)
        if not server or server.user_id != db_user.id:
            await query.answer(t("not_found", db_user.lang), show_alert=True)
            return
        if not action_allowed(server, action if action != "restart" else "restart"):
            # map start/stop
            mapped = action
            if not action_allowed(server, mapped):
                await query.answer(t("action_unavailable", db_user.lang), show_alert=True)
                return
        session.add(
            Job(
                kind="power",
                class_="manage",
                payload={"server_id": server.id, "action": action},
                status="pending",
                idem_key=f"job-pwr-{server.id}-{action}-{int(__import__('time').time())}",
                server_id=server.id,
            )
        )
        await session.commit()
    await query.answer("Команда в очереди")


@router.callback_query(SrvCB.filter(F.action == "pw"))
async def reset_pw(query: CallbackQuery, callback_data: SrvCB, db_user) -> None:
    factory = get_session_factory()
    async with factory() as session:
        server = await session.get(Server, callback_data.server_id)
        if not server or server.user_id != db_user.id:
            await query.answer(t("not_found", db_user.lang), show_alert=True)
            return
        if not action_allowed(server, "password"):
            await query.answer(t("action_unavailable", db_user.lang), show_alert=True)
            return
        session.add(
            Job(
                kind="reset_password",
                class_="manage",
                payload={"server_id": server.id},
                status="pending",
                idem_key=f"job-pw-{server.id}-{int(__import__('time').time())}",
                server_id=server.id,
            )
        )
        await session.commit()
    await query.answer("Новый пароль придёт сюда")


@router.callback_query(SrvCB.filter(F.action == "ar"))
async def auto_renew(query: CallbackQuery, callback_data: SrvCB, db_user) -> None:
    factory = get_session_factory()
    async with factory() as session:
        server = await session.get(Server, callback_data.server_id)
        if not server or server.user_id != db_user.id:
            await query.answer(t("not_found", db_user.lang), show_alert=True)
            return
        server.auto_renew = callback_data.arg == "on"
        if server.auto_renew:
            server.cancelled = False
        await session.commit()
    await open_server(query, callback_data, db_user)


@router.callback_query(SrvCB.filter(F.action == "rm"))
async def cancel_server(query: CallbackQuery, callback_data: SrvCB, db_user) -> None:
    factory = get_session_factory()
    async with factory() as session:
        server = await session.get(Server, callback_data.server_id)
        if not server or server.user_id != db_user.id:
            await query.answer(t("not_found", db_user.lang), show_alert=True)
            return
        server.cancelled = True
        server.auto_renew = False
        await session.commit()
    await query.answer("Автопродление выключено")
    await open_server(query, callback_data, db_user)


@router.callback_query(SrvCB.filter(F.action == "rn"))
async def renew_menu(query: CallbackQuery, callback_data: SrvCB, db_user) -> None:
    factory = get_session_factory()
    async with factory() as session:
        server = await session.get(Server, callback_data.server_id)
        if not server or server.user_id != db_user.id:
            await query.answer(t("not_found", db_user.lang), show_alert=True)
            return
        prices = server.renew_prices or {}
        days = []
        for d in (2, 7, 30, 90, 180, 365):
            raw = prices.get(str(d)) or prices.get(d)
            if raw is None:
                continue
            price = client_price(
                D(raw),
                settings_store.decimal("markup"),
                settings_store.decimal("gateway_fee"),
                settings_store.decimal("price_step"),
            )
            days.append((d, f"{d} дн · {format_usd(price)}"))
    if not days:
        await query.answer("Цены продления недоступны", show_alert=True)
        return
    await safe_edit(query, "Выбери срок продления:", renew_days_kb(callback_data.server_id, days))


@router.callback_query(SrvCB.filter(F.action == "rn_d"))
async def renew_go(query: CallbackQuery, callback_data: SrvCB, db_user) -> None:
    days = int(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        server = await session.get(Server, callback_data.server_id)
        if not server or server.user_id != db_user.id:
            await query.answer(t("not_found", db_user.lang), show_alert=True)
            return
        prices = server.renew_prices or {}
        raw = prices.get(str(days)) or prices.get(days)
        if raw is None:
            await query.answer("Нет цены", show_alert=True)
            return
        try:
            order, _job = await confirm_renewal(
                session,
                user_id=db_user.id,
                server=server,
                days=days,
                partner_cost=D(raw),
            )
            await session.commit()
        except Exception as e:
            await session.rollback()
            await query.answer(str(e), show_alert=True)
            return
    await query.answer(f"Продление #{order.id} в очереди", show_alert=True)


@router.callback_query(SrvCB.filter(F.action == "nm"))
async def rename_start(query: CallbackQuery, callback_data: SrvCB, state: FSMContext, db_user) -> None:
    await state.set_state(RenameServer.waiting)
    await state.update_data(server_id=callback_data.server_id)
    await query.message.answer("Новое имя (2–49 символов):")
    await query.answer()


@router.message(RenameServer.waiting)
async def rename_save(message: Message, state: FSMContext, db_user) -> None:
    name = (message.text or "").strip()
    if len(name) < 2 or len(name) > 49 or "\n" in name:
        await message.answer("Имя 2–49 символов, без переносов")
        return
    data = await state.get_data()
    await state.clear()
    factory = get_session_factory()
    async with factory() as session:
        server = await session.get(Server, data.get("server_id"))
        if not server or server.user_id != db_user.id:
            await message.answer(t("not_found", db_user.lang))
            return
        server.display_name = name
        await session.commit()
    await message.answer(f"Имя сохранено: {name}")


@router.callback_query(SrvCB.filter(F.action == "sc"))
async def scripts(query: CallbackQuery, callback_data: SrvCB, db_user) -> None:
    from app.partner import get_partner
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    from app.bot.ui.emoji import icon_id

    factory = get_session_factory()
    async with factory() as session:
        server = await session.get(Server, callback_data.server_id)
        if not server or server.user_id != db_user.id or not server.partner_id:
            await query.answer(t("not_found", db_user.lang), show_alert=True)
            return
        partner_id = server.partner_id
        sid = server.id
    partner = get_partner()
    try:
        scripts_list = await partner.scripts(partner_id)
    except Exception:
        scripts_list = []
    if not scripts_list:
        await query.answer("Скриптов нет", show_alert=True)
        return
    rows = [
        [
            InlineKeyboardButton(
                text=s.name,
                callback_data=SrvCB(action="sc_run", server_id=sid, arg=s.id).pack(),
                icon_custom_emoji_id=icon_id("hammer"),
            )
        ]
        for s in scripts_list
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="Назад",
                callback_data=SrvCB(action="open", server_id=sid).pack(),
                icon_custom_emoji_id=icon_id("down"),
            )
        ]
    )
    await safe_edit(query, "Скрипты:", InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(SrvCB.filter(F.action == "sc_run"))
async def script_run(query: CallbackQuery, callback_data: SrvCB, db_user) -> None:
    factory = get_session_factory()
    async with factory() as session:
        server = await session.get(Server, callback_data.server_id)
        if not server or server.user_id != db_user.id:
            await query.answer(t("not_found", db_user.lang), show_alert=True)
            return
        session.add(
            Job(
                kind="run_script",
                class_="manage",
                payload={"server_id": server.id, "script_id": callback_data.arg},
                status="pending",
                idem_key=f"job-sc-{server.id}-{callback_data.arg}",
                server_id=server.id,
            )
        )
        await session.commit()
    await query.answer("Скрипт в очереди")


@router.callback_query(SrvCB.filter(F.action == "ri"))
async def reinstall_os(query: CallbackQuery, callback_data: SrvCB, db_user) -> None:
    from app.partner import get_partner
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    from app.bot.ui.emoji import icon_id

    factory = get_session_factory()
    async with factory() as session:
        server = await session.get(Server, callback_data.server_id)
        if not server or server.user_id != db_user.id or not server.partner_id:
            await query.answer(t("not_found", db_user.lang), show_alert=True)
            return
        if not action_allowed(server, "reinstall"):
            await query.answer(t("action_unavailable", db_user.lang), show_alert=True)
            return
        partner_id = server.partner_id
        sid = server.id
    images = await get_partner().os_for_reinstall(partner_id)
    rows = [
        [
            InlineKeyboardButton(
                text=o.name,
                callback_data=SrvCB(action="ri_os", server_id=sid, arg=o.id).pack(),
                icon_custom_emoji_id=icon_id("term"),
            )
        ]
        for o in images
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="Назад",
                callback_data=SrvCB(action="open", server_id=sid).pack(),
                icon_custom_emoji_id=icon_id("down"),
            )
        ]
    )
    await safe_edit(query, "Выбери ОС для переустановки:", InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(SrvCB.filter(F.action == "ri_os"))
async def reinstall_go(query: CallbackQuery, callback_data: SrvCB, db_user) -> None:
    from app.db.models import Order

    factory = get_session_factory()
    async with factory() as session:
        server = await session.get(Server, callback_data.server_id)
        if not server or server.user_id != db_user.id:
            await query.answer(t("not_found", db_user.lang), show_alert=True)
            return
        order = Order(
            user_id=db_user.id,
            kind="reinstall",
            status="queued",
            server_id=server.id,
            os_id=callback_data.arg,
            snapshot={},
            payment_mode="balance",
        )
        session.add(order)
        await session.flush()
        session.add(
            Job(
                kind="reinstall",
                class_="manage",
                payload={"server_id": server.id, "os_id": callback_data.arg},
                status="pending",
                idem_key=f"reinstall-{order.id}",
                order_id=order.id,
                server_id=server.id,
            )
        )
        await session.commit()
    await query.answer("Переустановка в очереди", show_alert=True)
