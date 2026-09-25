"""Autodeploy + AI project support FSM (file/photo upload, agent Q&A, plan, stop)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.agent.topics import ensure_agent_topic
from app.bot.keyboards import AgentCB, SrvCB, agent_control_kb, deploy_strategy_kb
from app.bot.render import show_banner
from app.bot.texts import t
from app.bot.ui.screens import decorate
from app.config import get_settings
from app.core.secrets import get_secret
from app.db.models import Job, Server
from app.db.session import get_session_factory
from app.jobs.results import now_utc
from app.logging import get_logger

router = Router()
log = get_logger("bot.agent")

UPLOAD_ROOT = Path("data/uploads")
MAX_DOC_BYTES = 40 * 1024 * 1024
ALLOWED_SUFFIXES = {".zip", ".tar", ".gz", ".tgz", ".rar", ".7z", ".bz2"}


class AgentDeploy(StatesGroup):
    waiting_file = State()
    waiting_strategy = State()


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


async def _active_agent_job(session, server_id: int) -> Job | None:
    return await session.scalar(
        select(Job)
        .where(
            Job.server_id == server_id,
            Job.kind == "agent_run",
            Job.status.in_(("pending", "running")),
        )
        .limit(1)
    )


async def _guard_ready(query: CallbackQuery, db_user, redis, server_id: int) -> bool:
    lang = db_user.lang or "en"
    settings = get_settings()
    if not (settings.gemini_api_key or "").strip():
        await query.answer(t("agent_need_gemini", lang), show_alert=True)
        return False
    if await redis.get(f"agent:wait:{db_user.id}"):
        await query.answer(t("agent_busy", lang), show_alert=True)
        return False
    if await redis.get(f"agent:lock:{server_id}"):
        await query.answer(t("agent_already_running", lang), show_alert=True)
        return False
    factory = get_session_factory()
    async with factory() as session:
        server = await _require_server(session, server_id, db_user.id)
        if not server:
            await query.answer(t("not_found", lang), show_alert=True)
            return False
        if not server.ip:
            await query.answer(t("agent_no_ip", lang), show_alert=True)
            return False
        if await _active_agent_job(session, server_id):
            await query.answer(t("agent_already_running", lang), show_alert=True)
            return False
    secret = await get_secret(redis, f"cred:{server_id}")
    if not secret or not secret.get("password"):
        await query.answer(t("agent_need_password", lang), show_alert=True)
        return False
    return True


@router.callback_query(SrvCB.filter(F.action == "dep"))
async def deploy_start(query: CallbackQuery, callback_data: SrvCB, state: FSMContext, db_user, redis) -> None:
    lang = db_user.lang or "en"
    if not await _guard_ready(query, db_user, redis, callback_data.server_id):
        return
    await state.set_state(AgentDeploy.waiting_file)
    await state.update_data(agent_server_id=callback_data.server_id)
    text = decorate(t("agent_deploy_ask", lang))
    await show_banner(query, text, _back_kb(callback_data.server_id, lang), banner="servers", lang=lang)
    await query.answer()


@router.callback_query(SrvCB.filter(F.action == "ai"))
async def ai_start(query: CallbackQuery, callback_data: SrvCB, state: FSMContext, db_user, redis) -> None:
    lang = db_user.lang or "en"
    if not await _guard_ready(query, db_user, redis, callback_data.server_id):
        return
    await state.set_state(AgentSupport.waiting_problem)
    await state.update_data(agent_server_id=callback_data.server_id)
    text = decorate(t("agent_ai_ask", lang))
    await show_banner(query, text, _back_kb(callback_data.server_id, lang), banner="servers", lang=lang)
    await query.answer()


@router.callback_query(AgentCB.filter(F.action == "stop"))
async def agent_stop(query: CallbackQuery, callback_data: AgentCB, db_user, redis) -> None:
    lang = db_user.lang or "en"
    job_id = int(callback_data.job_id or 0)
    if not job_id:
        await query.answer("OK", show_alert=False)
        return
    factory = get_session_factory()
    async with factory() as session:
        job = await session.get(Job, job_id)
        if not job or not job.server_id:
            await query.answer(t("not_found", lang), show_alert=True)
            return
        server = await session.get(Server, job.server_id)
        if not server or server.user_id != db_user.id:
            await query.answer(t("not_found", lang), show_alert=True)
            return
        await redis.set(f"agent:cancel:{job_id}", "1", ex=7200)
        await redis.delete(f"agent:wait:{db_user.id}")
        # Wake job ASAP if waiting
        if job.status in {"pending", "running", "waiting"}:
            job.status = "pending"
            job.run_after = now_utc()
            payload = dict(job.payload or {})
            payload.pop("user_answer", None)
            payload.pop("plan_accepted", None)
            job.payload = payload
        await session.commit()
    await query.answer(t("agent_stopping", lang), show_alert=True)
    try:
        await query.message.answer(t("agent_stopping", lang))  # type: ignore[union-attr]
    except Exception:
        pass


@router.callback_query(AgentCB.filter(F.action == "plan_go"))
async def agent_plan_go(query: CallbackQuery, callback_data: AgentCB, db_user, redis) -> None:
    lang = db_user.lang or "en"
    job_id = int(callback_data.job_id or 0)
    if not job_id:
        await query.answer(t("not_found", lang), show_alert=True)
        return
    factory = get_session_factory()
    async with factory() as session:
        job = await session.get(Job, job_id)
        if not job or not job.server_id:
            await query.answer(t("not_found", lang), show_alert=True)
            return
        server = await session.get(Server, job.server_id)
        if not server or server.user_id != db_user.id:
            await query.answer(t("not_found", lang), show_alert=True)
            return
        payload = dict(job.payload or {})
        payload["phase"] = "execute"
        payload["mode"] = "execute"
        payload["plan_accepted"] = True
        payload.pop("user_answer", None)
        job.payload = payload
        job.status = "pending"
        job.run_after = now_utc()
        await session.commit()
    await redis.delete(f"agent:wait:{db_user.id}")
    text = t("agent_plan_accepted", lang)
    try:
        await query.message.edit_text(  # type: ignore[union-attr]
            text,
            parse_mode="HTML",
            reply_markup=agent_control_kb(
                job_id=job_id,
                server_id=callback_data.server_id,
                lang=lang,
                show_stop=True,
            ),
        )
    except Exception:
        try:
            await query.message.answer(  # type: ignore[union-attr]
                text,
                parse_mode="HTML",
                reply_markup=agent_control_kb(
                    job_id=job_id,
                    server_id=callback_data.server_id,
                    lang=lang,
                    show_stop=True,
                ),
            )
        except Exception:
            pass
    await query.answer()


@router.callback_query(AgentCB.filter(F.action == "plan_no"))
async def agent_plan_no(query: CallbackQuery, callback_data: AgentCB, db_user, redis) -> None:
    """Decline plan — same cancel path as Stop."""
    await agent_stop(query, callback_data, db_user, redis)


@router.callback_query(AgentCB.filter(F.action.in_({"dep_over", "dep_side"})))
async def deploy_strategy_pick(
    query: CallbackQuery, callback_data: AgentCB, state: FSMContext, db_user, bot, redis
) -> None:
    lang = db_user.lang or "en"
    data = await state.get_data()
    server_id = int(data.get("agent_server_id") or callback_data.server_id or 0)
    local_file = data.get("local_file")
    goal = (data.get("goal") or "").strip()
    if not server_id or not local_file:
        await state.clear()
        await query.answer(t("not_found", lang), show_alert=True)
        return
    strategy = "overwrite" if callback_data.action == "dep_over" else "alongside"
    await state.clear()
    thread_id = await ensure_agent_topic(
        bot,
        chat_id=query.message.chat.id,  # type: ignore[union-attr]
        server_id=server_id,
        title=f"Deploy #{server_id}",
        redis=redis,
    )
    result = await _enqueue_agent(
        db_user_id=db_user.id,
        server_id=server_id,
        mode="deploy",
        phase="deploy",
        goal=goal,
        local_file=str(local_file),
        deploy_strategy=strategy,
        message_thread_id=thread_id,
    )
    await query.answer()
    try:
        await _reply_started(query.message, lang, result, server_id)  # type: ignore[arg-type]
    except Exception:
        pass


@router.callback_query(AgentCB.filter(F.action == "open"))
async def agent_open_server(query: CallbackQuery, callback_data: AgentCB, db_user, redis, state: FSMContext) -> None:
    from app.bot.routers.servers import open_server

    await open_server(
        query,
        SrvCB(action="open", server_id=callback_data.server_id),
        db_user,
        redis,
        state,
    )


@router.message(AgentDeploy.waiting_file, F.document)
async def deploy_file(message: Message, state: FSMContext, db_user, bot, redis) -> None:
    lang = db_user.lang or "en"
    data = await state.get_data()
    server_id = int(data.get("agent_server_id") or 0)
    if not server_id:
        await state.clear()
        await message.answer(t("not_found", lang))
        return
    doc = message.document
    if not doc:
        await message.answer(t("agent_deploy_ask", lang))
        return
    if doc.file_size and doc.file_size > MAX_DOC_BYTES:
        await message.answer(t("agent_file_too_big", lang))
        return
    name = doc.file_name or f"upload-{uuid.uuid4().hex[:8]}"
    suffix = Path(name).suffix.lower()
    if name.lower().endswith(".tar.gz"):
        suffix = ".gz"
    if suffix and suffix not in ALLOWED_SUFFIXES and not name.lower().endswith(".tar.gz"):
        await message.answer(t("agent_file_bad_type", lang))
        return
    dest = _uploads_dir(db_user.id) / f"{uuid.uuid4().hex[:10]}_{name}"
    try:
        await bot.download(doc, destination=dest)
    except Exception:
        log.exception("download_failed")
        await message.answer(t("agent_fail", lang, err="download failed"))
        return
    if dest.stat().st_size > MAX_DOC_BYTES:
        dest.unlink(missing_ok=True)
        await message.answer(t("agent_file_too_big", lang))
        return
    caption = (message.caption or "").strip()
    goal = caption or (
        f"Update an EXISTING project on this VPS using archive {name}. "
        "Do not create a new app from scratch — match to /opt/arix-apps or ask which app."
    )
    await state.set_state(AgentDeploy.waiting_strategy)
    await state.update_data(
        agent_server_id=server_id,
        local_file=str(dest),
        goal=goal,
    )
    await message.answer(
        t("agent_deploy_strategy_ask", lang),
        parse_mode="HTML",
        reply_markup=deploy_strategy_kb(server_id=server_id, lang=lang),
    )


@router.message(AgentDeploy.waiting_file)
async def deploy_need_file(message: Message, db_user) -> None:
    await message.answer(t("agent_deploy_ask", db_user.lang or "en"))


@router.message(AgentDeploy.waiting_strategy)
async def deploy_need_strategy(message: Message, state: FSMContext, db_user) -> None:
    lang = db_user.lang or "en"
    data = await state.get_data()
    server_id = int(data.get("agent_server_id") or 0)
    await message.answer(
        t("agent_deploy_strategy_ask", lang),
        parse_mode="HTML",
        reply_markup=deploy_strategy_kb(server_id=server_id, lang=lang) if server_id else None,
    )


@router.message(AgentSupport.waiting_problem, F.photo)
async def support_photo(message: Message, state: FSMContext, db_user, bot, redis) -> None:
    lang = db_user.lang or "en"
    data = await state.get_data()
    server_id = int(data.get("agent_server_id") or 0)
    if not server_id:
        await state.clear()
        await message.answer(t("not_found", lang))
        return
    photo = message.photo[-1]
    dest = _uploads_dir(db_user.id) / f"{uuid.uuid4().hex[:10]}.jpg"
    try:
        await bot.download(photo, destination=dest)
    except Exception:
        log.exception("photo_download_failed")
        await message.answer(t("agent_fail", lang, err="download failed"))
        return
    caption = (message.caption or "").strip() or "See attached screenshot — something is broken."
    await state.clear()
    thread_id = await ensure_agent_topic(
        bot,
        chat_id=message.chat.id,
        server_id=server_id,
        title=f"AI #{server_id}",
        redis=redis,
    )
    result = await _enqueue_agent(
        db_user_id=db_user.id,
        server_id=server_id,
        mode="support",
        phase="analyze",
        goal=caption,
        local_photo=str(dest),
        photo_note=f"User sent a screenshot ({dest.name}). Diagnose from image + SSH logs.",
        message_thread_id=thread_id,
    )
    await _reply_started(message, lang, result, server_id)


@router.message(AgentSupport.waiting_problem, F.document)
async def support_doc(message: Message, state: FSMContext, db_user, bot, redis) -> None:
    lang = db_user.lang or "en"
    data = await state.get_data()
    server_id = int(data.get("agent_server_id") or 0)
    doc = message.document
    if not server_id or not doc:
        return
    if doc.file_size and doc.file_size > 2 * 1024 * 1024:
        await message.answer(t("agent_file_too_big", lang))
        return
    name = doc.file_name or "note.txt"
    dest = _uploads_dir(db_user.id) / f"{uuid.uuid4().hex[:10]}_{name}"
    await bot.download(doc, destination=dest)
    caption = (message.caption or "").strip()
    goal = caption or f"User attached file {name} describing the problem."
    note = f"Attached file saved locally as {dest.name} (not on VPS). Use SSH to inspect the project."
    local_photo = None
    if name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        local_photo = str(dest)
        note = f"User attached image file {name}."
    await state.clear()
    thread_id = await ensure_agent_topic(
        bot,
        chat_id=message.chat.id,
        server_id=server_id,
        title=f"AI #{server_id}",
        redis=redis,
    )
    result = await _enqueue_agent(
        db_user_id=db_user.id,
        server_id=server_id,
        mode="support",
        phase="analyze",
        goal=goal,
        local_photo=local_photo,
        photo_note=note,
        message_thread_id=thread_id,
    )
    await _reply_started(message, lang, result, server_id)


@router.message(AgentSupport.waiting_problem, F.text)
async def support_text(message: Message, state: FSMContext, db_user, bot, redis) -> None:
    lang = db_user.lang or "en"
    data = await state.get_data()
    server_id = int(data.get("agent_server_id") or 0)
    goal = (message.text or "").strip()
    if len(goal) < 3:
        await message.answer(t("agent_need_detail", lang))
        return
    if not server_id:
        await state.clear()
        await message.answer(t("not_found", lang))
        return
    await state.clear()
    thread_id = await ensure_agent_topic(
        bot,
        chat_id=message.chat.id,
        server_id=server_id,
        title=f"AI #{server_id}",
        redis=redis,
    )
    result = await _enqueue_agent(
        db_user_id=db_user.id,
        server_id=server_id,
        mode="support",
        phase="analyze",
        goal=goal,
        message_thread_id=thread_id,
    )
    await _reply_started(message, lang, result, server_id)


@router.message(HasPendingAgentAsk(), F.text)
async def agent_user_answer(message: Message, db_user, redis) -> None:
    """Accept answers even when wait meta.question is '(pending)' (rearmed wait).
    Plan waits must use buttons — ignore free text.
    """
    lang = db_user.lang or "en"
    answer = (message.text or "").strip()
    if not answer:
        return
    raw = await redis.get(f"agent:wait:{db_user.id}")
    if not raw:
        return
    try:
        meta = json.loads(raw if isinstance(raw, str) else raw.decode())
        job_id = int(meta["job_id"])
        server_id = int(meta.get("server_id") or 0)
        wait_type = meta.get("type") or ""
    except Exception:
        await redis.delete(f"agent:wait:{db_user.id}")
        await message.answer(t("agent_fail", lang, err="bad wait state"))
        return

    if wait_type == "plan":
        await message.answer(t("agent_plan_use_buttons", lang))
        return

    factory = get_session_factory()
    async with factory() as session:
        job = await session.get(Job, job_id)
        if not job:
            await redis.delete(f"agent:wait:{db_user.id}")
            await message.answer(t("agent_fail", lang, err="job gone"))
            return
        payload = dict(job.payload or {})
        payload["user_answer"] = answer[:4000]
        job.payload = payload
        job.status = "pending"
        job.run_after = now_utc()
        await session.commit()
    await redis.delete(f"agent:wait:{db_user.id}")
    await message.answer(
        t("agent_got_answer", lang),
        reply_markup=agent_control_kb(job_id=job_id, server_id=server_id, lang=lang, show_stop=True),
    )


async def _reply_started(message: Message, lang: str, result, server_id: int) -> None:
    if result == "busy":
        await message.answer(t("agent_already_running", lang))
        return
    if not result:
        await message.answer(t("not_found", lang))
        return
    job_id = int(result)
    text = t("agent_started", lang)
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=agent_control_kb(job_id=job_id, server_id=server_id, lang=lang, show_stop=True),
    )


async def _enqueue_agent(
    *,
    db_user_id: int,
    server_id: int,
    mode: str,
    goal: str,
    local_file: str | None = None,
    local_photo: str | None = None,
    photo_note: str | None = None,
    message_thread_id: int | None = None,
    deploy_strategy: str | None = None,
    phase: str | None = None,
) -> int | bool | str:
    """Enqueue agent_run. Returns job_id, False (not found), or 'busy'."""
    factory = get_session_factory()
    async with factory() as session:
        server = await _require_server(session, server_id, db_user_id)
        if not server:
            return False
        if await _active_agent_job(session, server_id):
            return "busy"
        resolved_phase = phase
        if not resolved_phase:
            if mode == "deploy":
                resolved_phase = "deploy"
            elif mode == "execute":
                resolved_phase = "execute"
            else:
                resolved_phase = "analyze"
        payload: dict = {
            "server_id": server_id,
            "user_id": db_user_id,
            "mode": mode,
            "phase": resolved_phase,
            "goal": goal,
        }
        if local_file:
            payload["local_file"] = local_file
        if local_photo:
            payload["local_photo"] = local_photo
        if photo_note:
            payload["photo_note"] = photo_note
        if message_thread_id:
            payload["message_thread_id"] = int(message_thread_id)
        if deploy_strategy in {"overwrite", "alongside"}:
            payload["deploy_strategy"] = deploy_strategy
        job = Job(
            kind="agent_run",
            class_="manage",
            payload=payload,
            status="pending",
            idem_key=f"agent-{mode}-{server_id}-{uuid.uuid4().hex[:10]}",
            server_id=server_id,
            max_attempts=24,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return int(job.id)
