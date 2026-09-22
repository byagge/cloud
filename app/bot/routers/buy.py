from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.bot.keyboards import (
    BuyCB,
    NavCB,
    confirm_kb,
    locations_kb,
    os_groups_kb,
    os_versions_kb,
    plans_kb,
    term_kb,
)
from app.bot.render import safe_edit, show_banner
from app.bot.texts import t
from app.bot.ui.emoji import pe
from app.bot.ui.screens import decorate
from app.core.money import D, format_usd, money
from app.core.orders import confirm_purchase, get_active_server_count
from app.core.pricing import client_price
from app.core.settings_store import settings_store
from app.db.models import Order
from app.db.session import get_session_factory
from app.partner import get_partner
from app.partner.fake import LOC_META, LOCATIONS, TIER_EMOJI, os_group, os_group_icon

router = Router()

LOC_LABELS = {
    "germany": {"ru": "Германия", "en": "Germany"},
    "finland": {"ru": "Финляндия", "en": "Finland"},
    "poland": {"ru": "Польша", "en": "Poland"},
}


def _loc_name(code: str, lang: str) -> str:
    return LOC_LABELS.get(code, {}).get(lang) or LOC_LABELS.get(code, {}).get("ru") or code


def _flag(code: str) -> str:
    return LOC_META.get(code, {}).get("flag", "🌐")


async def _catalog(redis, location: str):
    raw = await redis.get(f"catalog:plans:{location}")
    if raw:
        data = json.loads(raw)
        return data.get("plans") or []
    partner = get_partner()
    plans = await partner.plans(location)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "plans": [p.model_dump(mode="json") for p in plans],
    }
    await redis.set(f"catalog:plans:{location}", json.dumps(payload), ex=3600)
    return payload["plans"]


def _price(partner_cost: Decimal) -> Decimal:
    return client_price(
        partner_cost,
        settings_store.decimal("markup"),
        settings_store.decimal("gateway_fee"),
        settings_store.decimal("price_step"),
    )


def _spec_block(lang: str, *, location: str, plan: dict, price: Decimal) -> str:
    meta = LOC_META.get(location, {})
    return (
        "<blockquote>"
        + t(
            "buy_spec",
            lang,
            flag=_flag(location),
            location=_loc_name(location, lang),
            cpu=plan.get("cpu", 1),
            ram=int(plan.get("ram_mb", 0)) // 1024,
            disk=plan.get("disk_gb", 0),
            price=format_usd(price),
            cpu_model=escape(str(plan.get("cpu_model") or meta.get("cpu_model") or "AMD Ryzen 9 5950X")),
            bandwidth=escape(str(plan.get("bandwidth") or meta.get("bandwidth") or "1 Гбит/с")),
        )
        + "</blockquote>"
    )


def _plan_btn_label(p: dict) -> str:
    prices = p.get("prices") or {}
    cost = D(prices.get("1") or prices.get(1) or 0)
    price = _price(cost)
    tier = int(p.get("tier") or 1)
    emoji = TIER_EMOJI.get(tier, "🌪")
    ram = int(p.get("ram_mb", 0)) // 1024
    # match screenshot style without $ sign ambiguity — use $N
    return f"{emoji} {p.get('cpu')} vCPU / {ram} GB RAM / {p.get('disk_gb')}GB SSD - {price}$"


@router.callback_query(NavCB.filter(F.to == "buy"))
async def buy_start(query: CallbackQuery, db_user, redis) -> None:
    lang = db_user.lang
    if not db_user.accepted_terms:
        await query.answer(t("terms_gate", lang), show_alert=True)
        return
    items = []
    for loc in LOCATIONS:
        try:
            plans = await _catalog(redis, loc)
            monthly = []
            for p in plans:
                prices = p.get("prices") or {}
                monthly.append(_price(D(prices.get("1") or prices.get(1) or 0)))
            min_p = min(monthly) if monthly else Decimal("0")
            items.append((loc, f"{_flag(loc)} {_loc_name(loc, lang)}", format_usd(min_p)))
        except Exception:
            items.append((loc, f"{_flag(loc)} {_loc_name(loc, lang)}", "—"))
    text = decorate(t("buy_location", lang, pin="{pin}"))
    await show_banner(query, text, locations_kb(items), banner="buy", lang=lang)


