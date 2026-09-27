from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import select

from app.bot.keyboards import BalCB, NavCB, balance_kb, invoice_kb, networks_kb, topup_amounts_kb
from app.bot.render import show_banner
from app.bot.texts import t
from app.bot.ui.screens import decorate
from app.config import get_settings
from app.core.money import D, format_usd, money
from app.core.settings_store import settings_store
from app.db.models import Invoice
from app.db.session import get_session_factory
from app.payments import get_gateway, payment_methods
from app.payments.base import GatewayInvoice
from app.payments.crypto import (
    check_incoming,
    find_wallet,
    make_qr_png,
    pay_amount_for,
)
from app.payments.service import apply_invoice_state

router = Router()


class TopupAmount(StatesGroup):
    waiting = State()


def _dev_wallets() -> list[dict]:
    return [
        {
            "id": "USDT_TRC20",
            "label": "USDT TRC-20 (TEST)",
            "asset": "USDT",
            "network": "TRC20",
            "address": "TEST_TRC20_ADDRESS",
            "enabled": True,
            "kind": "wallet",
        },
        {
            "id": "USDT_BEP20",
            "label": "USDT BEP-20 (TEST)",
            "asset": "USDT",
            "network": "BEP20",
            "address": "TEST_BEP20_ADDRESS",
            "enabled": True,
            "kind": "wallet",
        },
        {
            "id": "TON",
            "label": "TON (TEST)",
            "asset": "TON",
            "network": "TON",
            "address": "TEST_TON_ADDRESS",
            "enabled": True,
            "kind": "wallet",
        },
        {
            "id": "USDT_TON",
            "label": "USDT TON (TEST)",
            "asset": "USDT",
            "network": "TON",
            "address": "TEST_USDT_TON_ADDRESS",
            "enabled": True,
            "kind": "wallet",
        },
        {
            "id": "BTC",
            "label": "BTC (TEST)",
            "asset": "BTC",
            "network": "BTC",
            "address": "TEST_BTC_ADDRESS",
            "enabled": True,
            "kind": "wallet",
        },
    ]


@router.callback_query(NavCB.filter(F.to == "balance"))
@router.message(F.text.in_({"/balance"}))
async def show_balance(event: Message | CallbackQuery, db_user) -> None:
    lang = db_user.lang or "ru"
    factory = get_session_factory()
    async with factory() as session:
        from app.db.models import User

        user = await session.get(User, db_user.id)
        bal = user.balance_usd if user else db_user.balance_usd
    text = decorate(t("balance", lang, balance=format_usd(bal), wallet="{wallet}"))
    await show_banner(event, text, balance_kb(lang), banner="balance", lang=lang)


@router.callback_query(BalCB.filter(F.action == "topup"))
async def topup_start(query: CallbackQuery, db_user) -> None:
    lang = db_user.lang or "ru"
    text = decorate(t("topup_amount", lang, wallet="{wallet}"))
    await show_banner(query, text, topup_amounts_kb(lang), banner="balance", lang=lang)


@router.callback_query(BalCB.filter(F.action == "amt"))
async def topup_amt(query: CallbackQuery, callback_data: BalCB, db_user) -> None:
    try:
        amount = money(D(callback_data.arg))
    except (InvalidOperation, ValueError):
        await query.answer("?", show_alert=True)
        return
    await _pick_network(query, db_user, amount)


@router.callback_query(BalCB.filter(F.action == "own"))
async def topup_own(query: CallbackQuery, state: FSMContext, db_user) -> None:
    lang = db_user.lang or "ru"
    await state.set_state(TopupAmount.waiting)
    mn = settings_store.decimal("min_topup")
    mx = settings_store.decimal("max_topup")
    await query.message.answer(t("topup_own_ask", lang, min=format_usd(mn), max=format_usd(mx)))
    await query.answer()


@router.message(TopupAmount.waiting)
async def topup_own_value(message: Message, state: FSMContext, db_user) -> None:
    lang = db_user.lang or "ru"
    try:
        amount = money(D((message.text or "").replace("$", "").replace(",", ".").strip()))
    except Exception:
        mn = settings_store.decimal("min_topup")
        mx = settings_store.decimal("max_topup")
        await message.answer(t("topup_own_ask", lang, min=format_usd(mn), max=format_usd(mx)))
        return
    mn = settings_store.decimal("min_topup")
    mx = settings_store.decimal("max_topup")
    if amount < mn or amount > mx:
        await message.answer(t("topup_own_ask", lang, min=format_usd(mn), max=format_usd(mx)))
        return
    await state.clear()
    await _pick_network(message, db_user, amount)


