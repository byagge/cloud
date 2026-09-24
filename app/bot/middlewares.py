from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User as TgUser
from sqlalchemy import select

from app.config import get_settings
from app.core.settings_store import settings_store
from app.db.models import Admin, User
from app.db.session import get_session_factory
from app.jobs.results import now_utc


def detect_lang(language_code: str | None, *, fallback: str | None = None) -> str:
    """Map Telegram language_code → bot lang. Default English."""
    fb = (fallback or get_settings().default_lang or "en").lower()
    if fb not in {"ru", "en"}:
        fb = "en"
    if not language_code:
        return fb
    code = language_code.lower().replace("_", "-")
    if code == "ru" or code.startswith("ru-"):
        return "ru"
    return "en"


def _tg_user(event: TelegramObject, data: dict[str, Any]) -> TgUser | None:
    # Outer middleware on Update: UserContextMiddleware already puts event_from_user
    user = data.get("event_from_user")
    if isinstance(user, TgUser):
        return user
    if isinstance(event, Message) and event.from_user:
        return event.from_user
    if isinstance(event, CallbackQuery) and event.from_user:
        return event.from_user
    return None


class DbUserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = _tg_user(event, data)
        if tg_user is None:
            return await handler(event, data)

        factory = get_session_factory()
        async with factory() as session:
            user = await session.scalar(select(User).where(User.tg_id == tg_user.id))
            if user is None:
                user = User(
                    tg_id=tg_user.id,
                    username=tg_user.username,
                    first_name=tg_user.first_name,
                    lang=detect_lang(tg_user.language_code),
                )
                session.add(user)
                await session.flush()
                settings = get_settings()
                if settings.owner_tg_id and tg_user.id == settings.owner_tg_id:
                    existing = await session.get(Admin, tg_user.id)
                    if existing is None:
                        session.add(Admin(tg_id=tg_user.id, role="owner"))
            else:
                user.username = tg_user.username
                user.first_name = tg_user.first_name
                user.last_seen_at = now_utc()
                # first-run only: keep detected lang until user picks / accepts terms
                if not user.accepted_terms and tg_user.language_code:
                    detected = detect_lang(tg_user.language_code)
                    if user.lang not in {"ru", "en"}:
                        user.lang = detected
            admin = await session.get(Admin, tg_user.id)
            await session.commit()
            # expire_on_commit=False keeps attributes; refresh to be safe after commit
            await session.refresh(user)
            data["db_user"] = user
            data["db_session_factory"] = factory
            data["admin_role"] = admin.role if admin else None
            data["settings_store"] = settings_store
        return await handler(event, data)


class BanMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user: User | None = data.get("db_user")
        if user and user.banned:
            from app.bot.texts import t

            text = t("banned", user.lang)
            # Ban check runs on Update — answer via event message/callback if present
            from aiogram.types import Update

            if isinstance(event, Update):
                if event.callback_query:
                    await event.callback_query.answer(text, show_alert=True)
                    return None
                if event.message:
                    await event.message.answer(text)
                    return None
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
                return None
            if isinstance(event, Message):
                await event.answer(text)
                return None
        return await handler(event, data)


class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if data.get("admin_role"):
            return await handler(event, data)
        if not settings_store.bool("maintenance"):
            return await handler(event, data)

        from aiogram.types import Update

        cb_data = None
        if isinstance(event, Update) and event.callback_query:
            cb_data = event.callback_query.data
        elif isinstance(event, CallbackQuery):
            cb_data = event.data

        if cb_data and (
            cb_data.startswith("nav:servers")
            or cb_data.startswith("nav:balance")
            or cb_data.startswith("nav:home")
            or cb_data.startswith("srv:list")
            or cb_data.startswith("srv:open")
        ):
            return await handler(event, data)

        from app.bot.texts import t

        user: User | None = data.get("db_user")
        lang = user.lang if user else "en"
        text = t("maintenance", lang)

        if isinstance(event, Update):
            if event.callback_query:
                await event.callback_query.answer(text, show_alert=True)
                return None
            if event.message and event.message.text and event.message.text.startswith("/"):
                await event.message.answer(text)
                return None
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
            return None
        if isinstance(event, Message) and event.text and event.text.startswith("/"):
            await event.answer(text)
            return None
        return await handler(event, data)
