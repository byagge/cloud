"""Agent job: analyze → plan → execute + deploy strategy over SSH + Gemini."""

from __future__ import annotations

import html
import json
import shlex
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import (
    AgentAnswer,
    AgentAskUser,
    AgentDone,
    AgentFail,
    AgentProposePlan,
    _pending_ask_user,
    deserialize_contents,
    open_ssh,
    resume_with_answer,
    resume_with_plan_ack,
    run_agent,
    serialize_contents,
)
from app.agent.gemini import GeminiAgent
from app.agent.progress import ProgressBoard
from app.config import get_settings
from app.core.secrets import get_secret
from app.db.models import Job, Server, User
from app.jobs.notify import enqueue_notify
from app.jobs.results import Done, Fail, Review, Wait
from app.logging import get_logger

log = get_logger("jobs.agent")

SESSION_TTL = 7200
LOCK_TTL = 7200
MAX_UPLOAD_BYTES = 40 * 1024 * 1024

_WAIT_HOLD = frozenset({"waiting_user", "waiting_user_rearm", "waiting_plan"})


def _esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def _phase_from_payload(payload: dict, mode: str) -> str:
    phase = str(payload.get("phase") or "").strip().lower()
    if phase in {"analyze", "execute", "deploy"}:
        return phase
    m = (mode or "").strip().lower()
    if m in {"support", "analyze"}:
        return "analyze"
    if m == "execute":
        return "execute"
    if m == "deploy":
        return "deploy"
    return "analyze"


async def _notify_fail(
    session: AsyncSession,
    *,
    user_id: int,
    job_id: int,
    lang: str,
    err: str,
    server_id: int | None = None,
    thread_id: int | None = None,
) -> None:
    from app.bot.texts import t

    payload: dict = {"server_id": server_id, "agent_job_id": job_id, "lang": lang, "show_stop": False}
    if thread_id:
        payload["message_thread_id"] = thread_id
    await enqueue_notify(
        session,
        user_id=user_id,
        key=f"agent_fail:{job_id}:{uuid.uuid4().hex[:8]}",
        ref=str(job_id),
        text=t("agent_fail", lang, err=_esc(err)),
        payload=payload,
        immediate=True,
    )


def _ui_payload(
    *,
    job_id: int,
    server_id: int,
    lang: str,
    thread_id: int | None,
    show_stop: bool,
    show_plan: bool = False,
) -> dict:
    p: dict = {
        "server_id": server_id,
        "agent_job_id": job_id,
        "lang": lang,
        "show_stop": show_stop,
    }
    if show_plan:
        p["show_plan"] = True
    if thread_id:
        p["message_thread_id"] = int(thread_id)
    return p


