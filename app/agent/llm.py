"""Multi-provider LLM: Gemini → OpenAI → Anthropic failover."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from app.agent.errors import ProviderError, ProviderQuotaError, is_quota_error
from app.agent.tools_def import max_output_tokens_for_phase, tools_for_phase
from app.config import Settings, get_settings

log = logging.getLogger(__name__)


class _FC:
    __slots__ = ("name", "args")

    def __init__(self, name: str, args: dict[str, Any]) -> None:
        self.name = name
        self.args = args or {}


class _ShimResponse:
    """Looks enough like a Gemini response for the agent loop."""

    def __init__(self, *, text: str, function_calls: list[Any], usage: Any = None) -> None:
        self.text = text
        self.function_calls = function_calls
        self.candidates: list[Any] = []
        self.usage = usage
        self.usage_metadata = usage


def _part_text(p: Any) -> str:
    t = getattr(p, "text", None)
    return t if isinstance(t, str) else ""


def _part_inline(p: Any) -> tuple[bytes, str] | None:
    data = getattr(p, "inline_data", None)
    if data is None:
        return None
    raw = getattr(data, "data", None)
    mime = getattr(data, "mime_type", None) or "image/jpeg"
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw), str(mime)
    if isinstance(raw, str):
        try:
            return base64.b64decode(raw), str(mime)
        except Exception:
            return None
    return None


def _fc_from_part(p: Any) -> _FC | None:
    fc = getattr(p, "function_call", None)
    if not fc:
        return None
    name = getattr(fc, "name", None) or ""
    args = getattr(fc, "args", None) or {}
    if hasattr(args, "items"):
        args = dict(args)
    elif not isinstance(args, dict):
        args = {}
    return _FC(name, args) if name else None


def _fr_from_part(p: Any) -> dict[str, Any] | None:
    fr = getattr(p, "function_response", None)
    if not fr:
        return None
    name = getattr(fr, "name", None) or "tool"
    resp = getattr(fr, "response", None) or {}
    if hasattr(resp, "items"):
        resp = dict(resp)
    return {"name": name, "response": resp}


# ── OpenAI ──────────────────────────────────────────────────────────────────


def _openai_tools(phase: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in tools_for_phase(phase)
    ]


def _contents_to_openai(contents: list[Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    for c in contents:
        role = getattr(c, "role", None) or "user"
        parts = list(getattr(c, "parts", None) or [])

        if role == "model":
            text_bits: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for i, p in enumerate(parts):
                t = _part_text(p)
                if t:
                    text_bits.append(t)
                fc = _fc_from_part(p)
                if fc:
                    tool_calls.append(
                        {
                            "id": f"call_{i}_{fc.name}",
                            "type": "function",
                            "function": {
                                "name": fc.name,
                                "arguments": json.dumps(fc.args, ensure_ascii=False),
                            },
                        }
                    )
            msg: dict[str, Any] = {
                "role": "assistant",
                "content": "\n".join(text_bits) if text_bits else None,
            }
            if tool_calls:
                msg["tool_calls"] = tool_calls
            messages.append(msg)
            continue

        text_bits = []
        images: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        for i, p in enumerate(parts):
            t = _part_text(p)
            if t:
                text_bits.append(t)
            img = _part_inline(p)
            if img:
                raw, mime = img
                b64 = base64.b64encode(raw).decode("ascii")
                images.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    }
                )
            fr = _fr_from_part(p)
            if fr:
                tool_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": f"call_{i}_{fr['name']}",
                        "content": json.dumps(fr["response"], ensure_ascii=False),
                    }
                )

        if tool_results:
            last_ids: list[str] = []
            for m in reversed(messages):
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    last_ids = [tc["id"] for tc in m["tool_calls"]]
                    break
            for j, tr in enumerate(tool_results):
                if j < len(last_ids):
                    tr["tool_call_id"] = last_ids[j]
                messages.append(tr)
            if text_bits or images:
                if images:
                    content: Any = [
                        {"type": "text", "text": "\n".join(text_bits) or "(image)"}
                    ]
                    content.extend(images)
                else:
                    content = "\n".join(text_bits)
                messages.append({"role": "user", "content": content})
            continue

        if images:
            content = [{"type": "text", "text": "\n".join(text_bits) or "(image)"}]
            content.extend(images)
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": "\n".join(text_bits) or ""})

    return messages


class _OpenAIBackend:
    name = "openai"

    def __init__(self, api_key: str, model: str, phase: str) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._phase = phase
        self._max_out = max_output_tokens_for_phase(phase)

    async def chat_async(self, contents: list[Any], *, system: str) -> Any:
        messages = [{"role": "system", "content": system}] + _contents_to_openai(contents)
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=_openai_tools(self._phase),
                temperature=0.2,
                max_tokens=self._max_out,
            )
        except Exception as e:
            if is_quota_error(e):
                raise ProviderQuotaError(self.name, str(e)) from e
            raise ProviderError(self.name, str(e)) from e

        choice = resp.choices[0].message if resp.choices else None
        text = (choice.content or "") if choice else ""
        fcs: list[Any] = []
        if choice and choice.tool_calls:
            for tc in choice.tool_calls:
                name = getattr(tc.function, "name", "") or ""
                raw_args = getattr(tc.function, "arguments", "") or "{}"
                try:
                    args = json.loads(raw_args)
                except Exception:
                    args = {}
                if name:
                    fcs.append(_FC(name, args if isinstance(args, dict) else {}))
        shim = _ShimResponse(text=text, function_calls=fcs)
        if getattr(resp, "usage", None) is not None:
            shim.usage = resp.usage
            shim.usage_metadata = resp.usage
        return shim


# ── Anthropic ───────────────────────────────────────────────────────────────


def _anthropic_tools(phase: str) -> list[dict[str, Any]]:
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["parameters"],
        }
        for t in tools_for_phase(phase)
    ]


def _contents_to_anthropic(contents: list[Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for c in contents:
        role = getattr(c, "role", None) or "user"
        parts = list(getattr(c, "parts", None) or [])
        blocks: list[dict[str, Any]] = []

        if role == "model":
            for i, p in enumerate(parts):
                t = _part_text(p)
                if t:
                    blocks.append({"type": "text", "text": t})
                fc = _fc_from_part(p)
                if fc:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": f"toolu_{i}_{fc.name}",
                            "name": fc.name,
                            "input": fc.args,
                        }
                    )
            if blocks:
                messages.append({"role": "assistant", "content": blocks})
            continue

        for i, p in enumerate(parts):
            t = _part_text(p)
            if t:
                blocks.append({"type": "text", "text": t})
            img = _part_inline(p)
            if img:
                raw, mime = img
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime,
                            "data": base64.b64encode(raw).decode("ascii"),
                        },
                    }
                )
            fr = _fr_from_part(p)
            if fr:
                tid = f"toolu_{i}_{fr['name']}"
                for m in reversed(messages):
                    if m.get("role") == "assistant":
                        uses = [
                            b
                            for b in (m.get("content") or [])
                            if isinstance(b, dict) and b.get("type") == "tool_use"
                        ]
                        if i < len(uses):
                            tid = uses[i]["id"]
                        break
                blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": json.dumps(fr["response"], ensure_ascii=False),
                    }
                )
        if blocks:
            messages.append({"role": "user", "content": blocks})
    return messages


class _AnthropicBackend:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, phase: str) -> None:
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._phase = phase
        self._max_out = max_output_tokens_for_phase(phase)

    async def chat_async(self, contents: list[Any], *, system: str) -> Any:
        messages = _contents_to_anthropic(contents)
        try:
            resp = await self._client.messages.create(
                model=self._model,
                system=system,
                messages=messages,
                tools=_anthropic_tools(self._phase),
                temperature=0.2,
                max_tokens=self._max_out,
            )
        except Exception as e:
            if is_quota_error(e):
                raise ProviderQuotaError(self.name, str(e)) from e
            raise ProviderError(self.name, str(e)) from e

        text_bits: list[str] = []
        fcs: list[Any] = []
        for block in resp.content or []:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_bits.append(getattr(block, "text", "") or "")
            elif btype == "tool_use":
                name = getattr(block, "name", "") or ""
                inp = getattr(block, "input", None) or {}
                if name:
                    fcs.append(_FC(name, inp if isinstance(inp, dict) else {}))
        return _ShimResponse(
            text="\n".join(text_bits),
            function_calls=fcs,
            usage=getattr(resp, "usage", None),
        )


# ── Public facade ───────────────────────────────────────────────────────────


class AgentLLM:
    """Chat with configured providers; failover on soft errors; stop hard on quota."""

    def __init__(self, *, phase: str = "analyze", settings: Settings | None = None) -> None:
        from app.agent.gemini import GeminiAgent, TOOLS_BY_PHASE

        settings = settings or get_settings()
        self.phase = phase if phase in TOOLS_BY_PHASE else "analyze"
        self._backends: list[Any] = []

        if (settings.gemini_api_key or "").strip():
            self._backends.append(GeminiAgent(phase=self.phase, api_key=settings.gemini_api_key))
        if (getattr(settings, "openai_api_key", None) or "").strip():
            self._backends.append(
                _OpenAIBackend(
                    settings.openai_api_key.strip(),
                    getattr(settings, "openai_model", None) or "gpt-4o-mini",
                    self.phase,
                )
            )
        if (getattr(settings, "anthropic_api_key", None) or "").strip():
            self._backends.append(
                _AnthropicBackend(
                    settings.anthropic_api_key.strip(),
                    getattr(settings, "anthropic_model", None) or "claude-sonnet-4-20250514",
                    self.phase,
                )
            )
        if not self._backends:
            raise RuntimeError("No LLM API keys configured (GEMINI / OPENAI / ANTHROPIC)")

    @property
    def providers(self) -> list[str]:
        return [getattr(b, "name", type(b).__name__) for b in self._backends]

    async def chat_async(self, contents: list[Any], *, system: str) -> Any:
        last_err: BaseException | None = None
        quota_hits: list[ProviderQuotaError] = []

        for i, backend in enumerate(self._backends):
            name = getattr(backend, "name", type(backend).__name__)
            try:
                result = await backend.chat_async(contents, system=system)
                if i > 0:
                    log.info("llm_failover_ok provider=%s", name)
                try:
                    setattr(result, "_arix_provider", name)
                except Exception:
                    pass
                return result
            except ProviderQuotaError as e:
                log.warning("llm_quota provider=%s detail=%s", e.provider, e.detail)
                quota_hits.append(e)
                last_err = e
                continue
            except ProviderError as e:
                log.warning("llm_error provider=%s detail=%s", e.provider, e.detail)
                last_err = e
                continue
            except Exception as e:
                if is_quota_error(e):
                    qe = ProviderQuotaError(str(name), str(e))
                    log.warning("llm_quota provider=%s detail=%s", qe.provider, qe.detail)
                    quota_hits.append(qe)
                    last_err = qe
                    continue
                log.exception("llm_unexpected provider=%s", name)
                last_err = e
                continue

        if quota_hits:
            raise quota_hits[0]
        raise last_err or RuntimeError("All LLM providers failed")


def has_any_llm_key(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return bool(
        (settings.gemini_api_key or "").strip()
        or (getattr(settings, "openai_api_key", "") or "").strip()
        or (getattr(settings, "anthropic_api_key", "") or "").strip()
    )
