"""Gemini client wrapper for tool-calling agent loop."""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.logging import get_logger

log = get_logger("agent.gemini")

TOOLS: list[dict[str, Any]] = [
    {
        "name": "run_shell",
        "description": (
            "Run a shell command on the client VPS as root over SSH. "
            "Prefer non-interactive flags. Never print secrets in commands."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"},
                "timeout_sec": {"type": "integer", "description": "Timeout seconds", "default": 120},
            },
            "required": ["command"],
        },
    },
    {
        "name": "write_file",
        "description": "Write text content to a file on the VPS (creates parent dirs).",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a text file from the VPS (truncated).",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "backup_path",
        "description": (
            "Create a timestamped backup copy before modifying a file or directory. "
            "ALWAYS call this before destructive edits."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "ask_user",
        "description": (
            "Ask the user a question in Telegram when you need tokens, passwords, "
            "domains, env vars, or clarification. Wait for their reply."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
            },
            "required": ["question"],
        },
    },
    {
        "name": "finish",
        "description": "Finish the task and report a short summary to the user.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "ok": {"type": "boolean", "default": True},
            },
            "required": ["summary"],
        },
    },
]


def _declarations() -> list[Any]:
    from google.genai import types

    out = []
    for t in TOOLS:
        # parameters as JSON schema dict (google-genai accepts Schema or dict)
        params = t["parameters"]
        try:
            schema = types.Schema(
                type=params.get("type", "object"),
                properties={
                    k: types.Schema(
                        type=v.get("type", "string"),
                        description=v.get("description"),
                    )
                    for k, v in (params.get("properties") or {}).items()
                },
                required=params.get("required") or [],
            )
            out.append(
                types.FunctionDeclaration(
                    name=t["name"],
                    description=t["description"],
                    parameters=schema,
                )
            )
        except Exception:
            out.append(
                types.FunctionDeclaration(
                    name=t["name"],
                    description=t["description"],
                    parameters=params,
                )
            )
    return out


class GeminiAgent:
    def __init__(self, *, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self.api_key = (api_key or settings.gemini_api_key or "").strip()
        self.model = model or settings.gemini_model or "gemini-2.0-flash"
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY missing")
        from google import genai

        self._client = genai.Client(api_key=self.api_key)

    def chat(self, contents: list[Any], *, system: str) -> Any:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=_declarations())],
            temperature=0.2,
        )
        return self._client.models.generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )
