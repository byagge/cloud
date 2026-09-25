"""SSH + Gemini agent for autodeploy and project support (Cursor-like tool loop)."""

from __future__ import annotations

from app.agent.loop import (
    AgentAnswer,
    AgentAskUser,
    AgentDone,
    AgentFail,
    AgentProposePlan,
    _is_dangerous_command,
    _pending_ask_user,
    deserialize_contents,
    resume_with_answer,
    resume_with_plan_ack,
    run_agent,
    serialize_contents,
)
from app.agent.ssh import SshSession, open_ssh

__all__ = [
    "AgentAnswer",
    "AgentAskUser",
    "AgentDone",
    "AgentFail",
    "AgentProposePlan",
    "SshSession",
    "_is_dangerous_command",
    "_pending_ask_user",
    "deserialize_contents",
    "open_ssh",
    "resume_with_answer",
    "resume_with_plan_ack",
    "run_agent",
    "serialize_contents",
]
