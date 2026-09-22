from __future__ import annotations

from pathlib import Path

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, FSInputFile, InputMediaPhoto, Message

from app.bot.keyboards import panel_keyboard

ASSETS = Path(__file__).resolve().parents[2] / "assets"

BANNER_FILES: dict[str, str] = {
    "home": "banner.png",
    "profile": "banner_profile.png",
    "support": "banner_support.png",
    "servers": "banner_servers.png",
    "buy": "banner_buy.png",
    "balance": "banner_balance.png",
    "referral": "banner_referral.png",
    "promo": "banner_promo.png",
}

_file_ids: dict[str, str] = {}


def _msg(event: Message | CallbackQuery) -> Message | None:
    if isinstance(event, CallbackQuery):
        return event.message
    return event


def _has_media(message: Message | None) -> bool:
    if message is None:
        return False
    return bool(message.photo or message.document or message.video or message.animation)


async def _ack(event: Message | CallbackQuery) -> None:
    if isinstance(event, CallbackQuery):
        try:
            await event.answer()
        except Exception:
            pass


def _banner_path(name: str, lang: str = "ru") -> Path | None:
    filename = BANNER_FILES.get(name) or BANNER_FILES["home"]
    stem = Path(filename).stem
    if lang == "en":
        en = ASSETS / f"{stem}_en.png"
        if en.is_file():
            return en
    path = ASSETS / filename
    if path.is_file():
        return path
    home = ASSETS / BANNER_FILES["home"]
    return home if home.is_file() else None


def _banner_media(name: str, lang: str = "ru") -> str | FSInputFile | None:
    cache_key = f"{name}:{lang}"
    if cache_key in _file_ids:
        return _file_ids[cache_key]
    path = _banner_path(name, lang)
    if path is None:
        return None
    return FSInputFile(path)


async def _remember(name: str, lang: str, message: Message | None) -> None:
    if message and message.photo:
        _file_ids[f"{name}:{lang}"] = message.photo[-1].file_id


async def safe_edit(
    event: Message | CallbackQuery,
    text: str,
    reply_markup=None,
) -> None:
    kwargs = {"reply_markup": reply_markup, "parse_mode": ParseMode.HTML}
    message = _msg(event)

    if isinstance(event, CallbackQuery) and message is not None:
        try:
            if _has_media(message):
                try:
                    await message.delete()
                except Exception:
                    pass
                await message.answer(text, **kwargs)
            else:
                await message.edit_text(text, **kwargs)
        except TelegramBadRequest as e:
            if "not modified" not in str(e).lower():
                await message.answer(text, **kwargs)
        except Exception:
            await message.answer(text, **kwargs)
        await _ack(event)
        return

    await event.answer(text, reply_markup=reply_markup or panel_keyboard(), parse_mode=ParseMode.HTML)


async def show_banner(
    event: Message | CallbackQuery,
    text: str,
    reply_markup,
    *,
    banner: str = "home",
    lang: str = "ru",
) -> None:
    """Show section banner photo with caption + inline keyboard (lang-aware assets)."""
    photo = _banner_media(banner, lang)
    message = _msg(event)

    if photo is None:
        await safe_edit(event, text, reply_markup)
        return

    if isinstance(event, CallbackQuery) and message is not None:
        try:
            if _has_media(message):
                media = InputMediaPhoto(media=photo, caption=text, parse_mode=ParseMode.HTML)
                edited = await message.edit_media(media=media, reply_markup=reply_markup)
                await _remember(banner, lang, edited if isinstance(edited, Message) else message)
            else:
                try:
                    await message.delete()
                except Exception:
                    pass
                sent = await message.answer_photo(
                    photo=photo,
                    caption=text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML,
                )
                await _remember(banner, lang, sent)
        except Exception:
            sent = await message.answer_photo(
                photo=photo,
                caption=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML,
            )
            await _remember(banner, lang, sent)
        await _ack(event)
        return

    sent = await event.answer_photo(
        photo=photo,
        caption=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML,
    )
    await _remember(banner, lang, sent)


async def show_home_banner(event, text, reply_markup, *, lang: str = "ru") -> None:
    await show_banner(event, text, reply_markup, banner="home", lang=lang)