async def handle(session: AsyncSession, redis, partner, job: Job):
    payload = dict(job.payload or {})
    answer = payload.pop("user_answer", None)
    plan_accepted = bool(payload.pop("plan_accepted", False))
    dirty = False
    if "user_answer" in (job.payload or {}):
        dirty = True
    if "plan_accepted" in (job.payload or {}):
        dirty = True
    if dirty:
        job.payload = payload

    server_id = int(job.server_id or payload.get("server_id") or 0)
    server = await session.get(Server, server_id) if server_id else None
    from app.bot.texts import t

    if server is None or not server.ip:
        notify_uid = (server.user_id if server is not None else None) or payload.get("user_id")
        if notify_uid:
            user = await session.get(User, int(notify_uid))
            lang = (user.lang if user else None) or "en"
            if server is not None:
                msg = t("agent_no_ip", lang)
            else:
                msg = t("agent_fail", lang, err=_esc("server not found"))
            await enqueue_notify(
                session,
                user_id=int(notify_uid),
                key=f"agent_no_ip:{job.id}",
                ref=str(job.id),
                text=msg,
                payload={"server_id": server.id} if server is not None else None,
                immediate=True,
            )
        return Fail(reason="server_missing_or_no_ip")

    user = await session.get(User, server.user_id)
    lang = (user.lang if user else None) or "en"

    settings = get_settings()
    if not (settings.gemini_api_key or "").strip():
        await _notify_fail(
            session,
            user_id=server.user_id,
            job_id=job.id,
            lang=lang,
            err="GEMINI_API_KEY missing",
            server_id=server.id,
        )
        return Fail(reason="gemini_missing")

    secret = await get_secret(redis, f"cred:{server.id}")
    if not secret or not secret.get("password"):
        if server.partner_id:
            session.add(
                Job(
                    kind="reset_password",
                    class_="manage",
                    payload={"server_id": server.id},
                    status="pending",
                    idem_key=f"agent-nopw-reset-{server.id}-{job.id}",
                    server_id=server.id,
                )
            )
            await enqueue_notify(
                session,
                user_id=server.user_id,
                key=f"agent_nopw:{job.id}",
                ref=str(job.id),
                text=t("agent_nopw_reset", lang),
                payload={"server_id": server.id},
                immediate=True,
            )
        else:
            await enqueue_notify(
                session,
                user_id=server.user_id,
                key=f"agent_nopw:{job.id}",
                ref=str(job.id),
                text=t("agent_need_password", lang),
                payload={"server_id": server.id},
                immediate=True,
            )
        return Fail(reason="no_password")

    mode = payload.get("mode") or "support"
    goal = (payload.get("goal") or "").strip()
    session_key = f"agent:session:{job.id}"
    wait_key = f"agent:wait:{server.user_id}"
    lock_key = f"agent:lock:{server.id}"

    # Per-server lock — only one agent run at a time.
    # Re-entrant: same job_id may already hold the lock from a prior ask_user / plan Wait.
    got_lock = await redis.set(lock_key, str(job.id), nx=True, ex=LOCK_TTL)
    if not got_lock:
        cur = await redis.get(lock_key)
        if cur is not None and str(cur) == str(job.id):
            await redis.set(lock_key, str(job.id), ex=LOCK_TTL)
            got_lock = True
        else:
            await enqueue_notify(
                session,
                user_id=server.user_id,
                key=f"agent_locked:{job.id}",
                ref=str(job.id),
                text=t("agent_locked", lang),
                payload={"server_id": server.id},
                immediate=True,
            )
            return Wait(after=timedelta(minutes=2), reason="agent_locked")

    hold_lock = False
    try:
        result = await _run_locked(
            session,
            redis,
            job=job,
            server=server,
            user=user,
            secret=secret,
            payload=payload,
            answer=answer,
            plan_accepted=plan_accepted,
            mode=mode,
            goal=goal,
            session_key=session_key,
            wait_key=wait_key,
            lang=lang,
        )
        if isinstance(result, Wait) and result.reason in _WAIT_HOLD:
            await redis.set(lock_key, str(job.id), ex=LOCK_TTL)
            hold_lock = True
        return result
    finally:
        if not hold_lock:
            try:
                cur = await redis.get(lock_key)
                if cur is not None and str(cur) == str(job.id):
                    await redis.delete(lock_key)
            except Exception:
                log.exception("agent_lock_release_failed", server_id=server.id)


