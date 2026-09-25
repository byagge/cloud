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
from app.partner import get_partner

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
    from app.core.servers import STATUS_DOT

    for s in chunk:
        try:
            st = display_status(s)
            dot = STATUS_DOT.get(st, "⚪")
        except Exception:
            dot = "⚪"
        name = (s.display_name or f"server-{s.id}")[:40]
        items.append((s.id, f"{dot} {name}"))
    lang = db_user.lang or "ru"
    text = decorate(t("servers_list", lang, count=len(servers), monitor="{monitor}"))
    await show_banner(event, text, servers_kb(items, page, total_pages, lang), banner="servers", lang=lang)


@router.callback_query(SrvCB.filter(F.action == "open"))
async def open_server(
    query: CallbackQuery, callback_data: SrvCB, db_user, redis, state: FSMContext | None = None
) -> None:
    from html import escape

    from app.bot.panel_card import remember_server_card
    from app.config import get_settings
    from app.core.secrets import get_secret

    # Clear agent deploy/support FSM when user presses Back to server card
    if state is not None:
        await state.clear()

    factory = get_session_factory()
    password: str | None = None
    fetching = False
    async with factory() as session:
        server = await session.get(Server, callback_data.server_id)
        if not server or server.user_id != db_user.id:
            await query.answer(t("not_found", db_user.lang), show_alert=True)
            return
        secret = await get_secret(redis, f"cred:{server.id}")
        password = (secret or {}).get("password") if secret else None
        # Silently request password from provider — user never presses a button
        if (not password) and server.partner_id:
            fetching = True
            flag = f"cred:fetch:{server.id}"
            if not await redis.get(flag):
                pending = await session.scalar(
                    select(Job)
                    .where(
                        Job.server_id == server.id,
                        Job.kind == "reset_password",
                        Job.status.in_(("pending", "running")),
                    )
                    .limit(1)
                )
                if pending is None:
                    session.add(
                        Job(
                            kind="reset_password",
                            class_="manage",
                            payload={"server_id": server.id, "silent": True},
                            status="pending",
                            idem_key=f"pw-auto-{server.id}-{int(__import__('time').time()) // 300}",
                            server_id=server.id,
                        )
                    )
                    await session.commit()
                await redis.set(flag, "1", ex=120)
            password = t("srv_pw_loading", db_user.lang or "ru")
        try:
            st = display_status(server)
            actions = ACTIONS.get(st, set())
            text = server_card_text(db_user, server, password=password)
        except Exception:
            actions = set()
            name = escape(server.display_name or f"server-{server.id}")
            text = (
                f"<b>{name}</b>\nIP: <code>{escape(str(server.ip or '—'))}</code>\n"
                f"Password: <code>{escape(str(password or '—'))}</code>"
            )
        lang = db_user.lang or "ru"
        settings = get_settings()
        kb = server_kb(
            server.id,
            actions,
            auto_renew=server.auto_renew,
            cancelled=server.cancelled,
            support_url=settings.support_url,
            lang=lang,
        )
        sid = server.id
        uid = db_user.id

    shown = None
    if len(text) > 1000:
        from app.bot.render import safe_edit

        await safe_edit(query, text, kb)
        shown = query.message
    else:
        shown = await show_banner(query, text, kb, banner="servers", lang=lang)

    # So the card auto-updates when password arrives (no user action)
    if fetching and shown is not None:
        try:
            await remember_server_card(
                redis,
                server_id=sid,
                chat_id=shown.chat.id,
                message_id=shown.message_id,
                user_id=uid,
            )
        except Exception:
            pass


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
    # Password is fetched automatically — no user action needed
    await query.answer()


@router.callback_query(SrvCB.filter(F.action == "ar"))
async def auto_renew(query: CallbackQuery, callback_data: SrvCB, db_user, redis) -> None:
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
    await open_server(query, callback_data, db_user, redis)


@router.callback_query(SrvCB.filter(F.action == "rm"))
async def cancel_server(query: CallbackQuery, callback_data: SrvCB, db_user, redis) -> None:
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
    await open_server(query, callback_data, db_user, redis)


