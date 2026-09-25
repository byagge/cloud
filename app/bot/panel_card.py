"""Track + refresh the open server card message when SSH password arrives."""

from __future__ import annotations

import json
from typing import Any

from app.logging import get_logger

log = get_logger("bot.panel_card")

CARD_TTL = 600


async def remember_server_card(
    redis,
    *,
    server_id: int,
    chat_id: int,
    message_id: int,
    user_id: int,
) -> None:
    await redis.set(
        f"panel:card:{server_id}",
        json.dumps(
            {"chat_id": chat_id, "message_id": message_id, "user_id": user_id}
        ),
        ex=CARD_TTL,
    )


async def refresh_server_card_password(session, redis, bot, server) -> bool:
    """Edit the last opened server card to include the real password."""
    from app.bot.keyboards import server_kb
    from app.bot.ui.screens import server_card_text
    from app.config import get_settings
    from app.core.secrets import get_secret
    from app.core.servers import ACTIONS, display_status
    from app.db.models import User

    raw = await redis.get(f"panel:card:{server.id}")
    if not raw:
        return False
    try:
        meta = json.loads(raw if isinstance(raw, str) else raw.decode())
        chat_id = int(meta["chat_id"])
        message_id = int(meta["message_id"])
        user_id = int(meta["user_id"])
    except Exception:
        return False

    user = await session.get(User, user_id)
    if user is None:
        return False
    secret = await get_secret(redis, f"cred:{server.id}")
    password = (secret or {}).get("password")
    if not password:
        return False

    lang = user.lang or "ru"
    try:
        st = display_status(server)
        actions = ACTIONS.get(st, set())
        text = server_card_text(user, server, password=password)
    except Exception:
        log.exception("panel_card_build_failed", server_id=server.id)
        return False

    settings = get_settings()
    kb = server_kb(
        server.id,
        actions,
        auto_renew=server.auto_renew,
        cancelled=server.cancelled,
        support_url=settings.support_url,
        lang=lang,
    )
    try:
        # Prefer caption edit (banner photo)
        await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=text[:1024],
            reply_markup=kb,
            parse_mode="HTML",
        )
        return True
    except Exception:
        pass
    try:
        await bot.edit_message_text(
            text,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=kb,
            parse_mode="HTML",
        )
        return True
    except Exception as e:
        log.debug("panel_card_edit_failed", err=str(e)[:160], server_id=server.id)
        return False