async def _run_locked(
    session: AsyncSession,
    redis,
    *,
    job: Job,
    server: Server,
    user: User | None,
    secret: dict,
    payload: dict,
    answer: str | None,
    plan_accepted: bool,
    mode: str,
    goal: str,
    session_key: str,
    wait_key: str,
    lang: str,
):
    from aiogram import Bot

    from app.bot.keyboards import agent_control_kb
    from app.bot.texts import t

    thread_id = payload.get("message_thread_id")
    try:
        thread_id = int(thread_id) if thread_id else None
    except Exception:
        thread_id = None

    cancel_key = f"agent:cancel:{job.id}"

    async def _cancelled() -> bool:
        return bool(await redis.get(cancel_key))

    if await _cancelled():
        await redis.delete(wait_key)
        await redis.delete(session_key)
        await redis.delete(cancel_key)
        await _notify_fail(
            session,
            user_id=server.user_id,
            job_id=job.id,
            lang=lang,
            err="stopped by user",
            server_id=server.id,
            thread_id=thread_id,
        )
        return Fail(reason="stopped_by_user")

    contents = None
    saved_state: dict = {}
    raw_state = await redis.get(session_key)
    if raw_state:
        try:
            saved_state = json.loads(raw_state if isinstance(raw_state, str) else raw_state.decode())
            contents = deserialize_contents(saved_state["contents_json"])
            if answer:
                contents = resume_with_answer(contents, str(answer))
            elif plan_accepted:
                contents = resume_with_plan_ack(contents, accepted=True)
            mode = saved_state.get("mode") or mode
            goal = saved_state.get("goal") or goal
        except Exception:
            log.exception("agent_session_load_failed", job_id=job.id)
            contents = None
            saved_state = {}

    phase = _phase_from_payload(payload, mode)
    if plan_accepted:
        phase = "execute"
        mode = "execute"
        payload["phase"] = "execute"
        payload["mode"] = "execute"
        job.payload = payload

    # still waiting for user / plan ack — don't burn Gemini calls
    if await redis.get(wait_key) and not answer and not plan_accepted:
        return Wait(after=timedelta(minutes=5), reason="waiting_user")

    # resume without session is fatal
    if (answer or plan_accepted) and contents is None:
        await redis.delete(wait_key)
        await _notify_fail(
            session,
            user_id=server.user_id,
            job_id=job.id,
            lang=lang,
            err="session expired — start the agent again",
            server_id=server.id,
            thread_id=thread_id,
        )
        return Fail(reason="session_expired")

    # session has unanswered ask_user but no answer yet (wait key expired)
    if contents is not None and not answer and not plan_accepted and _pending_ask_user(contents):
        await redis.set(
            wait_key,
            json.dumps({"job_id": job.id, "server_id": server.id, "question": "(pending)"}),
            ex=SESSION_TTL,
        )
        return Wait(after=timedelta(minutes=10), reason="waiting_user_rearm")

    # plan proposed earlier but wait key expired — rearm plan wait
    if (
        contents is not None
        and not answer
        and not plan_accepted
        and saved_state.get("plan_steps")
        and phase != "execute"
    ):
        await redis.set(
            wait_key,
            json.dumps(
                {
                    "job_id": job.id,
                    "server_id": server.id,
                    "type": "plan",
                    "question": saved_state.get("plan_diagnosis") or "(plan)",
                }
            ),
            ex=SESSION_TTL,
        )
        return Wait(after=timedelta(minutes=10), reason="waiting_plan")

    extra = ""
    local_file = payload.get("local_file")
    local_photo = payload.get("local_photo")
    remote_upload = payload.get("remote_upload")
    photo_note = payload.get("photo_note")
    if photo_note:
        extra += f"User attached a screenshot/photo note: {photo_note}\n"
    if remote_upload:
        extra += f"Uploaded archive on server: {remote_upload}\n"

    deploy_strategy = str(payload.get("deploy_strategy") or "").strip().lower()
    if deploy_strategy in {"overwrite", "alongside"}:
        extra += f"Deploy strategy chosen by user: {deploy_strategy}\n"

    if phase == "execute":
        steps = saved_state.get("plan_steps") or payload.get("plan_steps") or []
        diagnosis = saved_state.get("plan_diagnosis") or payload.get("plan_diagnosis") or ""
        if steps:
            extra += "APPROVED PLAN — execute these steps:\n"
            for i, s in enumerate(steps, 1):
                extra += f"{i}. {s}\n"
            if diagnosis:
                extra += f"Diagnosis: {diagnosis}\n"
            risk = saved_state.get("plan_risk") or payload.get("plan_risk") or ""
            if risk:
                extra += f"Risk: {risk}\n"

    extra += (
        "CONSTRAINT: Only patch/update EXISTING projects on this VPS. "
        "Do not create greenfield apps from scratch.\n"
    )

    login = secret.get("login") or server.login or "root"
    password = secret["password"]

    try:
        gemini = GeminiAgent(phase=phase)
    except Exception as e:
        await _notify_fail(
            session,
            user_id=server.user_id,
            job_id=job.id,
            lang=lang,
            err=str(e)[:200],
            server_id=server.id,
            thread_id=thread_id,
        )
        return Fail(reason=str(e)[:200])

    settings = get_settings()
    bot = None
    board: ProgressBoard | None = None
    ssh = None
    image_bytes: bytes | None = None
    result: AgentAskUser | AgentProposePlan | AgentAnswer | AgentDone | AgentFail | None = None
    try:
        try:
            try:
                ssh = await open_ssh(str(server.ip), str(login), str(password))
            except Exception as e:
                log.exception("ssh_connect_failed", server_id=server.id)
                await _notify_fail(
                    session,
                    user_id=server.user_id,
                    job_id=job.id,
                    lang=lang,
                    err=f"SSH failed: {str(e)[:180]}",
                    server_id=server.id,
                    thread_id=thread_id,
                )
                return Review(reason=f"ssh:{e}")

            if local_photo and Path(local_photo).is_file():
                try:
                    image_bytes = Path(local_photo).read_bytes()[:4_000_000]
                except Exception:
                    log.exception("read_photo_failed")

            # first run: upload file if any
            if local_file and not remote_upload and Path(local_file).is_file():
                path = Path(local_file)
                size = path.stat().st_size
                if size > MAX_UPLOAD_BYTES:
                    await _notify_fail(
                        session,
                        user_id=server.user_id,
                        job_id=job.id,
                        lang=lang,
                        err=f"upload too large ({size} bytes, max {MAX_UPLOAD_BYTES})",
                        server_id=server.id,
                        thread_id=thread_id,
                    )
                    return Fail(reason=f"upload_too_large:{size}")
                data = path.read_bytes()
                name = path.name
                remote_dir = f"/opt/arix-uploads/{server.id}"
                src_dir = f"{remote_dir}/src"
                remote = f"{remote_dir}/{name}"
                await ssh.run(
                    f"mkdir -p {shlex.quote(remote_dir)} /opt/arix-apps {shlex.quote(src_dir)}"
                )
                await ssh.write_bytes(remote, data)
                remote_upload = remote
                payload["remote_upload"] = remote
                job.payload = payload
                extra += f"Uploaded archive on server: {remote} ({size} bytes)\n"
                lower = name.lower()
                rq, sq = shlex.quote(remote), shlex.quote(src_dir)
                if lower.endswith(".zip"):
                    unz = await ssh.run(f"unzip -o {rq} -d {sq}")
                    if unz.exit_code != 0:
                        extra += (
                            f"unzip warning exit={unz.exit_code}: "
                            f"{(unz.stderr or unz.stdout)[:400]}\n"
                        )
                    else:
                        extra += f"Unzipped to {src_dir}\n"
                elif lower.endswith((".tar.gz", ".tgz")):
                    await ssh.run(f"tar -xzf {rq} -C {sq}")
                    extra += f"Extracted tar.gz to {src_dir}\n"
                elif lower.endswith(".tar"):
                    await ssh.run(f"tar -xf {rq} -C {sq}")
                    extra += f"Extracted tar to {src_dir}\n"

            if await _cancelled():
                await _notify_fail(
                    session,
                    user_id=server.user_id,
                    job_id=job.id,
                    lang=lang,
                    err="stopped by user",
                    server_id=server.id,
                    thread_id=thread_id,
                )
                return Fail(reason="stopped_by_user")

            tg_id = int(user.tg_id) if user and user.tg_id else 0
            if tg_id and (settings.bot_token or "").strip():
                bot = Bot(token=settings.bot_token)
                board = ProgressBoard(
                    bot=bot,
                    chat_id=tg_id,
                    lang=lang,
                    message_thread_id=thread_id,
                    reply_markup=agent_control_kb(
                        job_id=job.id,
                        server_id=server.id,
                        lang=lang,
                        show_stop=True,
                    ),
                )
                await board.ensure()

            result = await run_agent(
                ssh=ssh,
                gemini=gemini,
                mode=phase,
                user_goal=goal,
                extra_context=extra,
                image_bytes=image_bytes if contents is None else None,
                contents=contents,
                should_cancel=_cancelled,
                progress=board,
                lang=lang,
            )
        except Exception as e:
            log.exception("agent_run_failed", job_id=job.id)
            if board is not None:
                try:
                    await board.finish(str(e)[:200], ok=False)
                except Exception:
                    pass
            await _notify_fail(
                session,
                user_id=server.user_id,
                job_id=job.id,
                lang=lang,
                err=str(e)[:300],
                server_id=server.id,
                thread_id=thread_id,
            )
            return Review(reason=str(e)[:300])
        finally:
            if ssh is not None:
                try:
                    await ssh.close()
                except Exception:
                    pass

        return await _handle_agent_result(
            session,
            redis,
            job=job,
            server=server,
            result=result,
            board=board,
            mode=mode,
            goal=goal,
            phase=phase,
            session_key=session_key,
            wait_key=wait_key,
            cancel_key=cancel_key,
            saved_state=saved_state,
            lang=lang,
            thread_id=thread_id,
        )
    finally:
        if bot is not None:
            try:
                await bot.session.close()
            except Exception:
                pass


