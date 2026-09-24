"""Gemini tool-calling agent loop (Cursor-like)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.agent.gemini import GeminiAgent
from app.agent.prompts import DEPLOY_SYSTEM, SUPPORT_SYSTEM
from app.agent.ssh import SshSession
from app.logging import get_logger

log = get_logger("agent.loop")


@dataclass
class AgentAskUser:
    question: str
    contents: list[Any]
    system: str


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


def _extract_calls(response: Any) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    try:
        for cand in response.candidates or []:
            parts = getattr(cand.content, "parts", None) or []
            for part in parts:
                fc = getattr(part, "function_call", None)
                if fc and getattr(fc, "name", None):
                    args = dict(fc.args or {})
                    calls.append((fc.name, args))
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
    return ""


async def _exec_tool(ssh: SshSession, name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "run_shell":
        cmd = str(args.get("command") or "")
        timeout = float(args.get("timeout_sec") or 120)
        res = await ssh.run(cmd, timeout=timeout)
        return {
            "exit_code": res.exit_code,
            "stdout": res.stdout,
            "stderr": res.stderr,
        }
    if name == "write_file":
        path = str(args.get("path") or "")
        content = str(args.get("content") or "")
        await ssh.write_text(path, content)
        return {"ok": True, "path": path, "bytes": len(content.encode("utf-8"))}
    if name == "read_file":
        path = str(args.get("path") or "")
        text = await ssh.read_text(path)
        return {"path": path, "content": text}
    if name == "backup_path":
        path = str(args.get("path") or "")
        dest = await ssh.backup(path)
        return {"ok": True, "backup": dest}
    return {"error": f"unknown tool {name}"}


async def run_agent(
    *,
    ssh: SshSession,
    gemini: GeminiAgent,
    mode: str,
    user_goal: str,
    extra_context: str = "",
    contents: list[Any] | None = None,
    max_steps: int = 28,
) -> AgentAskUser | AgentDone | AgentFail:
    system = DEPLOY_SYSTEM if mode == "deploy" else SUPPORT_SYSTEM
    from google.genai import types

    if contents is None:
        prompt = user_goal
        if extra_context:
            prompt = f"{user_goal}\n\n---\nContext:\n{extra_context}"
        contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]

    for step in range(max_steps):
        try:
            response = gemini.chat(contents, system=system)
        except Exception as e:
            log.exception("gemini_chat_failed")
            return AgentFail(error=str(e)[:500])

        calls = _extract_calls(response)
        # append model content
        try:
            model_content = response.candidates[0].content
            contents.append(model_content)
        except Exception:
            txt = _text_from_response(response)
            if txt:
                contents.append(types.Content(role="model", parts=[types.Part.from_text(text=txt)]))

        if not calls:
            # no tools — treat as soft finish if text looks final
            txt = _text_from_response(response)
            if txt:
                return AgentDone(summary=txt[:1500], ok=True)
            return AgentFail(error="model returned no tools and no text")

        fn_parts = []
        for name, args in calls:
            if name == "ask_user":
                q = str(args.get("question") or "Need more info")
                return AgentAskUser(question=q, contents=contents, system=system)
            if name == "finish":
                return AgentDone(
                    summary=str(args.get("summary") or "Done")[:2000],
                    ok=bool(args.get("ok", True)),
                )
            try:
                result = await _exec_tool(ssh, name, args)
            except Exception as e:
                result = {"error": str(e)[:800]}
            fn_parts.append(
                types.Part.from_function_response(
                    name=name,
                    response=result if isinstance(result, dict) else {"result": result},
                )
            )
        contents.append(types.Content(role="user", parts=fn_parts))

    return AgentFail(error=f"max steps ({max_steps}) reached")


def resume_with_answer(contents: list[Any], answer: str) -> list[Any]:
    from google.genai import types

    contents = list(contents)
    contents.append(
        types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name="ask_user",
                    response={"answer": answer},
                )
            ],
        )
    )
    return contents


def serialize_contents(contents: list[Any]) -> str:
    """Best-effort JSON dump for Redis (lossy for some Part types)."""
    from google.genai import types

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
                    {"function_call": {"name": fc.name, "args": dict(fc.args or {})}}
                )
            elif getattr(p, "function_response", None):
                fr = p.function_response
                parts_out.append(
                    {
                        "function_response": {
                            "name": fr.name,
                            "response": dict(fr.response or {})
                            if isinstance(fr.response, dict)
                            else {"value": str(fr.response)},
                        }
                    }
                )
        out.append({"role": role, "parts": parts_out})
    return json.dumps(out, ensure_ascii=False)


def deserialize_contents(raw: str) -> list[Any]:
    from google.genai import types

    data = json.loads(raw)
    contents = []
    for item in data:
        parts = []
        for p in item.get("parts") or []:
            if "text" in p:
                parts.append(types.Part.from_text(text=p["text"]))
            elif "function_call" in p:
                fc = p["function_call"]
                parts.append(
                    types.Part.from_function_call(name=fc["name"], args=fc.get("args") or {})
                )
            elif "function_response" in p:
                fr = p["function_response"]
                parts.append(
                    types.Part.from_function_response(
                        name=fr["name"], response=fr.get("response") or {}
                    )
                )
        contents.append(types.Content(role=item.get("role") or "user", parts=parts))
    return contents