@router.callback_query(BuyCB.filter(F.step == "loc"))
async def buy_loc(query: CallbackQuery, callback_data: BuyCB, db_user, redis) -> None:
    lang = db_user.lang
    location = callback_data.arg
    factory = get_session_factory()
    async with factory() as session:
        order = Order(
            user_id=db_user.id,
            kind="purchase",
            status="draft",
            location=location,
            snapshot={"location": location, "plans": [], "os": []},
        )
        session.add(order)
        await session.commit()
        draft_id = order.id

    plans = await _catalog(redis, location)
    plans_sorted = sorted(
        plans,
        key=lambda x: D((x.get("prices") or {}).get("1") or (x.get("prices") or {}).get(1) or 0),
    )
    plan_btns = [(p["id"], _plan_btn_label(p)) for p in plans_sorted]

    async with factory() as session:
        order = await session.get(Order, draft_id)
        if order:
            order.snapshot = {"location": location, "plans": plans_sorted, "os": []}
            await session.commit()

    meta = LOC_META.get(location, {})
    text = (
        t("buy_plan_title", lang)
        + "\n\n<blockquote>"
        + t(
            "buy_plan_meta",
            lang,
            flag=_flag(location),
            location=_loc_name(location, lang),
            cpu_model=escape(str(meta.get("cpu_model", "AMD Ryzen 9 5950X"))),
            bandwidth=escape(str(meta.get("bandwidth", "1 Гбит/с"))),
        )
        + "</blockquote>\n\n"
        + t("buy_plan_hint", lang)
    )
    await show_banner(query, text, plans_kb(draft_id, plan_btns, lang), banner="buy", lang=lang)


@router.callback_query(BuyCB.filter(F.step == "plan"))
async def buy_plan(query: CallbackQuery, callback_data: BuyCB, db_user, redis) -> None:
    lang = db_user.lang
    factory = get_session_factory()
    async with factory() as session:
        order = await session.get(Order, callback_data.draft_id)
        if not order or order.user_id != db_user.id or order.status != "draft":
            await query.answer(t("order_stale", lang), show_alert=True)
            return
        order.plan_id = callback_data.arg
        plans = (order.snapshot or {}).get("plans") or []
        plan = next((p for p in plans if p["id"] == callback_data.arg), None)
        if plan:
            snap = dict(order.snapshot or {})
            snap["plan"] = plan
            snap["plan_label"] = (
                f"{plan.get('cpu')} vCPU / {int(plan.get('ram_mb', 0))//1024} GB / {plan.get('disk_gb')}GB"
            )
            order.snapshot = snap
        await session.commit()
        location = order.location or ""
        plan_id = order.plan_id or ""
        plan = (order.snapshot or {}).get("plan") or {}

    partner = get_partner()
    images = await partner.os_list(location, plan_id)
    os_dump = [o.model_dump() for o in images]
    async with factory() as session:
        order = await session.get(Order, callback_data.draft_id)
        if order:
            snap = dict(order.snapshot or {})
            snap["os"] = os_dump
            order.snapshot = snap
            await session.commit()

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

    prices = plan.get("prices") or {}
    monthly = _price(D(prices.get("1") or prices.get(1) or 0))
    text = (
        t("buy_os_group_title", lang)
        + "\n\n"
        + _spec_block(lang, location=location, plan=plan, price=monthly)
        + "\n\n"
        + t("buy_os_group_hint", lang)
    )
    await show_banner(query, text, os_groups_kb(callback_data.draft_id, group_btns, lang), banner="buy", lang=lang)


