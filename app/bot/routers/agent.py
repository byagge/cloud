"""Autodeploy + AI project support FSM (file/photo upload, agent Q&A)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import SrvCB
from app.bot.render import show_banner
from app.bot.texts import t
from app.bot.ui.screens import decorate
from app.config import get_settings
from app.core.secrets import get_secret
from app.db.models import Job, Server
from app.db.session import get_session_factory

router = Router()

UPLOAD_ROOT = Path("data/uploads")


class AgentDeploy(StatesGroup):
    waiting_file = State()


class AgentSupport(StatesGroup):
    waiting_problem = State()


class HasPendingAgentAsk(Filter):
    async def __call__(self, message: Message, redis, db_user) -> bool:
        if not db_user:
            return False
        return bool(await redis.get(f"agent:wait:{db_user.id}"))


def _uploads_dir(user_id: int) -> Path:
    d = UPLOAD_ROOT / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _require_server(session, server_id: int, user_id: int) -> Server | None:
    server = await session.get(Server, server_id)
    if not server or server.user_id != user_id:
        return None
    return server


def _back_kb(server_id: int, lang: str):
    from aiogram.types import InlineKeyboardMarkup
    from app.bot.keyboards import _ib

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_ib(t("srv_btn_back", lang), SrvCB(action="open", server_id=server_id).pack(), "down")]
        ]
    )


@router.callback_query(SrvCB.filter(F.action == "dep"))
async def deploy_start(query: CallbackQuery, callback_data: SrvCB, state: FSMContext, db_user, redis) -> None:
    lang = db_user.lang or "en"
    settings = get_settings()
    if not (settings.gemini_api_key or "").strip():
        await query.answer(t("agent_need_gemini", lang), show_alert=True)
        return
    factory = get_session_factory()
    async with factory() as session:
        server = await _require_server(session, callback_data.server_id, db_user.id)
        if not server:
            await query.answer(t("not_found", lang), show_alert=True)
            return
        if not server.ip:
            await query.answer("No IP yet", show_alert=True)
            return
    secret = await get_secret(redis, f"cred:{callback_data.server_id}")
    if not secret or not secret.get("password"):
        await query.answer(t("agent_need_password", lang), show_alert=True)
        return
    await state.set_state(AgentDeploy.waiting_file)
    await state.update_data(agent_server_id=callback_data.server_id)
    text = decorate(t("agent_deploy_ask", lang))
    await show_banner(query, text, _back_kb(callback_data.server_id, lang), banner="servers", lang=lang)
    await query.answer()


@router.callback_query(SrvCB.filter(F.action == "ai"))
async def ai_start(query: CallbackQuery, callback_data: SrvCB, state: FSMContext, db_user, redis) -> None:
    lang = db_user.lang or "en"
    settings = get_settings()
    if not (settings.gemini_api_key or "").strip():
        await query.answer(t("agent_need_gemini", lang), show_alert=True)
        return
    factory = get_session_factory()
    async with factory() as session:
        server = await _require_server(session, callback_data.server_id, db_user.id)
        if not server:
            await query.answer(t("not_found", lang), show_alert=True)
            return
        if not server.ip:
            await query.answer("No IP yet", show_alert=True)
            return
    secret = await get_secret(redis, f"cred:{callback_data.server_id}")
    if not secret or not secret.get("password"):
        await query.answer(t("agent_need_password", lang), show_alert=True)
        return
    await state.set_state(AgentSupport.waiting_problem)
    await state.update_data(agent_server_id=callback_data.server_id)
    text = decorate(t("agent_ai_ask", lang))
    await show_banner(query, text, _back_kb(callback_data.server_id, lang), banner="servers", lang=lang)
    await query.answer()


@router.message(AgentDeploy.waiting_file, F.document)
async def deploy_file(message: Message, state: FSMContext, db_user, bot) -> None:
    lang = db_user.lang or "en"
    data = await state.get_data()
    server_id = int(data.get("agent_server_id") or 0)
    doc = message.document
    if not doc:
        await message.answer("Send a file")
        return
    name = doc.file_name or f"upload-{uuid.uuid4().hex[:8]}"
    dest = _uploads_dir(db_user.id) / f"{uuid.uuid4().hex[:10]}_{name}"
    await bot.download(doc, destination=dest)
    caption = (message.caption or "").strip()
    goal = caption or f"Deploy the uploaded project archive {name} on this VPS."
    await state.clear()
    await _enqueue_agent(
        db_user_id=db_user.id,
        server_id=server_id,
        mode="deploy",
        goal=goal,
        local_file=str(dest),
    )
    await message.answer(t("agent_started", lang))


@router.message(AgentDeploy.waiting_file)
async def deploy_need_file(message: Message, db_user) -> None:
    await message.answer(t("agent_deploy_ask", db_user.lang or "en"))


@router.message(AgentSupport.waiting_problem, F.photo)
async def support_photo(message: Message, state: FSMContext, db_user, bot) -> None:
    lang = db_user.lang or "en"
    data = await state.get_data()
    server_id = int(data.get("agent_server_id") or 0)
    photo = message.photo[-1]
    dest = _uploads_dir(db_user.id) / f"{uuid.uuid4().hex[:10]}.jpg"
    await bot.download(photo, destination=dest)
    caption = (message.caption or "").strip() or "See attached screenshot — something is broken."
    await state.clear()
    await _enqueue_agent(
        db_user_id=db_user.id,
        server_id=server_id,
        mode="support",
        goal=caption,
        photo_note=f"User sent a screenshot ({dest.name}). Use logs/SSH to diagnose; caption describes the issue.",
    )
    await message.answer(t("agent_started", lang))


@router.message(AgentSupport.waiting_problem, F.text)
async def support_text(message: Message, state: FSMContext, db_user) -> None:
    lang = db_user.lang or "en"
    data = await state.get_data()
    server_id = int(data.get("agent_server_id") or 0)
    goal = (message.text or "").strip()
    if len(goal) < 3:
        await message.answer("Describe the problem in more detail.")
        return
    await state.clear()
    await _enqueue_agent(
        db_user_id=db_user.id,
        server_id=server_id,
        mode="support",
        goal=goal,
    )
    await message.answer(t("agent_started", lang))


@router.message(HasPendingAgentAsk(), F.text)
async def agent_user_answer(message: Message, db_user, redis) -> None:
    lang = db_user.lang or "en"
    answer = (message.text or "").strip()
    if not answer:
        return
    raw = await redis.get(f"agent:wait:{db_user.id}")
    if not raw:
        return
    meta = json.loads(raw if isinstance(raw, str) else raw.decode())
    job_id = int(meta["job_id"])
    await redis.delete(f"agent:wait:{db_user.id}")

    factory = get_session_factory()
    async with factory() as session:
        job = await session.get(Job, job_id)
        if not job:
            await message.answer("Job gone")
            return
        payload = dict(job.payload or {})
        payload["user_answer"] = answer
        job.payload = payload
        job.status = "pending"
        from app.jobs.results import now_utc

        job.run_after = now_utc()
        await session.commit()
    await message.answer(t("agent_started", lang))


async def _enqueue_agent(
    *,
    db_user_id: int,
    server_id: int,
    mode: str,
    goal: str,
    local_file: str | None = None,
    photo_note: str | None = None,
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        server = await _require_server(session, server_id, db_user_id)
        if not server:
            return
        payload: dict = {
            "server_id": server_id,
            "mode": mode,
            "goal": goal,
        }
        if local_file:
            payload["local_file"] = local_file
        if photo_note:
            payload["photo_note"] = photo_note
        session.add(
            Job(
                kind="agent_run",
                class_="manage",
                payload=payload,
                status="pending",
                idem_key=f"agent-{mode}-{server_id}-{uuid.uuid4().hex[:10]}",
                server_id=server_id,
                max_attempts=24,
            )
        )
        await session.commit()
