from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.bot.ui.emoji import icon_id

BTN_PANEL = "Панель"
BTN_CANCEL = "Отмена"


class NavCB(CallbackData, prefix="nav"):
    to: str


class BuyCB(CallbackData, prefix="buy"):
    step: str
    draft_id: int = 0
    arg: str = "-"


class BalCB(CallbackData, prefix="bal"):
    action: str
    arg: str = "-"


class SrvCB(CallbackData, prefix="srv"):
    action: str
    server_id: int = 0
    arg: str = "-"


class AgentCB(CallbackData, prefix="ag"):
    """AI agent session controls (stop / open / plan / deploy strategy)."""

    action: str  # stop | open | plan_go | plan_no | dep_over | dep_side
    job_id: int = 0
    server_id: int = 0


class ProfCB(CallbackData, prefix="prof"):
    action: str
    arg: str = "-"


class AdmCB(CallbackData, prefix="adm"):
    section: str
    action: str = "open"
    arg: str = "-"


class TermsCB(CallbackData, prefix="terms"):
    ok: int = 1


def _ib(text: str, data: str, icon: str = "cube") -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=data,
        icon_custom_emoji_id=icon_id(icon),
    )


def _url(text: str, url: str, icon: str = "user") -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        url=url,
        icon_custom_emoji_id=icon_id(icon),
    )


def panel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_PANEL, icon_custom_emoji_id=icon_id("cube"))]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Панель…",
    )


def terms_kb(lang: str = "en") -> InlineKeyboardMarkup:
    from app.bot.texts import t

    label = t("btn_accept_terms", lang)
    return InlineKeyboardMarkup(
        inline_keyboard=[[_ib(label, TermsCB(ok=1).pack(), "check")]]
    )


def main_menu(*, is_admin: bool = False, lang: str = "ru") -> InlineKeyboardMarkup:
    from app.bot.texts import t

    rows = [
        [
            _ib(t("btn_buy", lang), NavCB(to="buy").pack(), "cart"),
            _ib(t("btn_servers", lang), NavCB(to="servers").pack(), "monitor"),
        ],
        [
            _ib(t("btn_profile", lang), NavCB(to="profile").pack(), "user"),
            _ib(t("btn_support", lang), NavCB(to="support").pack(), "mega"),
        ],
    ]
    if is_admin:
        rows.append([_ib(t("btn_admin", lang), AdmCB(section="home").pack(), "crown")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_home_row(lang: str = "ru") -> list[InlineKeyboardButton]:
    from app.bot.texts import t

    return [_ib(t("btn_menu", lang), NavCB(to="home").pack(), "home")]


def back_profile_row(lang: str = "ru") -> list[InlineKeyboardButton]:
    from app.bot.texts import t

    return [_ib(t("btn_back", lang), NavCB(to="profile").pack(), "down")]


def profile_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    from app.bot.texts import t

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_ib(t("btn_topup", lang), BalCB(action="topup").pack(), "wallet")],
            [_ib(t("btn_ref", lang), ProfCB(action="ref").pack(), "users")],
            [
                _ib(t("btn_orders", lang), ProfCB(action="orders", arg="0").pack(), "bag"),
                _ib(t("btn_ledger", lang), ProfCB(action="ledger", arg="0").pack(), "chart"),
            ],
            [_ib(t("btn_promo", lang), ProfCB(action="promo").pack(), "gift")],
            [_ib(t("btn_autorenew", lang), ProfCB(action="autorenew").pack(), "clock")],
            [_ib(t("btn_lang", lang), NavCB(to="lang").pack(), "at")],
            back_home_row(lang),
        ]
    )


def support_kb(support_url: str, lang: str = "ru") -> InlineKeyboardMarkup:
    from app.bot.texts import t

    rows: list[list[InlineKeyboardButton]] = []
    if support_url:
        rows.append([_url(t("btn_write_manager", lang), support_url, "user")])
    rows.append(back_home_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def promo_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    from app.bot.texts import t

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_ib(t("btn_enter_promo", lang), ProfCB(action="promo_in").pack(), "gift")],
            back_profile_row(lang),
        ]
    )


def referral_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[back_profile_row(lang)])


