"""Agent job: autodeploy + AI project support over SSH + Gemini."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import (
    AgentAskUser,
    AgentDone,
    AgentFail,
    deserialize_contents,
    open_ssh,
    resume_with_answer,
    run_agent,
    serialize_contents,
)
from app.agent.gemini import GeminiAgent
from app.config import get_settings
from app.core.secrets import get_secret
from app.db.models import Job, Server, User
from app.jobs.notify import enqueue_notify
from app.jobs.results import Done, Fail, Review, Wait
from app.logging import get_logger

log = get_logger("jobs.agent")

SESSION_TTL = 3600


async def handle(session: AsyncSession, redis, partner, job: Job):
    payload = dict(job.payload or {})
    answer = payload.pop("user_answer", None)
    if answer is not None:
        job.payload = payload
    server_id = int(job.server_id or payload.get("server_id") or 0)
    server = await session.get(Server, server_id)
    if server is None or not server.ip:
        return Fail(reason="server_missing_or_no_ip")

    settings = get_settings()
    if not (settings.gemini_api_key or "").strip():
        await enqueue_notify(
            session,
            user_id=server.user_id,
            key=f"agent_fail:{job.id}",
            ref=str(job.id),
            text="GEMINI_API_KEY missing",
            payload={"server_id": server.id},
        )
        return Fail(reason="gemini_missing")

    secret = await get_secret(redis, f"cred:{server.id}")
    if not secret or not secret.get("password"):
        await enqueue_notify(
            session,
            user_id=server.user_id,
            key=f"agent_nopw:{job.id}",
            ref=str(job.id),
            text="No SSH password — reset password in bot first",
            payload={"server_id": server.id},
        )
        return Fail(reason="no_password")

    mode = payload.get("mode") or "support"
    goal = payload.get("goal") or ""
    session_key = f"agent:session:{job.id}"

    # resume contents
    contents = None
    raw_state = await redis.get(session_key)
    if raw_state:
        try:
            state = json.loads(raw_state if isinstance(raw_state, str) else raw_state.decode())
            contents = deserialize_contents(state["contents_json"])
            if answer:
                contents = resume_with_answer(contents, str(answer))
            mode = state.get("mode") or mode
            goal = state.get("goal") or goal
        except Exception:
            log.exception("agent_session_load_failed", job_id=job.id)
            contents = None

    # still waiting for user reply — don't burn Gemini calls
    wait_key = f"agent:wait:{server.user_id}"
    if await redis.get(wait_key) and not answer and contents is not None:
        return Wait(after=timedelta(minutes=3), reason="waiting_user")

    extra = ""
    local_file = payload.get("local_file")
    remote_upload = payload.get("remote_upload")
    photo_note = payload.get("photo_note")
    if photo_note:
        extra += f"User attached a screenshot/photo note: {photo_note}\n"
    if remote_upload:
        extra += f"Uploaded archive on server: {remote_upload}\n"

    login = secret.get("login") or server.login or "root"
    password = secret["password"]

    try:
        gemini = GeminiAgent()
    except Exception as e:
        return Fail(reason=str(e)[:200])

    try:
        ssh = await open_ssh(str(server.ip), str(login), str(password))
    except Exception as e:
        log.exception("ssh_connect_failed", server_id=server.id)
        return Review(reason=f"ssh:{e}")

    try:
        # first run: upload file if any
        if local_file and not remote_upload and Path(local_file).is_file():
            data = Path(local_file).read_bytes()
            name = Path(local_file).name
            remote = f"/opt/arix-uploads/{server.id}/{name}"
            await ssh.run(f"mkdir -p /opt/arix-uploads/{server.id} /opt/arix-apps")
            await ssh.write_bytes(remote, data)
            remote_upload = remote
            payload["remote_upload"] = remote
            job.payload = payload
            extra += f"Uploaded archive on server: {remote}\n"
            # unpack hint
            if name.lower().endswith(".zip"):
                await ssh.run(
                    f"mkdir -p /opt/arix-uploads/{server.id}/src && "
                    f"unzip -o {remote} -d /opt/arix-uploads/{server.id}/src"
                )
                extra += f"Unzipped to /opt/arix-uploads/{server.id}/src\n"

        result = await run_agent(
            ssh=ssh,
            gemini=gemini,
            mode=mode,
            user_goal=goal,
            extra_context=extra,
            contents=contents,
        )
    except Exception as e:
        log.exception("agent_run_failed", job_id=job.id)
        await ssh.close()
        return Review(reason=str(e)[:300])
    finally:
        try:
            await ssh.close()
        except Exception:
            pass

    if isinstance(result, AgentAskUser):
        await redis.set(
            session_key,
            json.dumps(
                {
                    "contents_json": serialize_contents(result.contents),
                    "mode": mode,
                    "goal": goal,
                    "system": result.system,
                },
                ensure_ascii=False,
            ),
            ex=SESSION_TTL,
        )
        # mark waiting — bot will collect answer
        await redis.set(
            f"agent:wait:{server.user_id}",
            json.dumps({"job_id": job.id, "server_id": server.id, "question": result.question}),
            ex=SESSION_TTL,
        )
        user = await session.get(User, server.user_id)
        lang = (user.lang if user else None) or "en"
        from app.bot.texts import t

        await enqueue_notify(
            session,
            user_id=server.user_id,
            key=f"agent_ask:{job.id}:{job.attempts}",
            ref=f"{job.id}:{job.attempts}",
            text=t("agent_ask_user", lang, question=result.question),
        )
        return Wait(after=timedelta(hours=2), reason="waiting_user")

    await redis.delete(session_key)
    await redis.delete(f"agent:wait:{server.user_id}")

    user = await session.get(User, server.user_id)
    lang = (user.lang if user else None) or "en"
    from app.bot.texts import t

    if isinstance(result, AgentDone):
        await enqueue_notify(
            session,
            user_id=server.user_id,
            key=f"agent_done:{job.id}",
            ref=str(job.id),
            text=t("agent_done", lang, summary=result.summary),
        )
        return Done()

    err = result.error if isinstance(result, AgentFail) else "unknown"
    await enqueue_notify(
        session,
        user_id=server.user_id,
        key=f"agent_fail:{job.id}",
        ref=str(job.id),
        text=t("agent_fail", lang, err=err),
    )
    return Fail(reason=err[:200])