async def _handle_agent_result(
    session: AsyncSession,
    redis,
    *,
    job: Job,
    server: Server,
    result: AgentAskUser | AgentProposePlan | AgentAnswer | AgentDone | AgentFail | None,
    board: ProgressBoard | None,
    mode: str,
    goal: str,
    phase: str,
    session_key: str,
    wait_key: str,
    cancel_key: str,
    saved_state: dict,
    lang: str,
    thread_id: int | None,
):
    from app.bot.texts import t

    if result is None:
        await _notify_fail(
            session,
            user_id=server.user_id,
            job_id=job.id,
            lang=lang,
            err="no result",
            server_id=server.id,
            thread_id=thread_id,
        )
        return Fail(reason="no_result")

    if isinstance(result, AgentAskUser):
        if board is not None:
            try:
                await board.done_line("вопрос пользователю" if lang == "ru" else "asking user")
            except Exception:
                pass
        await redis.set(
            session_key,
            json.dumps(
                {
                    "contents_json": serialize_contents(result.contents),
                    "mode": mode,
                    "goal": goal,
                    "phase": phase,
                    "system": result.system,
                    "plan_steps": saved_state.get("plan_steps"),
                    "plan_diagnosis": saved_state.get("plan_diagnosis"),
                    "plan_risk": saved_state.get("plan_risk"),
                },
                ensure_ascii=False,
            ),
            ex=SESSION_TTL,
        )
        await redis.set(
            wait_key,
            json.dumps(
                {
                    "job_id": job.id,
                    "server_id": server.id,
                    "question": result.question,
                }
            ),
            ex=SESSION_TTL,
        )
        ask_token = uuid.uuid4().hex[:10]
        await enqueue_notify(
            session,
            user_id=server.user_id,
            key=f"agent_ask:{job.id}:{ask_token}",
            ref=f"{job.id}:{ask_token}",
            text=t("agent_ask_user", lang, question=_esc(result.question)),
            payload=_ui_payload(
                job_id=job.id,
                server_id=server.id,
                lang=lang,
                thread_id=thread_id,
                show_stop=True,
            ),
            immediate=True,
        )
        return Wait(after=timedelta(hours=2), reason="waiting_user")

    if isinstance(result, AgentProposePlan):
        if board is not None:
            try:
                await board.finish("план готов" if lang == "ru" else "plan ready")
            except Exception:
                pass
        await redis.set(
            session_key,
            json.dumps(
                {
                    "contents_json": serialize_contents(result.contents),
                    "mode": mode,
                    "goal": goal,
                    "phase": phase,
                    "system": result.system,
                    "plan_steps": result.steps,
                    "plan_diagnosis": result.diagnosis,
                    "plan_risk": result.risk,
                },
                ensure_ascii=False,
            ),
            ex=SESSION_TTL,
        )
        await redis.set(
            wait_key,
            json.dumps(
                {
                    "job_id": job.id,
                    "server_id": server.id,
                    "type": "plan",
                    "question": result.diagnosis,
                }
            ),
            ex=SESSION_TTL,
        )
        plan_token = uuid.uuid4().hex[:10]
        await enqueue_notify(
            session,
            user_id=server.user_id,
            key=f"agent_plan:{job.id}:{plan_token}",
            ref=f"{job.id}:{plan_token}",
            text=result.format_text(lang),
            payload=_ui_payload(
                job_id=job.id,
                server_id=server.id,
                lang=lang,
                thread_id=thread_id,
                show_stop=True,
                show_plan=True,
            ),
            immediate=True,
        )
        return Wait(after=timedelta(hours=2), reason="waiting_plan")

    await redis.delete(session_key)
    await redis.delete(wait_key)
    await redis.delete(cancel_key)

    if isinstance(result, AgentAnswer):
        summary = (result.answer or "—")[:200]
        if board is not None:
            try:
                await board.finish(summary)
            except Exception:
                pass
        sug = (result.suggestion or "").strip()
        text = t(
            "agent_answer",
            lang,
            answer=_esc(result.answer),
            suggestion=_esc(sug) if sug else ("—" if lang != "ru" else "—"),
        )
        await enqueue_notify(
            session,
            user_id=server.user_id,
            key=f"agent_answer:{job.id}:{uuid.uuid4().hex[:8]}",
            ref=str(job.id),
            text=text,
            payload=_ui_payload(
                job_id=job.id,
                server_id=server.id,
                lang=lang,
                thread_id=thread_id,
                show_stop=False,
            ),
            immediate=True,
        )
        return Done()

    if isinstance(result, AgentDone):
        if board is not None:
            try:
                await board.finish(result.summary[:200], ok=result.ok)
            except Exception:
                pass
        await enqueue_notify(
            session,
            user_id=server.user_id,
            key=f"agent_done:{job.id}:{uuid.uuid4().hex[:8]}",
            ref=str(job.id),
            text=t("agent_done", lang, summary=_esc(result.summary)),
            payload=_ui_payload(
                job_id=job.id,
                server_id=server.id,
                lang=lang,
                thread_id=thread_id,
                show_stop=False,
            ),
            immediate=True,
        )
        return Done()

    err = result.error if isinstance(result, AgentFail) else "unknown"
    if board is not None:
        try:
            await board.finish(err[:200], ok=False)
        except Exception:
            pass
    await enqueue_notify(
        session,
        user_id=server.user_id,
        key=f"agent_fail:{job.id}:{uuid.uuid4().hex[:8]}",
        ref=str(job.id),
        text=t("agent_fail", lang, err=_esc(err)),
        payload=_ui_payload(
            job_id=job.id,
            server_id=server.id,
            lang=lang,
            thread_id=thread_id,
            show_stop=False,
        ),
        immediate=True,
    )
    return Fail(reason=err[:200])
