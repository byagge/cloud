"""Shared tool definitions for all LLM providers (short — resent every request)."""

from __future__ import annotations

from typing import Any

_SHELL = {
    "name": "run_shell",
    "description": "SSH shell as root. Non-interactive. Never print secrets.",
    "parameters": {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout_sec": {"type": "integer"},
        },
        "required": ["command"],
    },
}
_WRITE = {
    "name": "write_file",
    "description": "Write text file (mkdir -p parents).",
    "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    },
}
_READ = {
    "name": "read_file",
    "description": "Read text file (truncated).",
    "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
}
_BACKUP = {
    "name": "backup_path",
    "description": "Timestamped backup before edit.",
    "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
}
_ASK = {
    "name": "ask_user",
    "description": "One question to user. Call alone.",
    "parameters": {
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    },
}
_FINISH = {
    "name": "finish",
    "description": "Done. Call alone.",
    "parameters": {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "ok": {"type": "boolean"},
        },
        "required": ["summary"],
    },
}
_ANSWER = {
    "name": "answer_only",
    "description": "Advice/diagnosis, no changes. Call alone.",
    "parameters": {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "suggestion": {"type": "string"},
        },
        "required": ["answer"],
    },
}
_PLAN = {
    "name": "propose_plan",
    "description": "Small fix plan (2–8 steps). Call alone; user must accept.",
    "parameters": {
        "type": "object",
        "properties": {
            "diagnosis": {"type": "string"},
            "steps": {"type": "array", "items": {"type": "string"}},
            "risk": {"type": "string"},
        },
        "required": ["diagnosis", "steps"],
    },
}

TOOLS_BY_PHASE: dict[str, list[dict[str, Any]]] = {
    "analyze": [_SHELL, _READ, _ASK, _ANSWER, _PLAN],
    "execute": [_SHELL, _READ, _WRITE, _BACKUP, _ASK, _FINISH],
    "deploy": [_SHELL, _READ, _WRITE, _BACKUP, _ASK, _FINISH],
    "support": [_SHELL, _READ, _ASK, _ANSWER, _PLAN],
}

# Soft caps per phase (each step resends full history)
MAX_STEPS_BY_PHASE: dict[str, int] = {
    "analyze": 10,
    "execute": 14,
    "deploy": 16,
    "support": 10,
}

# Output token budget (completion) — input history is the real cost
MAX_OUTPUT_TOKENS_BY_PHASE: dict[str, int] = {
    "analyze": 1536,
    "execute": 2560,
    "deploy": 2560,
    "support": 1536,
}


def tools_for_phase(phase: str) -> list[dict[str, Any]]:
    return list(TOOLS_BY_PHASE.get(phase) or TOOLS_BY_PHASE["analyze"])


def max_steps_for_phase(phase: str) -> int:
    return MAX_STEPS_BY_PHASE.get(phase) or 12


def max_output_tokens_for_phase(phase: str) -> int:
    return MAX_OUTPUT_TOKENS_BY_PHASE.get(phase) or 2048