async def _pick_network(event: Message | CallbackQuery, db_user, amount: Decimal) -> None:
    lang = db_user.lang or "ru"
    methods = payment_methods()
    if not methods and get_settings().is_dev:
        methods = _dev_wallets()
    if not methods:
        text = t("invoice_no_wallets", lang)
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return

    text = decorate(t("topup_network", lang, amount=format_usd(amount), wallet="{wallet}"))
    kb = networks_kb(methods, str(amount), lang)
    await show_banner(event, text, kb, banner="balance", lang=lang)


@router.callback_query(BalCB.filter(F.action == "net"))
async def topup_network(query: CallbackQuery, callback_data: BalCB, db_user, bot: Bot) -> None:
    lang = db_user.lang or "ru"
    try:
        amount_s, method_id = callback_data.arg.split("|", 1)
        amount = money(D(amount_s))
    except Exception:
        await query.answer("?", show_alert=True)
        return

    if method_id in {"CRYPTOBOT", "XROCKET"}:
        await _create_gateway_invoice(query, db_user, bot, amount=amount, method_id=method_id)
        return

    wallet = find_wallet(method_id)
    if wallet is None or not wallet.get("address"):
        if get_settings().is_dev:
            wallet = next((w for w in _dev_wallets() if w["id"] == method_id), None)
        if wallet is None or not wallet.get("address"):
            await query.answer(t("invoice_no_wallets", lang), show_alert=True)
            return

    ttl = settings_store.int("invoice_ttl_min")
    asset = wallet.get("asset") or "USDT"
    network = wallet.get("network") or method_id
    address = wallet["address"]
    pay_amount = pay_amount_for(asset, amount)
    factory = get_session_factory()
    async with factory() as session:
        pending = list(
            await session.scalars(
                select(Invoice).where(Invoice.user_id == db_user.id, Invoice.status == "pending")
            )
        )
        if len(pending) >= 5:
            await query.answer("5", show_alert=True)
            return
        inv = Invoice(
            user_id=db_user.id,
            gateway="crypto_direct",
            gateway_invoice_id=f"crypto-{uuid4().hex[:16]}",
            amount_usd=amount,
            status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl),
            network=network,
            asset=asset,
            address=address,
            pay_amount=pay_amount,
        )
        session.add(inv)
        await session.commit()
        invoice_id = inv.id
        expires = inv.expires_at.strftime("%d.%m.%Y %H:%M")

    text = decorate(
        t(
            "invoice",
            lang,
            invoice_id=invoice_id,
            network=network,
            asset=asset,
            pay_amount=str(pay_amount),
            amount=format_usd(amount),
            address=address,
            expires=expires,
            wallet="{wallet}",
        )
    )
    qr_payload = address
    if asset == "TON" and not str(address).startswith("TEST"):
        qr_payload = f"ton://transfer/{address}?amount={int(pay_amount * Decimal('1000000000'))}"
    qr_bytes = make_qr_png(qr_payload)
    photo = BufferedInputFile(qr_bytes, filename=f"pay_{invoice_id}.png")
    try:
        if query.message and query.message.photo:
            await query.message.delete()
    except Exception:
        pass
    await bot.send_photo(
        chat_id=query.from_user.id,
        photo=photo,
        caption=text,
        reply_markup=invoice_kb(invoice_id, lang),
        parse_mode="HTML",
    )
    await query.answer()


async def _create_gateway_invoice(
    query: CallbackQuery,
    db_user,
    bot: Bot,
    *,
    amount: Decimal,
    method_id: str,
) -> None:
    lang = db_user.lang or "ru"
    gw_name = "cryptobot" if method_id == "CRYPTOBOT" else "xrocket"
    label = "CryptoBot" if gw_name == "cryptobot" else "xRocket"
    from app.payments import gateway_enabled

    if not gateway_enabled(gw_name):
        await query.answer(t("invoice_no_wallets", lang), show_alert=True)
        return
    ttl = settings_store.int("invoice_ttl_min")
    gateway = get_gateway(gw_name)
    factory = get_session_factory()
    async with factory() as session:
        pending = list(
            await session.scalars(
                select(Invoice).where(Invoice.user_id == db_user.id, Invoice.status == "pending")
            )
        )
        if len(pending) >= 5:
            await query.answer("5", show_alert=True)
            return
        inv = Invoice(
            user_id=db_user.id,
            gateway=gw_name,
            gateway_invoice_id=None,
            amount_usd=amount,
            status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl),
            network=label,
            asset="USD",
        )
        session.add(inv)
        await session.flush()
        try:
            remote = await gateway.create_invoice(
                order_ref=str(inv.id),
                amount_usd=amount,
                ttl_min=ttl,
                network="USDT",
            )
        except Exception as e:
            await session.rollback()
            await query.answer(t("invoice_gateway_fail", lang, err=str(e)[:120]), show_alert=True)
            return
        inv.gateway_invoice_id = remote.gateway_invoice_id
        inv.pay_url = remote.pay_url
        inv.asset = remote.asset or "USD"
        inv.network = remote.network or label
        inv.pay_amount = remote.pay_amount or amount
        inv.address = remote.address
        if remote.expires_at:
            inv.expires_at = remote.expires_at
        await session.commit()
        invoice_id = inv.id
        pay_url = inv.pay_url
        expires = inv.expires_at.strftime("%d.%m.%Y %H:%M")
        asset = inv.asset or "USD"
        network = inv.network or label

    text = decorate(
        t(
            "invoice_gateway",
            lang,
            invoice_id=invoice_id,
            method=label,
            amount=format_usd(amount),
            asset=asset,
            network=network,
            expires=expires,
            wallet="{wallet}",
        )
    )
    try:
        if query.message:
            await query.message.delete()
    except Exception:
        pass
    await bot.send_message(
        chat_id=query.from_user.id,
        text=text,
        reply_markup=invoice_kb(invoice_id, lang, pay_url=pay_url),
        parse_mode="HTML",
    )
    await query.answer()


@router.callback_query(BalCB.filter(F.action == "chk"))
async def check_invoice(query: CallbackQuery, callback_data: BalCB, db_user) -> None:
    lang = db_user.lang or "ru"
    invoice_id = int(callback_data.arg)
    factory = get_session_factory()
    async with factory() as session:
        inv = await session.get(Invoice, invoice_id)
        if not inv or inv.user_id != db_user.id:
            await query.answer(t("not_found", lang), show_alert=True)
            return
        if inv.status == "paid":
            await query.answer(t("pay_status_paid", lang), show_alert=True)
            return

        if inv.gateway in {"cryptobot", "xrocket"}:
            if not inv.gateway_invoice_id:
                await query.answer(t("pay_status_pending", lang), show_alert=True)
                return
            try:
                remote = await get_gateway(inv.gateway).get_invoice(inv.gateway_invoice_id)
            except Exception:
                await query.answer(t("pay_status_pending", lang), show_alert=True)
                return
            await apply_invoice_state(session, inv, remote)
            await session.commit()
            if inv.status == "paid":
                await query.answer(t("pay_status_paid", lang), show_alert=True)
                await show_balance(query, db_user)
            else:
                await query.answer(t("pay_status_pending", lang), show_alert=True)
            return

        since = inv.created_at.timestamp() if inv.created_at else datetime.now(timezone.utc).timestamp()
        ok = await check_incoming(
            network=inv.network or "",
            asset=inv.asset or "USDT",
            address=inv.address or "",
            min_amount=D(inv.pay_amount or inv.amount_usd),
            since_ts=since,
        )
        if ok:
            remote = GatewayInvoice(
                gateway_invoice_id=inv.gateway_invoice_id or str(inv.id),
                status="paid",
                paid_usd=inv.amount_usd,
                pay_amount=inv.pay_amount,
                address=inv.address,
                asset=inv.asset,
                network=inv.network,
                expires_at=inv.expires_at,
                txid=f"auto-{invoice_id}",
            )
            await apply_invoice_state(session, inv, remote)
            await session.commit()
            await query.answer(t("pay_status_paid", lang), show_alert=True)
            await show_balance(query, db_user)
            return
        await query.answer(t("pay_status_pending", lang), show_alert=True)
