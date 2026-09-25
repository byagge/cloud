from __future__ import annotations

from html import escape

from app.bot.texts import t
from app.bot.ui.emoji import pe
from app.core.money import format_usd
from app.core.servers import STATUS_DOT, STATUS_EN, STATUS_RU, display_status


def decorate(text: str) -> str:
    mapping = {
        "{cube}": pe("cube"),
        "{wallet}": pe("wallet"),
        "{monitor}": pe("monitor"),
        "{info}": pe("info"),
        "{pin}": pe("pin"),
        "{term}": pe("term"),
        "{clock}": pe("clock"),
        "{check}": pe("check"),
        "{robot}": pe("robot"),
        "{lock}": pe("lock"),
        "{warn}": pe("warn"),
        "{crown}": pe("crown"),
        "{user}": pe("user"),
        "{mega}": pe("mega"),
        "{gift}": pe("gift"),
        "{users}": pe("users"),
        "{chart}": pe("chart"),
        "{bag}": pe("bag"),
    }
    for k, v in mapping.items():
        text = text.replace(k, v)
    return text


def home_text(user, servers_count: int, expiring: int) -> str:
    lang = user.lang or "ru"
    line = ""
    if expiring:
        line = t("home_expiring", lang, n=expiring)
    return decorate(
        t(
            "home",
            lang,
            balance=format_usd(user.balance_usd),
            count=servers_count,
            expiring_line=line,
            cube="{cube}",
            wallet="{wallet}",
            monitor="{monitor}",
        )
    )


def profile_text(user, *, servers_count: int, refs_count: int, auto_renew_on: int) -> str:
    lang = user.lang or "ru"
    uname = f"@{escape(user.username)}" if user.username else "—"
    name = escape(user.first_name or "—")
    created = user.created_at.strftime("%d.%m.%Y") if user.created_at else "—"
    lang_name = t("lang_ru", lang) if lang == "ru" else t("lang_en", lang)
    return decorate(
        t("profile_title", lang, user="{user}")
        + "\n"
        + t(
            "profile_body",
            lang,
            tg_id=user.tg_id,
            username=uname,
            name=name,
            balance=format_usd(user.balance_usd),
            servers=servers_count,
            auto=auto_renew_on,
            refs=refs_count,
            lang_name=lang_name,
            created=created,
        )
    )


def support_text(support_url: str, manager: str = "@arxixx", lang: str = "ru") -> str:
    handle = manager if manager.startswith("@") else f"@{manager}"
    link = support_url or f"https://t.me/{handle.lstrip('@')}"
    manager_html = f'<a href="{escape(link)}">{escape(handle)}</a>'
    return decorate(
        t("support_title", lang, mega="{mega}")
        + t(
            "support_body",
            lang,
            user="{user}",
            manager=manager_html,
            clock="{clock}",
            check="{check}",
        )
    )


def referral_text(*, link: str, refs: int, earned: str, lang: str = "ru") -> str:
    return decorate(
        t("referral_title", lang, users="{users}")
        + t(
            "referral_body",
            lang,
            link=escape(link),
            refs=refs,
            earned=earned,
        )
        + "\n\n"
        + t("referral_works", lang)
    )


def promo_text(lang: str = "ru") -> str:
    return decorate(t("promo_title", lang, gift="{gift}"))


def autorenew_text(lang: str = "ru") -> str:
    return decorate(t("autorenew_title", lang, clock="{clock}"))


def server_card_text(user, server, *, password: str | None = None) -> str:
    lang = user.lang or "ru"
    st = display_status(server)
    status_map = STATUS_RU if lang == "ru" else STATUS_EN
    expires_at = server.rent_expires_at
    try:
        expires = expires_at.strftime("%d.%m.%Y %H:%M") if expires_at else "—"
    except Exception:
        expires = "—"
    auto = (
        ("да" if lang == "ru" else "yes")
        if server.auto_renew and not server.cancelled
        else ("нет" if lang == "ru" else "no")
    )
    pwd = password if password else "—"
    ram = f"{(server.ram_mb or 0) / 1024:.1f} GB" if server.ram_mb else "—"
    return decorate(
        t(
            "server_card",
            lang,
            dot=STATUS_DOT.get(st, "⚪"),
            name=escape(server.display_name or f"server-{server.id}"),
            status=status_map.get(st, str(st)),
            location=escape(server.location or "—"),
            plan=escape(server.plan_label or "—"),
            os=escape(server.os_label or "—"),
            ip=escape(str(server.ip or "—")),
            login=escape(str(server.login or "root")),
            password=escape(str(pwd)),
            cpu=server.cpu or "—",
            ram=ram,
            disk=f"{server.disk_gb} GB" if server.disk_gb else "—",
            sid=server.id,
            expires=expires,
            auto_renew=auto,
        )
    )
