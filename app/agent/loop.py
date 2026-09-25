"""Gemini tool-calling agent loop (Cursor-like)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.agent.prompts import ANALYZE_SYSTEM, DEPLOY_SYSTEM, EXECUTE_SYSTEM
from app.agent.ssh import SshSession
from app.agent.tools_def import max_steps_for_phase
from app.logging import get_logger

if TYPE_CHECKING:
    from app.agent.llm import AgentLLM
    from app.agent.progress import ProgressBoard

log = get_logger("agent.loop")

# Tool results stay in history every subsequent turn — keep tight but readable.
MAX_TOOL_JSON = 3_600
MAX_FIELD_CHARS = 900
MAX_CONTENTS_CHARS = 60_000  # Redis / resume dump
MAX_LIVE_CHARS = 42_000  # Compact mid-loop when over this
KEEP_TAIL_TURNS = 6
READ_FILE_MAX_BYTES = 18_000
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_MULTI_NL_RE = re.compile(r"\n{3,}")
_WRITE_CONTENT_KEEP = 240

# (pattern, reason) — matched against the full command string
_DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\brm\s+(?:-[^\s]*\s+)*-(?:[a-zA-Z]*f[a-zA-Z]*|rf|fr)\b[^;&|]*?(?:/(?:\s|$)|/\*|/\.\b)",
            re.I,
        ),
        "rm -rf / (or root wipe)",
    ),
    (re.compile(r"\brm\b[^;&|]*--no-preserve-root", re.I), "rm --no-preserve-root"),
    (re.compile(r"\bmkfs(?:\.\w+)?\b", re.I), "mkfs"),
    (re.compile(r"\bdd\s+[^;&|]*\bif=", re.I), "dd if="),
    (re.compile(r"\bwipefs\b", re.I), "wipefs"),
    (re.compile(r"\bshred\b[^;&|]*(/dev/|-\s*/\s|$)", re.I), "shred disk/root"),
    (
        re.compile(r"(?:>|tee\b[^;&|]*)\s*/dev/sd[a-z]\d*", re.I),
        "overwrite block device",
    ),
    (re.compile(r"\bpasswd\s+root\b", re.I), "passwd root"),
    (re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;?\s*:", re.I), "fork bomb"),
    (re.compile(r"\b(?:sgdisk|sfdisk)\b[^;&|]*--zap", re.I), "disk zap"),
]


def _is_dangerous_command(cmd: str) -> str | None:
    """Return a short reason if the shell command must be blocked, else None."""
    text = (cmd or "").strip()
    if not text:
        return None
    for pat, reason in _DANGEROUS_PATTERNS:
        if pat.search(text):
            return reason
    return None


def _pending_ask_user(contents: list[Any]) -> bool:
    """True if an ask_user function_call has no later matching function_response."""
    pending = False
    for c in contents:
        for p in getattr(c, "parts", None) or []:
            fc = getattr(p, "function_call", None)
            if fc and getattr(fc, "name", None) == "ask_user":
                pending = True
            fr = getattr(p, "function_response", None)
            if fr and getattr(fr, "name", None) == "ask_user":
                pending = False
    return pending


@dataclass
class AgentAskUser:
    question: str
    contents: list[Any]
    system: str


@dataclass
class AgentProposePlan:
    diagnosis: str
    steps: list[str]
    risk: str
    contents: list[Any]
    system: str

    def format_text(self, lang: str = "en") -> str:
        import html as _html

        def e(s: str) -> str:
            return _html.escape(s or "", quote=False)

        steps = "\n".join(f"{i}. {e(s)}" for i, s in enumerate(self.steps, 1))
        risk = e(self.risk)
        diag = e(self.diagnosis)
        if lang == "ru":
            return (
                f"📋 <b>План работ</b> (риск: {risk})\n\n"
                f"<b>Диагноз:</b>\n{diag}\n\n"
                f"<b>Шаги:</b>\n{steps}\n\n"
                "Нажмите «Работать по плану», чтобы начать."
            )
        return (
            f"📋 <b>Work plan</b> (risk: {risk})\n\n"
            f"<b>Diagnosis:</b>\n{diag}\n\n"
            f"<b>Steps:</b>\n{steps}\n\n"
            "Press “Work on plan” to start."
        )


@dataclass
class AgentAnswer:
    answer: str
    suggestion: str = ""


@dataclass
class AgentDone:
    summary: str
    ok: bool = True


@dataclass
class AgentFail:
    error: str


@dataclass
class AgentState:
    contents: list[Any] = field(default_factory=list)
    system: str = ""
    step: int = 0


def _args_dict(args: Any) -> dict[str, Any]:
    if args is None:
        return {}
    if isinstance(args, dict):
        return dict(args)
    try:
        return dict(args)
    except Exception:
        try:
            return json.loads(json.dumps(args, default=str))
        except Exception:
            return {}


def _extract_calls(response: Any) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    try:
        # Prefer SDK helper when present
        fcs = getattr(response, "function_calls", None)
        if fcs:
            for fc in fcs:
                name = getattr(fc, "name", None)
                if name:
                    calls.append((str(name), _args_dict(getattr(fc, "args", None))))
            if calls:
                return calls
        for cand in response.candidates or []:
            parts = getattr(cand.content, "parts", None) or []
            for part in parts:
                fc = getattr(part, "function_call", None)
                if fc and getattr(fc, "name", None):
                    calls.append((str(fc.name), _args_dict(getattr(fc, "args", None))))
    except Exception:
        log.exception("extract_calls_failed")
    return calls


def _text_from_response(response: Any) -> str:
    try:
        t = getattr(response, "text", None)
        if t:
            return str(t)[:2000]
    except Exception:
        pass
    try:
        for cand in response.candidates or []:
            parts = getattr(cand.content, "parts", None) or []
            bits = []
            for part in parts:
                if getattr(part, "text", None):
                    bits.append(str(part.text))
            if bits:
                return "".join(bits)[:2000]
    except Exception:
        pass
    return ""


def _clean_text(text: str) -> str:
    """Strip noise that burns tokens without aiding diagnosis."""
    if not text:
        return text
    text = _ANSI_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _MULTI_NL_RE.sub("\n\n", text)
    return text.strip()


def _head_tail(text: str, limit: int = MAX_FIELD_CHARS) -> str:
    text = _clean_text(text)
    if len(text) <= limit:
        return text
    head = max(200, limit * 2 // 3)
    tail = max(100, limit - head - 20)
    return f"{text[:head]}\n…[{len(text) - head - tail} chars truncated]…\n{text[-tail:]}"


def _clip_result(result: dict[str, Any]) -> dict[str, Any]:
    out = dict(result)
    for key in ("stdout", "stderr", "content"):
        if key in out and isinstance(out[key], str):
            # read_file content can be slightly larger than shell dumps
            lim = MAX_FIELD_CHARS + 300 if key == "content" else MAX_FIELD_CHARS
            out[key] = _head_tail(out[key], lim)
    raw = json.dumps(out, ensure_ascii=False, default=str)
    if len(raw) <= MAX_TOOL_JSON:
        return out
    for key in ("stdout", "stderr", "content"):
        if key in out and isinstance(out[key], str):
            out[key] = _head_tail(out[key], 350)
    raw2 = json.dumps(out, ensure_ascii=False, default=str)
    if len(raw2) > MAX_TOOL_JSON:
        return {"error": "tool result too large", "preview": raw2[:1200]}
    return out


def _slim_write_file_in_contents(contents: list[Any]) -> None:
    """write_file args.content is huge — keep only a short preview in history."""
    from google.genai import types

    if not contents:
        return
    last = contents[-1]
    if getattr(last, "role", None) != "model":
        return
    parts_out: list[Any] = []
    changed = False
    for p in getattr(last, "parts", None) or []:
        fc = getattr(p, "function_call", None)
        if fc and getattr(fc, "name", None) == "write_file":
            args = _args_dict(getattr(fc, "args", None))
            content = str(args.get("content") or "")
            if len(content) > _WRITE_CONTENT_KEEP:
                args["content"] = content[:_WRITE_CONTENT_KEEP] + f"\n…[{len(content)} bytes written]"
                parts_out.append(
                    types.Part.from_function_call(name="write_file", args=args)
                )
                changed = True
                continue
        # Drop long assistant prose when tools are present (saves tokens, tools carry intent)
        if getattr(p, "text", None) and len(str(p.text)) > 400:
            # keep short note only if no function_call siblings — handled below
            parts_out.append(types.Part.from_text(text=_head_tail(str(p.text), 280)))
            changed = True
            continue
        parts_out.append(p)
    if changed and parts_out:
        # If any function_call exists, drop text parts entirely (noise)
        has_fc = any(getattr(p, "function_call", None) for p in parts_out)
        if has_fc:
            parts_out = [p for p in parts_out if getattr(p, "function_call", None) or getattr(p, "function_response", None)]
        contents[-1] = types.Content(role="model", parts=parts_out)


def _contents_chars(contents: list[Any]) -> int:
    n = 0
    for c in contents:
        for p in getattr(c, "parts", None) or []:
            t = getattr(p, "text", None)
            if t:
                n += len(t)
            fc = getattr(p, "function_call", None)
            if fc:
                n += len(str(getattr(fc, "name", "") or ""))
                n += len(json.dumps(_args_dict(getattr(fc, "args", None)), ensure_ascii=False, default=str))
            fr = getattr(p, "function_response", None)
            if fr:
                resp = getattr(fr, "response", None)
                n += len(json.dumps(resp if isinstance(resp, dict) else {"v": str(resp)}, ensure_ascii=False, default=str))
    return n


def compact_contents(contents: list[Any], *, max_chars: int = MAX_LIVE_CHARS, keep_tail: int = KEEP_TAIL_TURNS) -> list[Any]:
    """Drop middle turns when history is too large (keep first user + recent tail)."""
    if len(contents) <= keep_tail + 1:
        if _contents_chars(contents) <= max_chars:
            return contents
    if _contents_chars(contents) <= max_chars and len(contents) <= keep_tail + 2:
        return contents

    head = contents[:1]
    tail = contents[-keep_tail:] if len(contents) > keep_tail else contents[1:]
    # Avoid duplicating if head is inside tail
    if head and tail and head[0] is tail[0]:
        merged = list(tail)
    else:
        merged = list(head) + list(tail)

    # If still huge, shrink tool responses in older tail entries (keep last 2 full)
    if _contents_chars(merged) > max_chars and len(merged) > 3:
        from google.genai import types

        slimmed: list[Any] = [merged[0]]
        for i, c in enumerate(merged[1:]):
            near_end = i >= len(merged) - 3
            if near_end:
                slimmed.append(c)
                continue
            parts_out = []
            for p in getattr(c, "parts", None) or []:
                fr = getattr(p, "function_response", None)
                if fr:
                    resp = getattr(fr, "response", None)
                    if isinstance(resp, dict):
                        resp = _clip_result(resp)
                        # Extra-aggressive for old turns
                        for k in ("stdout", "stderr", "content"):
                            if k in resp and isinstance(resp[k], str):
                                resp[k] = _head_tail(resp[k], 300)
                    parts_out.append(
                        types.Part.from_function_response(
                            name=getattr(fr, "name", "tool") or "tool",
                            response=resp if isinstance(resp, dict) else {"v": str(resp)[:300]},
                        )
                    )
                elif getattr(p, "text", None):
                    parts_out.append(types.Part.from_text(text=_head_tail(str(p.text), 500)))
                elif getattr(p, "function_call", None):
                    parts_out.append(p)
            if parts_out:
                slimmed.append(types.Content(role=getattr(c, "role", "user") or "user", parts=parts_out))
        merged = slimmed

    log.info(
        "contents_compacted",
        before=len(contents),
        after=len(merged),
        chars=_contents_chars(merged),
    )
    return merged


def compact_for_execute(
    contents: list[Any],
    *,
    goal: str,
    diagnosis: str,
    steps: list[str],
    risk: str = "low",
) -> list[Any]:
    """Drop analyze exploration; keep goal + approved plan only."""
    from google.genai import types

    goal_text = (goal or "").strip()
    if not goal_text and contents:
        for p in getattr(contents[0], "parts", None) or []:
            if getattr(p, "text", None):
                goal_text = str(p.text).split("\n\n---\n", 1)[0].strip()
                break
    goal_text = goal_text or "Execute the approved plan."
    steps_txt = "\n".join(f"{i}. {s}" for i, s in enumerate(steps or [], 1)) or "1. Apply minimal fix"
    plan = (
        f"APPROVED PLAN (risk: {risk or 'low'})\n"
        f"Diagnosis: {(diagnosis or '—')[:800]}\n"
        f"Steps:\n{steps_txt[:2000]}\n"
        "Execute these steps now. Do not re-explore from scratch."
    )
    return [
        types.Content(role="user", parts=[types.Part.from_text(text=goal_text[:2000])]),
        types.Content(role="user", parts=[types.Part.from_text(text=plan)]),
    ]


async def _exec_tool(ssh: SshSession, name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "run_shell":
        cmd = str(args.get("command") or "").strip()
        if not cmd:
            return {"error": "empty command"}
        blocked = _is_dangerous_command(cmd)
        if blocked:
            return {
                "error": f"blocked dangerous command: {blocked}",
                "exit_code": 126,
            }
        timeout = float(args.get("timeout_sec") or 120)
        timeout = max(5.0, min(timeout, 600.0))
        res = await ssh.run(cmd, timeout=timeout)
        return {
            "exit_code": res.exit_code,
            "stdout": res.stdout,
            "stderr": res.stderr,
        }
    if name == "write_file":
        path = str(args.get("path") or "").strip()
        if not path:
            return {"error": "empty path"}
        content = str(args.get("content") or "")
        if len(content.encode("utf-8")) > 1_500_000:
            return {"error": "content too large (max ~1.5MB)"}
        await ssh.write_text(path, content)
        return {"ok": True, "path": path, "bytes": len(content.encode("utf-8"))}
    if name == "read_file":
        path = str(args.get("path") or "").strip()
        if not path:
            return {"error": "empty path"}
        text = await ssh.read_text(path, max_bytes=READ_FILE_MAX_BYTES)
        return {"path": path, "content": text}
    if name == "backup_path":
        path = str(args.get("path") or "").strip()
        if not path:
            return {"error": "empty path"}
        dest = await ssh.backup(path)
        return {"ok": True, "backup": dest}
    return {"error": f"unknown tool {name}"}


def _append_model_content(contents: list[Any], response: Any, types: Any) -> None:
    try:
        cand = (response.candidates or [None])[0]
        if cand is not None and getattr(cand, "content", None) is not None:
            contents.append(cand.content)
            return
    except Exception:
        pass
    # OpenAI / Anthropic shim: rebuild Gemini-style Content from text + function_calls
    parts: list[Any] = []
    txt = _text_from_response(response)
    if txt:
        parts.append(types.Part.from_text(text=txt))
    try:
        for fc in getattr(response, "function_calls", None) or []:
            name = getattr(fc, "name", None)
            if not name:
                continue
            parts.append(
                types.Part.from_function_call(
                    name=str(name),
                    args=_args_dict(getattr(fc, "args", None)),
                )
            )
    except Exception:
        log.exception("append_fc_failed")
    if parts:
        contents.append(types.Content(role="model", parts=parts))


def _tool_label(name: str, args: dict[str, Any], lang: str) -> str:
    if name == "run_shell":
        cmd = str(args.get("command") or "")[:80]
        return f"shell: {cmd}" if lang != "ru" else f"команда: {cmd}"
    if name == "read_file":
        return f"read {args.get('path')}" if lang != "ru" else f"читаю {args.get('path')}"
    if name == "write_file":
        return f"write {args.get('path')}" if lang != "ru" else f"пишу {args.get('path')}"
    if name == "backup_path":
        return f"backup {args.get('path')}" if lang != "ru" else f"бэкап {args.get('path')}"
    return name


def _system_for_phase(phase: str) -> str:
    if phase == "deploy":
        return DEPLOY_SYSTEM
    if phase == "execute":
        return EXECUTE_SYSTEM
    return ANALYZE_SYSTEM


def _log_usage(response: Any, *, step: int, phase: str) -> None:
    """Best-effort token usage log (provider-dependent)."""
    try:
        meta = getattr(response, "usage_metadata", None) or getattr(response, "usage", None)
        if meta is None:
            return
        prompt_t = (
            getattr(meta, "prompt_token_count", None)
            or getattr(meta, "input_tokens", None)
            or getattr(meta, "prompt_tokens", None)
        )
        out_t = (
            getattr(meta, "candidates_token_count", None)
            or getattr(meta, "output_tokens", None)
            or getattr(meta, "completion_tokens", None)
        )
        total = getattr(meta, "total_token_count", None) or getattr(meta, "total_tokens", None)
        if prompt_t or out_t or total:
            log.info(
                "llm_usage",
                phase=phase,
                step=step,
                prompt_tokens=prompt_t,
                output_tokens=out_t,
                total_tokens=total,
            )
    except Exception:
        pass


async def run_agent(
    *,
    ssh: SshSession,
    gemini: "AgentLLM",
    mode: str,
    user_goal: str,
    extra_context: str = "",
    image_bytes: bytes | None = None,
    image_mime: str = "image/jpeg",
    contents: list[Any] | None = None,
    max_steps: int | None = None,
    should_cancel: Any | None = None,
    progress: "ProgressBoard | None" = None,
    lang: str = "en",
    on_llm_usage: Any | None = None,
) -> AgentAskUser | AgentProposePlan | AgentAnswer | AgentDone | AgentFail:
    phase = mode if mode in {"analyze", "execute", "deploy"} else (
        "deploy" if mode == "deploy" else "analyze"
    )
    # mode aliases: support → analyze
    if mode in {"support", "analyze"}:
        phase = "analyze"
    elif mode == "execute":
        phase = "execute"
    elif mode == "deploy":
        phase = "deploy"

    if max_steps is None:
        max_steps = max_steps_for_phase(phase)

    system = _system_for_phase(phase)
    from google.genai import types

    if contents is None:
        prompt = (user_goal or "").strip() or "Help with this VPS."
        if extra_context:
            prompt = f"{prompt}\n\n---\nContext:\n{extra_context}"
        parts: list[Any] = [types.Part.from_text(text=prompt)]
        if image_bytes:
            try:
                # Cap image payload (~1.2MB) — photos are expensive in tokens
                parts.append(
                    types.Part.from_bytes(data=image_bytes[:800_000], mime_type=image_mime)
                )
            except Exception:
                log.exception("attach_image_failed")
        contents = [types.Content(role="user", parts=parts)]
    else:
        contents = compact_contents(contents)

    if progress:
        title = {
            "analyze": "🔍 Анализ проекта" if lang == "ru" else "🔍 Analyzing project",
            "execute": "⚙️ Работаю по плану" if lang == "ru" else "⚙️ Working on plan",
            "deploy": "📦 Деплой" if lang == "ru" else "📦 Deploy",
        }.get(phase, "🛠 AI")
        await progress.set_title(title)
        await progress.set_current("думаю…" if lang == "ru" else "thinking…")

    for step in range(max_steps):
        if should_cancel is not None:
            try:
                if await should_cancel():
                    return AgentFail(error="stopped by user")
            except Exception:
                log.exception("cancel_check_failed")

        if progress:
            await progress.set_current("думаю…" if lang == "ru" else "thinking…")

        if step > 0 and step % 2 == 0:
            contents = compact_contents(contents)

        if on_llm_usage is not None:
            try:
                cont = await on_llm_usage(None, step=step, phase=phase, contents=contents)
                if cont is False:
                    from app.agent.budget import BUDGET_SENTINEL

                    return AgentFail(error=f"{BUDGET_SENTINEL}:week_tokens")
            except Exception:
                log.exception("on_llm_usage_precheck_failed")

        try:
            response = await gemini.chat_async(contents, system=system)
        except Exception as e:
            from app.agent.errors import OOPS_SENTINEL, ProviderQuotaError, is_quota_error

            if isinstance(e, ProviderQuotaError) or is_quota_error(e):
                log.warning("llm_quota_stop", step=step, err=str(e)[:300])
                return AgentFail(error=OOPS_SENTINEL)
            log.exception("llm_chat_failed", step=step)
            return AgentFail(error=str(e)[:500])

        _log_usage(response, step=step, phase=phase)
        if on_llm_usage is not None:
            try:
                cont = await on_llm_usage(response, step=step, phase=phase, contents=contents)
                if cont is False:
                    from app.agent.budget import BUDGET_SENTINEL

                    return AgentFail(error=f"{BUDGET_SENTINEL}:week_tokens")
            except Exception:
                log.exception("on_llm_usage_failed")

        calls = _extract_calls(response)
        has_text = bool(_text_from_response(response))
        try:
            cands = list(response.candidates or [])
        except Exception:
            cands = []
        if not cands and not calls and not has_text:
            reason = getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
            return AgentFail(error=f"empty model response ({reason or 'no candidates'})")

        _append_model_content(contents, response, types)
        _slim_write_file_in_contents(contents)

        if not calls:
            txt = _text_from_response(response)
            if txt:
                return AgentDone(summary=txt[:1500], ok=True)
            return AgentFail(error="model returned no tools and no text")

        # Terminal tools that must be alone
        alone_map = {
            "ask_user": "ask",
            "answer_only": "answer",
            "propose_plan": "plan",
            "finish": "finish",
        }
        alone = [(n, a) for n, a in calls if n in alone_map]
        if alone:
            name, args = alone[0]
            if len(calls) > 1:
                contents.pop()
                contents.append(
                    types.Content(
                        role="model",
                        parts=[types.Part.from_function_call(name=name, args=args)],
                    )
                )
            if name == "ask_user":
                q = str(args.get("question") or "Need more info").strip() or "Need more info"
                if progress:
                    await progress.done_line("вопрос пользователю" if lang == "ru" else "asking user")
                return AgentAskUser(question=q[:2000], contents=contents, system=system)
            if name == "answer_only":
                ans = str(args.get("answer") or "").strip() or "—"
                sug = str(args.get("suggestion") or "").strip()
                if progress:
                    await progress.done_line("ответ готов" if lang == "ru" else "answer ready")
                return AgentAnswer(answer=ans[:3500], suggestion=sug[:1500])
            if name == "propose_plan":
                diagnosis = str(args.get("diagnosis") or "").strip() or "—"
                raw_steps = args.get("steps") or []
                if isinstance(raw_steps, str):
                    steps = [s.strip() for s in raw_steps.split("\n") if s.strip()]
                else:
                    steps = [str(s).strip() for s in raw_steps if str(s).strip()]
                if not steps:
                    steps = ["Review and apply minimal fix"]
                risk = str(args.get("risk") or "low").strip().lower() or "low"
                if progress:
                    await progress.done_line("план готов" if lang == "ru" else "plan ready")
                return AgentProposePlan(
                    diagnosis=diagnosis[:2000],
                    steps=steps[:10],
                    risk=risk[:20],
                    contents=contents,
                    system=system,
                )
            if name == "finish":
                return AgentDone(
                    summary=str(args.get("summary") or "Done")[:2000],
                    ok=bool(args.get("ok", True)),
                )

        fn_parts = []
        for name, args in calls:
            if should_cancel is not None:
                try:
                    if await should_cancel():
                        return AgentFail(error="stopped by user")
                except Exception:
                    pass
            label = _tool_label(name, args, lang)
            if progress:
                await progress.set_current(label)
            try:
                result = await _exec_tool(ssh, name, args)
            except Exception as e:
                result = {"error": str(e)[:800]}
            ok = not (isinstance(result, dict) and result.get("error"))
            if progress:
                await progress.done_line(label, ok=ok)
            fn_parts.append(
                types.Part.from_function_response(
                    name=name,
                    response=_clip_result(result if isinstance(result, dict) else {"result": result}),
                )
            )
        contents.append(types.Content(role="user", parts=fn_parts))

    return AgentFail(error=f"max steps ({max_steps}) reached")


def resume_with_plan_ack(contents: list[Any], *, accepted: bool = True) -> list[Any]:
    """After propose_plan, continue into execute with a fresh user turn (new gemini phase)."""
    from google.genai import types

    contents = list(contents)
    contents.append(
        types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name="propose_plan",
                    response={
                        "accepted": accepted,
                        "note": "User accepted the plan. Execute the steps now.",
                    },
                )
            ],
        )
    )
    return contents


def resume_with_answer(contents: list[Any], answer: str) -> list[Any]:
    from google.genai import types

    contents = list(contents)
    contents.append(
        types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name="ask_user",
                    response={"answer": (answer or "")[:4000]},
                )
            ],
        )
    )
    return contents


def serialize_contents(contents: list[Any]) -> str:
    """Best-effort JSON dump for Redis (lossy for inline image bytes)."""
    out = []
    for c in contents:
        role = getattr(c, "role", "user")
        parts_out = []
        for p in getattr(c, "parts", None) or []:
            if getattr(p, "text", None):
                parts_out.append({"text": p.text})
            elif getattr(p, "function_call", None):
                fc = p.function_call
                parts_out.append(
                    {
                        "function_call": {
                            "name": fc.name,
                            "args": _args_dict(getattr(fc, "args", None)),
                        }
                    }
                )
            elif getattr(p, "function_response", None):
                fr = p.function_response
                resp = getattr(fr, "response", None)
                if not isinstance(resp, dict):
                    resp = {"value": str(resp)}
                else:
                    resp = _clip_result(dict(resp))
                parts_out.append(
                    {"function_response": {"name": fr.name, "response": resp}}
                )
            # skip inline_data / thought / etc. — too large for Redis
        if parts_out:
            out.append({"role": role, "parts": parts_out})
    raw = json.dumps(out, ensure_ascii=False)
    if len(raw) > MAX_CONTENTS_CHARS:
        # keep head + tail turns
        while len(out) > 4 and len(json.dumps(out, ensure_ascii=False)) > MAX_CONTENTS_CHARS:
            # drop oldest non-first user message block
            del out[1]
        raw = json.dumps(out, ensure_ascii=False)
        if len(raw) > MAX_CONTENTS_CHARS:
            raw = json.dumps(out[:3] + out[-2:], ensure_ascii=False)[:MAX_CONTENTS_CHARS]
    return raw


def deserialize_contents(raw: str) -> list[Any]:
    from google.genai import types

    data = json.loads(raw)
    contents = []
    for item in data:
        parts = []
        for p in item.get("parts") or []:
            if "text" in p:
                parts.append(types.Part.from_text(text=str(p["text"])))
            elif "function_call" in p:
                fc = p["function_call"]
                parts.append(
                    types.Part.from_function_call(
                        name=str(fc["name"]),
                        args=_args_dict(fc.get("args")),
                    )
                )
            elif "function_response" in p:
                fr = p["function_response"]
                parts.append(
                    types.Part.from_function_response(
                        name=str(fr["name"]),
                        response=fr.get("response") if isinstance(fr.get("response"), dict) else {},
                    )
                )
        if parts:
            contents.append(types.Content(role=item.get("role") or "user", parts=parts))
    return contents
