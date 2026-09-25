"""Live Telegram status board for AI agent (edit one message as work progresses)."""

from __future__ import annotations

import asyncio
import html
import time
from dataclasses import dataclass, field
from typing import Any

from app.logging import get_logger

log = get_logger("agent.progress")


def _esc(s: str) -> str:
    return html.escape(s or "", quote=False)


@dataclass
class ProgressBoard:
    """Edits a single Telegram message to show real AI activity."""

    bot: Any
    chat_id: int
    lang: str = "en"
    message_thread_id: int | None = None
    reply_markup: Any | None = None
    title: str = ""
    message_id: int | None = None
    lines: list[str] = field(default_factory=list)
    current: str = ""
    _last_edit: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    min_edit_interval: float = 1.2
    max_lines: int = 12

    async def ensure(self) -> None:
        if self.message_id:
            return
        text = self.render()
        kwargs: dict[str, Any] = {"parse_mode": "HTML"}
        if self.message_thread_id:
            kwargs["message_thread_id"] = self.message_thread_id
        if self.reply_markup is not None:
            kwargs["reply_markup"] = self.reply_markup
        try:
            msg = await self.bot.send_message(self.chat_id, text, **kwargs)
            self.message_id = msg.message_id
        except Exception:
            log.exception("progress_send_failed")

    def render(self) -> str:
        title = self.title or ("🛠 AI" if self.lang == "en" else "🛠 ИИ")
        parts = [f"<b>{_esc(title)}</b>", ""]
        for line in self.lines[-self.max_lines :]:
            parts.append(line)
        if self.current:
            parts.append("")
            parts.append(f"⏳ <i>{_esc(self.current)}</i>")
        return "\n".join(parts)[:3900]

    async def _flush(self, *, force: bool = False) -> None:
        async with self._lock:
            if not self.message_id:
                await self.ensure()
                return
            now = time.monotonic()
            if not force and (now - self._last_edit) < self.min_edit_interval:
                return
            self._last_edit = now
            text = self.render()
            kwargs: dict[str, Any] = {"parse_mode": "HTML"}
            if self.reply_markup is not None:
                kwargs["reply_markup"] = self.reply_markup
            try:
                await self.bot.edit_message_text(
                    text,
                    chat_id=self.chat_id,
                    message_id=self.message_id,
                    **kwargs,
                )
            except Exception as e:
                # ignore "message is not modified"
                if "not modified" not in str(e).lower():
                    log.debug("progress_edit_failed", err=str(e)[:160])

    async def set_title(self, title: str) -> None:
        self.title = title
        await self._flush(force=True)

    async def set_current(self, text: str) -> None:
        self.current = (text or "")[:200]
        await self._flush()

    async def done_line(self, text: str, *, ok: bool = True) -> None:
        mark = "✅" if ok else "⚠️"
        self.lines.append(f"{mark} {_esc(text[:220])}")
        self.current = ""
        await self._flush(force=True)

    async def fail_line(self, text: str) -> None:
        self.lines.append(f"❌ {_esc(text[:220])}")
        self.current = ""
        await self._flush(force=True)

    async def finish(self, summary: str, *, ok: bool = True, reply_markup: Any | None = None) -> None:
        mark = "✅" if ok else "❌"
        self.current = ""
        self.lines.append(f"{mark} <b>{_esc(summary[:500])}</b>")
        if reply_markup is not None:
            self.reply_markup = reply_markup
        await self._flush(force=True)