@router.callback_query(SrvCB.filter(F.action == "rn"))
async def renew_menu(query: CallbackQuery, callback_data: SrvCB, db_user) -> None:
    factory = get_session_factory()
    async with factory() as session:
        server = await session.get(Server, callback_data.server_id)
        if not server or server.user_id != db_user.id:
            await query.answer(t("not_found", db_user.lang), show_alert=True)
            return
        prices = dict(server.renew_prices or {})
        if not prices and server.partner_id:
            try:
                remote = await get_partner().get_server(server.partner_id)
                if remote.renew_prices:
                    prices = {str(k): str(v) for k, v in remote.renew_prices.items()}
                    server.renew_prices = prices
                    await session.commit()
            except Exception:
                pass
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
async def reinstall_os(query: CallbackQuery, callback_data: SrvCB, db_user, redis) -> None:
    import json
    from collections import defaultdict

    from app.bot.keyboards import reinstall_os_groups_kb
    from app.partner import get_partner
    from app.partner.fake import os_group, os_group_icon

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
    os_dump = [{"id": o.id, "name": o.name} for o in images]
    await redis.set(f"reinstall:os:{sid}", json.dumps(os_dump), ex=600)

    groups: dict[str, list] = defaultdict(list)
    for o in os_dump:
        groups[os_group(o["name"])].append(o)
    order_groups = [
        "Windows",
        "Alma Linux",
        "Debian",
        "Rocky",
        "Ubuntu",
        "Oracle",
        "CentOS",
        "FreeBSD",
        "Other",
    ]
    group_btns = []
    for g in order_groups:
        if g not in groups:
            continue
        group_btns.append((g, f"{g} ({len(groups[g])})", os_group_icon(g)))

    lang = db_user.lang or "ru"
    text = decorate(t("reinstall_os_groups", lang))
    await show_banner(
        query, text, reinstall_os_groups_kb(sid, group_btns, lang), banner="servers", lang=lang
    )


@router.callback_query(SrvCB.filter(F.action == "ri_g"))
async def reinstall_os_group(query: CallbackQuery, callback_data: SrvCB, db_user, redis) -> None:
    import json

    from app.bot.keyboards import reinstall_os_versions_kb
    from app.partner.fake import os_group, os_group_icon

    sid = callback_data.server_id
    group = callback_data.arg
    raw = await redis.get(f"reinstall:os:{sid}")
    if not raw:
        await query.answer(t("not_found", db_user.lang), show_alert=True)
        return
    os_list = json.loads(raw if isinstance(raw, str) else raw.decode())
    versions = [
        (o["id"], o["name"], os_group_icon(group))
        for o in os_list
        if os_group(o["name"]) == group
    ]
    if not versions:
        await query.answer(t("not_found", db_user.lang), show_alert=True)
        return
    lang = db_user.lang or "ru"
    from html import escape

    text = decorate(t("reinstall_os_versions", lang, group=escape(group)))
    await show_banner(
        query,
        text,
        reinstall_os_versions_kb(sid, versions, lang),
        banner="servers",
        lang=lang,
    )


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
    await query.answer("OK", show_alert=True)

@router.callback_query(SrvCB.filter(F.action == "mon"))
async def monitor(query: CallbackQuery, callback_data: SrvCB, db_user) -> None:
    from app.core.servers import STATUS_EN, STATUS_RU

    factory = get_session_factory()
    async with factory() as session:
        server = await session.get(Server, callback_data.server_id)
        if not server or server.user_id != db_user.id:
            await query.answer(t("not_found", db_user.lang), show_alert=True)
            return
        lang = db_user.lang or "ru"
        st = display_status(server)
        status_map = STATUS_RU if lang == "ru" else STATUS_EN
        ram = f"{(server.ram_mb or 0) / 1024:.1f} GB" if server.ram_mb else "—"
        text = decorate(
            t(
                "srv_monitor",
                lang,
                status=status_map.get(st, str(st)),
                ip=server.ip or "—",
                cpu=server.cpu or "—",
                ram=ram,
                disk=f"{server.disk_gb} GB" if server.disk_gb else "—",
            )
        )
        from aiogram.types import InlineKeyboardMarkup
        from app.bot.keyboards import _ib

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [_ib(t("srv_btn_back", lang), SrvCB(action="open", server_id=server.id).pack(), "down")]
            ]
        )
    await show_banner(query, text, kb, banner="servers", lang=lang)