@router.callback_query(BuyCB.filter(F.step == "osg"))
async def buy_os_group(query: CallbackQuery, callback_data: BuyCB, db_user) -> None:
    lang = db_user.lang
    group = callback_data.arg
    factory = get_session_factory()
    async with factory() as session:
        order = await session.get(Order, callback_data.draft_id)
        if not order or order.user_id != db_user.id or order.status != "draft":
            await query.answer(t("order_stale", lang), show_alert=True)
            return
        snap = dict(order.snapshot or {})
        snap["os_group"] = group
        order.snapshot = snap
        await session.commit()
        plan = snap.get("plan") or {}
        location = order.location or ""
        os_list = snap.get("os") or []

    versions = [
        (o["id"], o["name"], os_group_icon(group))
        for o in os_list
        if os_group(o["name"]) == group
    ]
    prices = plan.get("prices") or {}
    monthly = _price(D(prices.get("1") or prices.get(1) or 0))
    text = (
        t("buy_os_title", lang, group=escape(group))
        + "\n\n"
        + _spec_block(lang, location=location, plan=plan, price=monthly)
        + "\n\n"
        + t("buy_os_hint", lang)
    )
    await show_banner(query, text, os_versions_kb(callback_data.draft_id, versions, lang), banner="buy", lang=lang)


@router.callback_query(BuyCB.filter(F.step == "os"))
async def buy_os(query: CallbackQuery, callback_data: BuyCB, db_user) -> None:
    lang = db_user.lang
    factory = get_session_factory()
    async with factory() as session:
        order = await session.get(Order, callback_data.draft_id)
        if not order or order.user_id != db_user.id or order.status != "draft":
            await query.answer(t("order_stale", lang), show_alert=True)
            return
        order.os_id = callback_data.arg
        os_list = (order.snapshot or {}).get("os") or []
        os_name = next((o["name"] for o in os_list if o["id"] == callback_data.arg), callback_data.arg)
        snap = dict(order.snapshot or {})
        snap["os_name"] = os_name
        order.snapshot = snap
        await session.commit()
        plan = snap.get("plan") or {}
        plan_label = snap.get("plan_label", "")
        location = order.location or ""
        prices = plan.get("prices") or {}

    terms = []
    for months in (1, 3, 6, 12):
        cost = prices.get(str(months)) or prices.get(months)
        if cost is None:
            continue
        price = _price(D(cost))
        monthly = _price(D(prices.get("1") or prices.get(1) or cost))
        disc = ""
        if months > 1:
            full = monthly * months
            if full > price:
                pct = int((1 - (price / full)) * 100)
                disc = f" (−{pct}%)"
        label = f"{months} мес · {format_usd(price)}{disc}" if lang == "ru" else f"{months} mo · {format_usd(price)}{disc}"
        terms.append((months, label))

    text = decorate(
        t(
            "buy_term",
            lang,
            plan=plan_label,
            location=_loc_name(location, lang),
            os=os_name,
            clock="{clock}",
        )
    )
    await safe_edit(query, text, term_kb(callback_data.draft_id, terms, lang))


@router.callback_query(BuyCB.filter(F.step == "back"))
async def buy_back(query: CallbackQuery, callback_data: BuyCB, db_user, redis) -> None:
    where = callback_data.arg
    if where == "loc":
        await buy_start(query, db_user, redis)
        return
    factory = get_session_factory()
    if where == "plan":
        async with factory() as session:
            order = await session.get(Order, callback_data.draft_id)
            loc = order.location if order else "germany"
        await buy_loc(query, BuyCB(step="loc", draft_id=0, arg=loc or "germany"), db_user, redis)
        return
    if where == "osg":
        async with factory() as session:
            order = await session.get(Order, callback_data.draft_id)
            pid = order.plan_id if order else ""
        if pid:
            await buy_plan(
                query, BuyCB(step="plan", draft_id=callback_data.draft_id, arg=pid), db_user, redis
            )
        return
    if where == "os":
        async with factory() as session:
            order = await session.get(Order, callback_data.draft_id)
            group = ((order.snapshot or {}).get("os_group") if order else None) or "Ubuntu"
        await buy_os_group(
            query, BuyCB(step="osg", draft_id=callback_data.draft_id, arg=group), db_user
        )
        return