def networks_kb(wallets: list[dict], amount: str, lang: str = "ru") -> InlineKeyboardMarkup:
    rows = [
        [_ib(w.get("label") or w["id"], BalCB(action="net", arg=f"{amount}|{w['id']}").pack(), "wallet")]
        for w in wallets
    ]
    rows.append(back_profile_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def invoice_kb(invoice_id: int, lang: str = "ru") -> InlineKeyboardMarkup:
    from app.bot.texts import t

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_ib(t("btn_check_pay", lang), BalCB(action="chk", arg=str(invoice_id)).pack(), "search")],
            [_ib(t("btn_back", lang), BalCB(action="topup").pack(), "down")],
        ]
    )


def plans_kb(draft_id: int, plans: list[tuple[str, str]], lang: str = "ru") -> InlineKeyboardMarkup:
    """plans: (plan_id, button_label) — one per row like screenshot."""
    from app.bot.texts import t

    rows = [
        [_ib(label, BuyCB(step="plan", draft_id=draft_id, arg=pid).pack(), "cube")]
        for pid, label in plans
    ]
    rows.append([_ib(t("btn_back", lang), BuyCB(step="back", draft_id=draft_id, arg="loc").pack(), "down")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def os_groups_kb(draft_id: int, groups: list[tuple[str, str, str]], lang: str = "ru") -> InlineKeyboardMarkup:
    """groups: (group_key, button_label, icon_pack_key)"""
    from app.bot.texts import t

    rows = [
        [_ib(label, BuyCB(step="osg", draft_id=draft_id, arg=key).pack(), icon)]
        for key, label, icon in groups
    ]
    rows.append([_ib(t("btn_back", lang), BuyCB(step="back", draft_id=draft_id, arg="plan").pack(), "down")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def os_versions_kb(
    draft_id: int, images: list[tuple[str, str, str]], lang: str = "ru"
) -> InlineKeyboardMarkup:
    """images: (os_id, button_label, icon_pack_key)"""
    from app.bot.texts import t

    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for oid, name, icon in images:
        row.append(_ib(name, BuyCB(step="os", draft_id=draft_id, arg=oid).pack(), icon))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_ib(t("btn_back", lang), BuyCB(step="back", draft_id=draft_id, arg="osg").pack(), "down")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def autorenew_kb(items: list[tuple[int, str, bool]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for sid, label, on in items:
        mark = "✓" if on else "○"
        rows.append(
            [
                _ib(
                    f"{mark} {label}",
                    ProfCB(action="ar_tog", arg=str(sid)).pack(),
                    "check" if on else "block",
                )
            ]
        )
    if not rows:
        rows.append([_ib("Нет серверов", NavCB(to="buy").pack(), "cart")])
    rows.append(back_profile_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def history_kb(*, back: str = "profile", page: int = 0, has_next: bool = False, action: str = "orders") -> InlineKeyboardMarkup:
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_ib("◀", ProfCB(action=action, arg=str(page - 1)).pack(), "down"))
    if has_next:
        nav.append(_ib("▶", ProfCB(action=action, arg=str(page + 1)).pack(), "up"))
    rows: list[list[InlineKeyboardButton]] = []
    if nav:
        rows.append(nav)
    rows.append(back_profile_row() if back == "profile" else back_home_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def locations_kb(items: list[tuple[str, str, str]]) -> InlineKeyboardMarkup:
    rows = [
        [_ib(f"{label} · от {price}", BuyCB(step="loc", arg=code).pack(), f"flag_{code}")]
        for code, label, price in items
    ]
    rows.append(back_home_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def term_kb(draft_id: int, terms: list[tuple[int, str]], lang: str = "ru") -> InlineKeyboardMarkup:
    from app.bot.texts import t

    rows = [
        [_ib(label, BuyCB(step="term", draft_id=draft_id, arg=str(months)).pack(), "clock")]
        for months, label in terms
    ]
    rows.append([_ib(t("btn_back", lang), BuyCB(step="back", draft_id=draft_id, arg="os").pack(), "down")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb(
    draft_id: int, *, can_pay: bool, price: str, missing: str | None, lang: str = "ru"
) -> InlineKeyboardMarkup:
    from app.bot.texts import t

    rows: list[list[InlineKeyboardButton]] = []
    if can_pay:
        rows.append(
            [_ib(t("btn_pay", lang, price=price), BuyCB(step="go", draft_id=draft_id).pack(), "check")]
        )
    else:
        rows.append(
            [
                _ib(
                    t("btn_topup_missing", lang, missing=missing or "10"),
                    BalCB(action="topup").pack(),
                    "wallet",
                )
            ]
        )
    rows.append([_ib(t("btn_back", lang), BuyCB(step="cancel", draft_id=draft_id).pack(), "block")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def balance_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    from app.bot.texts import t

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_ib(t("btn_topup", lang), BalCB(action="topup").pack(), "wallet")],
            [_ib(t("btn_ledger", lang), ProfCB(action="ledger", arg="0").pack(), "chart")],
            back_profile_row(lang),
        ]
    )


def topup_amounts_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    amounts = ["10", "25", "50", "100"]
    rows = [[_ib(f"${a}", BalCB(action="amt", arg=a).pack(), "wallet") for a in amounts[:2]]]
    rows.append([_ib(f"${a}", BalCB(action="amt", arg=a).pack(), "wallet") for a in amounts[2:]])
    rows.append([_ib("✏️", BalCB(action="own").pack(), "pin")])
    rows.append(back_profile_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def servers_kb(
    items: list[tuple[int, str]], page: int, total_pages: int, lang: str = "ru"
) -> InlineKeyboardMarkup:
    from app.bot.texts import t

    rows = [[_ib(label, SrvCB(action="open", server_id=sid).pack(), "monitor")] for sid, label in items]
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_ib("◀️", SrvCB(action="list", arg=str(page - 1)).pack(), "down"))
    if page + 1 < total_pages:
        nav.append(_ib("▶️", SrvCB(action="list", arg=str(page + 1)).pack(), "up"))
    if nav:
        rows.append(nav)
    rows.append([_ib(t("btn_buy", lang), NavCB(to="buy").pack(), "cart")])
    rows.append(back_home_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def server_kb(
    server_id: int,
    actions: set[str],
    *,
    auto_renew: bool,
    cancelled: bool = False,
    support_url: str | None = None,
    lang: str = "ru",
    panel_url: str | None = None,
    partner_id: str | None = None,
) -> InlineKeyboardMarkup:
    """User-facing manage keyboard."""
    from app.bot.texts import t

    rows: list[list[InlineKeyboardButton]] = []
    if "renew" in actions:
        rows.append(
            [_ib(t("srv_btn_renew", lang), SrvCB(action="rn", server_id=server_id).pack(), "clock")]
        )
    if "monitor" in actions:
        rows.append(
            [_ib(t("srv_btn_monitor", lang), SrvCB(action="mon", server_id=server_id).pack(), "chart")]
        )
    if "deploy" in actions:
        rows.append(
            [_ib(t("srv_btn_deploy", lang), SrvCB(action="dep", server_id=server_id).pack(), "cube")]
        )
    if "ai_fix" in actions:
        rows.append(
            [_ib(t("srv_btn_ai", lang), SrvCB(action="ai", server_id=server_id).pack(), "robot")]
        )

    power_row: list[InlineKeyboardButton] = []
    if "stop" in actions:
        power_row.append(
            _ib(t("srv_btn_stop", lang), SrvCB(action="pwr", server_id=server_id, arg="stop").pack(), "down")
        )
    if "start" in actions:
        power_row.append(
            _ib(t("srv_btn_start", lang), SrvCB(action="pwr", server_id=server_id, arg="start").pack(), "up")
        )
    if "restart" in actions:
        power_row.append(
            _ib(
                t("srv_btn_restart", lang),
                SrvCB(action="pwr", server_id=server_id, arg="restart").pack(),
                "robot",
            )
        )
    if power_row:
        rows.append(power_row)

    mid: list[InlineKeyboardButton] = []
    if "reinstall" in actions:
        mid.append(
            _ib(t("srv_btn_os", lang), SrvCB(action="ri", server_id=server_id).pack(), "term")
        )
    mid.append(
        _ib(t("srv_btn_rename", lang), SrvCB(action="nm", server_id=server_id).pack(), "pin")
    )
    rows.append(mid)

    support = (support_url or "https://t.me/arxixx").strip()
    rows.append(
        [
            _url(t("srv_btn_ip", lang), support, "link"),
            _url(t("srv_btn_upgrade", lang), support, "up"),
        ]
    )

    if "scripts" in actions:
        rows.append(
            [_ib(t("srv_btn_script", lang), SrvCB(action="sc", server_id=server_id).pack(), "hammer")]
        )

    ar = "off" if auto_renew else "on"
    rows.append(
        [
            _ib(
                t("srv_btn_autorenew_off", lang) if auto_renew else t("srv_btn_autorenew_on", lang),
                SrvCB(action="ar", server_id=server_id, arg=ar).pack(),
                "block" if auto_renew else "check",
            )
        ]
    )
    rows.append([_ib(t("srv_btn_back", lang), SrvCB(action="list", arg="0").pack(), "down")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reinstall_os_groups_kb(
    server_id: int, groups: list[tuple[str, str, str]], lang: str = "ru"
) -> InlineKeyboardMarkup:
    from app.bot.texts import t

    rows = [
        [_ib(label, SrvCB(action="ri_g", server_id=server_id, arg=key).pack(), icon)]
        for key, label, icon in groups
    ]
    rows.append([_ib(t("srv_btn_back", lang), SrvCB(action="open", server_id=server_id).pack(), "down")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reinstall_os_versions_kb(
    server_id: int, images: list[tuple[str, str, str]], lang: str = "ru"
) -> InlineKeyboardMarkup:
    from app.bot.texts import t

    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for oid, name, icon in images:
        row.append(_ib(name, SrvCB(action="ri_os", server_id=server_id, arg=oid).pack(), icon))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_ib(t("btn_back", lang), SrvCB(action="ri", server_id=server_id).pack(), "down")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def renew_days_kb(server_id: int, days: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [
        [_ib(label, SrvCB(action="rn_d", server_id=server_id, arg=str(d)).pack(), "clock")]
        for d, label in days
    ]
    rows.append([_ib("Назад", SrvCB(action="open", server_id=server_id).pack(), "down")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_home_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    from app.bot.texts import t

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _ib(t("adm_btn_users", lang), AdmCB(section="usr", action="list", arg="0").pack(), "users"),
                _ib(t("adm_btn_servers", lang), AdmCB(section="srv", action="list", arg="0").pack(), "monitor"),
            ],
            [
                _ib(t("adm_btn_orders", lang), AdmCB(section="ord", action="list", arg="0").pack(), "bag"),
                _ib(t("adm_btn_invoices", lang), AdmCB(section="inv", action="list", arg="0").pack(), "wallet"),
            ],
            [
                _ib(t("adm_btn_jobs", lang), AdmCB(section="job", action="list", arg="0").pack(), "robot"),
                _ib(t("adm_btn_fin", lang), AdmCB(section="fin", action="open").pack(), "chart"),
            ],
            [
                _ib(t("adm_btn_settings", lang), AdmCB(section="set", action="open").pack(), "hammer"),
                _ib(t("adm_btn_wallets", lang), AdmCB(section="wal", action="list").pack(), "wallet"),
            ],
            [
                _ib(t("adm_btn_audit", lang), AdmCB(section="aud", action="list", arg="0").pack(), "chart"),
                _ib(t("adm_btn_broadcast", lang), AdmCB(section="bc", action="open").pack(), "mega"),
            ],
            back_home_row(lang),
        ]
    )


def admin_back_home(lang: str = "ru") -> list[InlineKeyboardButton]:
    from app.bot.texts import t

    return [_ib(t("adm_btn_admin", lang), AdmCB(section="home").pack(), "crown")]


def admin_list_kb(
    section: str,
    items: list[tuple[str, str]],
    *,
    page: int = 0,
    has_next: bool = False,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    """items: (arg_id, button_label)"""
    rows = [
        [_ib(label, AdmCB(section=section, action="open", arg=iid).pack(), "cube")]
        for iid, label in items
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_ib("◀️", AdmCB(section=section, action="list", arg=str(page - 1)).pack(), "down"))
    if has_next:
        nav.append(_ib("▶️", AdmCB(section=section, action="list", arg=str(page + 1)).pack(), "up"))
    if nav:
        rows.append(nav)
    rows.append(admin_back_home(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_user_kb(user_id: int, *, banned: bool, lang: str = "ru") -> InlineKeyboardMarkup:
    from app.bot.texts import t

    ban_label = t("adm_unban", lang) if banned else t("adm_ban", lang)
    ban_act = "unban" if banned else "ban"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _ib(ban_label, AdmCB(section="usr", action=ban_act, arg=str(user_id)).pack(), "block"),
                _ib(t("adm_balance", lang), AdmCB(section="usr", action="bal", arg=str(user_id)).pack(), "wallet"),
            ],
            [
                _ib(t("adm_user_servers", lang), AdmCB(section="usr", action="servers", arg=str(user_id)).pack(), "monitor"),
                _ib(t("adm_user_orders", lang), AdmCB(section="usr", action="orders", arg=str(user_id)).pack(), "bag"),
            ],
            [
                _ib(
                    t("adm_attach_server", lang),
                    AdmCB(section="usr", action="attach", arg=str(user_id)).pack(),
                    "link",
                )
            ],
            [_ib(t("btn_back", lang), AdmCB(section="usr", action="list", arg="0").pack(), "down")],
        ]
    )


def admin_server_kb(server_id: int, user_id: int, lang: str = "ru") -> InlineKeyboardMarkup:
    from app.bot.texts import t

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _ib(t("adm_open_user", lang), AdmCB(section="usr", action="open", arg=str(user_id)).pack(), "user"),
            ],
            [
                _ib("▶ Start", AdmCB(section="srv", action="pwr", arg=f"{server_id}|start").pack(), "up"),
                _ib("⏹ Stop", AdmCB(section="srv", action="pwr", arg=f"{server_id}|stop").pack(), "down"),
                _ib("↻", AdmCB(section="srv", action="pwr", arg=f"{server_id}|restart").pack(), "robot"),
            ],
            [
                _ib(t("adm_freeze", lang), AdmCB(section="srv", action="freeze", arg=str(server_id)).pack(), "lock"),
                _ib(t("adm_unfreeze", lang), AdmCB(section="srv", action="unfreeze", arg=str(server_id)).pack(), "check"),
            ],
            [_ib(t("btn_back", lang), AdmCB(section="srv", action="list", arg="0").pack(), "down")],
        ]
    )


def admin_order_kb(order_id: int, user_id: int, *, can_retry: bool, lang: str = "ru") -> InlineKeyboardMarkup:
    from app.bot.texts import t

    rows: list[list[InlineKeyboardButton]] = [
        [_ib(t("adm_open_user", lang), AdmCB(section="usr", action="open", arg=str(user_id)).pack(), "user")],
    ]
    if can_retry:
        rows.append(
            [_ib(t("adm_retry", lang), AdmCB(section="ord", action="retry", arg=str(order_id)).pack(), "robot")]
        )
    rows.append([_ib(t("btn_back", lang), AdmCB(section="ord", action="list", arg="0").pack(), "down")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_invoice_kb(invoice_id: int, user_id: int, *, pending: bool, lang: str = "ru") -> InlineKeyboardMarkup:
    from app.bot.texts import t

    rows: list[list[InlineKeyboardButton]] = [
        [_ib(t("adm_open_user", lang), AdmCB(section="usr", action="open", arg=str(user_id)).pack(), "user")],
    ]
    if pending:
        rows.append(
            [_ib(t("adm_mark_paid", lang), AdmCB(section="inv", action="paid", arg=str(invoice_id)).pack(), "check")]
        )
    rows.append([_ib(t("btn_back", lang), AdmCB(section="inv", action="list", arg="0").pack(), "down")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_job_kb(job_id: int, lang: str = "ru") -> InlineKeyboardMarkup:
    from app.bot.texts import t

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_ib(t("adm_cancel_job", lang), AdmCB(section="job", action="cancel", arg=str(job_id)).pack(), "block")],
            [_ib(t("btn_back", lang), AdmCB(section="job", action="list", arg="0").pack(), "down")],
        ]
    )


def admin_settings_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    from app.bot.texts import t

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _ib("Markup −", AdmCB(section="set", action="mk-", arg="-").pack(), "down"),
                _ib("Markup +", AdmCB(section="set", action="mk+", arg="-").pack(), "up"),
            ],
            [
                _ib("Fee −", AdmCB(section="set", action="fee-", arg="-").pack(), "down"),
                _ib("Fee +", AdmCB(section="set", action="fee+", arg="-").pack(), "up"),
            ],
            [_ib(t("adm_maint_tog", lang), AdmCB(section="set", action="maint", arg="-").pack(), "warn")],
            admin_back_home(lang),
        ]
    )


def admin_wallets_kb(wallets: list[dict], lang: str = "ru") -> InlineKeyboardMarkup:
    rows = []
    for w in wallets:
        on = bool(w.get("enabled") and w.get("address"))
        mark = "🟢" if on else "🔴"
        label = f"{mark} {w.get('label') or w['id']}"
        rows.append(
            [_ib(label, AdmCB(section="wal", action="open", arg=w["id"]).pack(), "wallet")]
        )
    rows.append(admin_back_home(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_wallet_kb(wallet_id: str, *, enabled: bool, has_addr: bool, lang: str = "ru") -> InlineKeyboardMarkup:
    from app.bot.texts import t

    tog = t("adm_pay_off", lang) if enabled else t("adm_pay_on", lang)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _ib(
                    tog,
                    AdmCB(section="wal", action="tog", arg=wallet_id).pack(),
                    "check" if not enabled else "block",
                )
            ],
            [
                _ib(
                    t("adm_set_addr", lang),
                    AdmCB(section="wal", action="addr", arg=wallet_id).pack(),
                    "pin",
                )
            ],
            [_ib(t("btn_back", lang), AdmCB(section="wal", action="list").pack(), "down")],
        ]
    )


def lang_kb(*, onboarding: bool = False, lang: str = "en") -> InlineKeyboardMarkup:
    rows = [
        [
            _ib("English", NavCB(to="lang_en").pack(), "at"),
            _ib("Русский", NavCB(to="lang_ru").pack(), "check"),
        ]
    ]
    if not onboarding:
        rows.append(back_profile_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def agent_control_kb(
    *,
    job_id: int,
    server_id: int,
    lang: str = "en",
    show_stop: bool = True,
) -> InlineKeyboardMarkup:
    """Inline shell while AI is running / asking."""
    from app.bot.texts import t

    rows: list[list[InlineKeyboardButton]] = []
    if show_stop and job_id:
        rows.append(
            [
                _ib(
                    t("agent_btn_stop", lang),
                    AgentCB(action="stop", job_id=job_id, server_id=server_id).pack(),
                    "block",
                )
            ]
        )
    rows.append(
        [
            _ib(
                t("srv_btn_back", lang),
                AgentCB(action="open", job_id=job_id, server_id=server_id).pack(),
                "down",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def agent_plan_kb(
    *,
    job_id: int,
    server_id: int,
    lang: str = "en",
) -> InlineKeyboardMarkup:
    """Work on plan / Stop / Back after analyze proposes a plan."""
    from app.bot.texts import t

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _ib(
                    t("agent_btn_plan_go", lang),
                    AgentCB(action="plan_go", job_id=job_id, server_id=server_id).pack(),
                    "check",
                )
            ],
            [
                _ib(
                    t("agent_btn_stop", lang),
                    AgentCB(action="stop", job_id=job_id, server_id=server_id).pack(),
                    "block",
                )
            ],
            [
                _ib(
                    t("srv_btn_back", lang),
                    AgentCB(action="open", job_id=job_id, server_id=server_id).pack(),
                    "down",
                )
            ],
        ]
    )


def deploy_strategy_kb(*, server_id: int, lang: str = "en") -> InlineKeyboardMarkup:
    """Overwrite existing app vs deploy alongside."""
    from app.bot.texts import t

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _ib(
                    t("agent_btn_dep_over", lang),
                    AgentCB(action="dep_over", server_id=server_id).pack(),
                    "cube",
                )
            ],
            [
                _ib(
                    t("agent_btn_dep_side", lang),
                    AgentCB(action="dep_side", server_id=server_id).pack(),
                    "up",
                )
            ],
            [
                _ib(
                    t("srv_btn_back", lang),
                    SrvCB(action="open", server_id=server_id).pack(),
                    "down",
                )
            ],
        ]
    )
