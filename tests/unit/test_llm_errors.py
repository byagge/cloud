"""Unit tests for LLM provider errors and tool defs."""

from __future__ import annotations

import pytest

from app.agent.errors import OOPS_SENTINEL, ProviderQuotaError, is_quota_error
from app.agent.tools_def import TOOLS_BY_PHASE, tools_for_phase


def test_is_quota_error_gemini_resource_exhausted():
    msg = (
        "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': "
        "'You exceeded your current quota, please check your plan and billing details.'}}"
    )
    assert is_quota_error(Exception(msg))


def test_openai_contents_roundtrip_tools():
    pytest.importorskip("google.genai")
    from google.genai import types

    from app.agent.llm import _contents_to_openai

    contents = [
        types.Content(role="user", parts=[types.Part.from_text(text="fix nginx")]),
        types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(
                    name="run_shell",
                    args={"command": "nginx -t"},
                )
            ],
        ),
        types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name="run_shell",
                    response={"exit_code": 0, "stdout": "ok", "stderr": ""},
                )
            ],
        ),
    ]
    msgs = _contents_to_openai(contents)
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["tool_calls"][0]["function"]["name"] == "run_shell"
    assert msgs[2]["role"] == "tool"
    assert msgs[2]["tool_call_id"] == msgs[1]["tool_calls"][0]["id"]


def test_anthropic_contents_roundtrip_tools():
    pytest.importorskip("google.genai")
    from google.genai import types

    from app.agent.llm import _contents_to_anthropic

    contents = [
        types.Content(role="user", parts=[types.Part.from_text(text="fix nginx")]),
        types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(
                    name="run_shell",
                    args={"command": "nginx -t"},
                )
            ],
        ),
        types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name="run_shell",
                    response={"exit_code": 0, "stdout": "ok", "stderr": ""},
                )
            ],
        ),
    ]
    msgs = _contents_to_anthropic(contents)
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"][0]["type"] == "tool_use"
    assert msgs[2]["role"] == "user"
    assert msgs[2]["content"][0]["type"] == "tool_result"
    assert msgs[2]["content"][0]["tool_use_id"] == msgs[1]["content"][0]["id"]


def test_is_quota_error_openai_insufficient_quota():
    assert is_quota_error(Exception("Error code: 429 - insufficient_quota"))


def test_is_quota_error_soft_rate_limit_not_hard():
    # Soft rate limit without quota wording should NOT be treated as hard oops
    assert not is_quota_error(Exception("429 Too Many Requests — rate limit"))


def test_provider_quota_error_str():
    e = ProviderQuotaError("gemini", "RESOURCE_EXHAUSTED")
    assert e.provider == "gemini"
    assert "gemini" in str(e)


def test_oops_sentinel():
    assert OOPS_SENTINEL == "PROVIDER_OOPS"


def test_tools_by_phase():
    analyze = {t["name"] for t in tools_for_phase("analyze")}
    execute = {t["name"] for t in tools_for_phase("execute")}
    assert "propose_plan" in analyze
    assert "write_file" not in analyze
    assert "write_file" in execute
    assert "finish" in execute
    assert set(TOOLS_BY_PHASE) >= {"analyze", "execute", "deploy", "support"}