@router.callback_query(BuyCB.filter(F.step == "term"))
async def buy_term(query: CallbackQuery, callback_data: BuyCB, db_user) -> None:
    lang = db_user.lang
    months = int(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        order = await session.get(Order, callback_data.draft_id)
        if not order or order.user_id != db_user.id or order.status != "draft":
            await query.answer(t("order_stale", lang), show_alert=True)
            return
        order.months = months
        plan = (order.snapshot or {}).get("plan") or {}
        prices = plan.get("prices") or {}
        cost = D(prices.get(str(months)) or prices.get(months) or 0)
        price = _price(cost)
        order.partner_cost_usd = money(cost)
        order.price_usd = price
        from app.db.models import User

        user = await session.get(User, db_user.id)
        bal = user.balance_usd if user else db_user.balance_usd
        await session.commit()
        end = datetime.now(timezone.utc) + timedelta(days=30 * months)
        text = decorate(
            t(
                "buy_confirm",
                lang,
                location=_loc_name(order.location or "", lang),
                plan=(order.snapshot or {}).get("plan_label", ""),
                os=(order.snapshot or {}).get("os_name", ""),
                months=months,
                end_date=end.strftime("%d.%m.%Y"),
                price=format_usd(price),
                balance=format_usd(bal),
                check="{check}",
            )
        )
        can = bal >= price
        missing = format_usd(price - bal) if not can else None
        await safe_edit(
            query,
            text,
            confirm_kb(order.id, can_pay=can, price=format_usd(price), missing=missing, lang=lang),
        )


@router.callback_query(BuyCB.filter(F.step == "go"))
async def buy_go(query: CallbackQuery, callback_data: BuyCB, db_user) -> None:
    lang = db_user.lang
    factory = get_session_factory()
    async with factory() as session:
        order = await session.get(Order, callback_data.draft_id)
        if not order or order.user_id != db_user.id or order.status != "draft":
            await query.answer(t("order_stale", lang), show_alert=True)
            return
        max_srv = settings_store.int("max_servers_per_user")
        if await get_active_server_count(session, db_user.id) >= max_srv:
            await query.answer(f"max {max_srv}", show_alert=True)
            return
        price = order.price_usd or Decimal("0")
        cost = order.partner_cost_usd or Decimal("0")
        from app.db.models import User

        user = await session.get(User, db_user.id)
        if user is None or user.balance_usd < price:
            await query.answer(t("need_funds", lang), show_alert=True)
            return
        try:
            await confirm_purchase(session, order=order, price=price, partner_cost=cost)
            await session.commit()
            order_id = order.id
        except Exception as e:
            await session.rollback()
            await query.answer(str(e), show_alert=True)
            return

    text = decorate(t("order_accepted", lang, order_id=order_id, robot="{robot}"))
    from app.bot.keyboards import main_menu

    await safe_edit(query, text, main_menu(is_admin=False, lang=lang))


@router.callback_query(BuyCB.filter(F.step == "cancel"))
async def buy_cancel(query: CallbackQuery, callback_data: BuyCB, db_user, admin_role) -> None:
    factory = get_session_factory()
    async with factory() as session:
        order = await session.get(Order, callback_data.draft_id)
        if order and order.user_id == db_user.id and order.status == "draft":
            order.status = "cancelled"
            await session.commit()
    from app.bot.routers.start import show_home

    await show_home(query, db_user, admin_role)
