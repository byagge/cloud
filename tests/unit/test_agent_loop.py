"""Unit tests for agent serialize / resume helpers (no live Gemini/SSH)."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("google.genai")

from google.genai import types

from app.agent.loop import (
    _args_dict,
    _clip_result,
    _is_dangerous_command,
    _pending_ask_user,
    deserialize_contents,
    resume_with_answer,
    serialize_contents,
)


def test_args_dict_none():
    assert _args_dict(None) == {}


def test_clip_result_truncates():
    big = {"stdout": "x" * 50_000, "stderr": "y" * 10_000, "exit_code": 0}
    clipped = _clip_result(big)
    raw = json.dumps(clipped)
    assert len(raw) <= 14_000 + 100
    assert "truncated" in clipped["stdout"] or "preview" in clipped


def test_serialize_roundtrip_text_and_tools():
    contents = [
        types.Content(role="user", parts=[types.Part.from_text(text="deploy my app")]),
        types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(
                    name="run_shell",
                    args={"command": "ls /opt", "timeout_sec": 30},
                )
            ],
        ),
        types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name="run_shell",
                    response={"exit_code": 0, "stdout": "arix-apps\n", "stderr": ""},
                )
            ],
        ),
    ]
    raw = serialize_contents(contents)
    back = deserialize_contents(raw)
    assert len(back) == 3
    assert back[0].parts[0].text == "deploy my app"
    assert back[1].parts[0].function_call.name == "run_shell"
    assert dict(back[1].parts[0].function_call.args)["command"] == "ls /opt"
    assert back[2].parts[0].function_response.name == "run_shell"


def test_resume_with_answer_appends_ask_user_response():
    contents = [
        types.Content(role="user", parts=[types.Part.from_text(text="hi")]),
        types.Content(
            role="model",
            parts=[types.Part.from_function_call(name="ask_user", args={"question": "token?"})],
        ),
    ]
    resumed = resume_with_answer(contents, "secret-token")
    assert len(resumed) == 3
    fr = resumed[-1].parts[0].function_response
    assert fr.name == "ask_user"
    assert dict(fr.response)["answer"] == "secret-token"


@pytest.mark.parametrize(
    "cmd",
    [
        "rm -rf /",
        "rm -rf /*",
        "sudo rm -rf / --no-preserve-root",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda bs=1M",
        "wipefs -a /dev/sdb",
        "passwd root",
        ":(){ :|:& };:",
        "echo hi > /dev/sda",
    ],
)
def test_dangerous_commands_blocked(cmd: str):
    reason = _is_dangerous_command(cmd)
    assert reason is not None, f"expected block for: {cmd}"


@pytest.mark.parametrize(
    "cmd",
    [
        "ls /opt/arix-apps",
        "rm -rf /opt/arix-apps/myapp/tmp",
        "systemctl restart arix-web",
        "cat /etc/passwd",
        "docker compose up -d",
        "mkdir -p /opt/arix-apps/demo",
    ],
)
def test_safe_commands_allowed(cmd: str):
    assert _is_dangerous_command(cmd) is None


def test_pending_ask_user_true_until_answered():
    contents = [
        types.Content(role="user", parts=[types.Part.from_text(text="hi")]),
        types.Content(
            role="model",
            parts=[types.Part.from_function_call(name="ask_user", args={"question": "?"})],
        ),
    ]
    assert _pending_ask_user(contents) is True
    answered = resume_with_answer(contents, "ok")
    assert _pending_ask_user(answered) is False


def test_pending_ask_user_empty():
    assert _pending_ask_user([]) is False
    contents = [
        types.Content(role="user", parts=[types.Part.from_text(text="hi")]),
    ]
    assert _pending_ask_user(contents) is False
