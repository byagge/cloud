from __future__ import annotations

from app.bot.texts import en, ru

_LOCALES = {"ru": ru.TEXTS, "en": en.TEXTS}


def t(key: str, lang: str = "en", **kwargs) -> str:
    pack = _LOCALES.get(lang) or en.TEXTS
    template = pack.get(key) or en.TEXTS.get(key) or ru.TEXTS.get(key) or key
    try:
        return template.format(**kwargs)
    except Exception:
        return template
