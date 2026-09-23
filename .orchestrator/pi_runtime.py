"""Pi CLI command and JSONL handling shared by delivery and health probes."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any


def settings(config: dict, provider_id: str) -> dict:
    return config.get("providers", {}).get(provider_id, {}).get("pi", {})


def binary(profile: dict) -> str | None:
    configured = os.path.expanduser(str(profile.get("cli") or "~/.local/bin/pi"))
    # An explicitly configured executable must never silently use another Pi.
    resolved = shutil.which(configured)
    return str(Path(resolved).resolve()) if resolved else None


def environment(profile: dict) -> dict[str, str]:
    env = dict(os.environ)
    for key in ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_CONVERSATION_ID",
                "CODEX_PARENT_THREAD_ID", "PI_CODING_AGENT_SESSION_DIR"):
        env.pop(key, None)
    env["PI_CODING_AGENT_DIR"] = str(Path(str(profile.get("agent_dir") or "~/.pi/pantheon-astra")).expanduser())
    env["PI_TELEMETRY"] = "0"
    # Subscription workers must not accidentally inherit API billing.
    api_key_env = str(profile.get("api_key_env") or "")
    api_key = env.get(api_key_env) if api_key_env else None
    env.pop("OPENAI_API_KEY", None)
    if api_key:
        env["OPENAI_API_KEY"] = api_key
    return env


def command(cli: str, profile: dict, prompt: str, *, probe: bool = False) -> list[str]:
    argv = [cli, "--mode", "json", "--provider", str(profile.get("provider") or "openai-codex"),
            "--model", str(profile.get("model") or "gpt-6-astra"),
            "--thinking", "low" if probe else str(profile.get("thinking") or "high"),
            "--no-extensions", "--no-approve"]
    if probe:
        argv += ["--no-tools", "--no-context-files", "--no-skills",
                 "--no-prompt-templates", "--no-themes", "--no-session"]
    # -- prevents a task prompt beginning with '-' from becoming CLI options.
    return [*argv, "--", prompt]


def stream_state(content: str) -> dict[str, Any]:
    """Read control envelopes; nested tool/user text is never control data.

    Pi JSON mode can exit zero after a model error. agent_end is not terminal:
    retries and compaction may follow. Only agent_settled finalizes the latest
    assistant message, including a recovered success after an earlier error.
    """
    state: dict[str, Any] = {"session_id": None, "settled": False, "error": None,
                             "text": "", "usage": {}}
    assistant: dict[str, Any] | None = None
    for line in content.split("\n"):
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "session" and isinstance(event.get("id"), str):
            state["session_id"] = event["id"]
        elif kind in {"agent_start", "turn_start", "auto_retry_start", "compaction_start"}:
            state["settled"] = False
            state["error"] = None
        elif kind == "message_end":
            message = event.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                assistant = message
        elif kind == "agent_settled":
            state["settled"] = True
            if not assistant:
                state["error"] = "Pi settled without an assistant result"
                continue
            state["usage"] = assistant.get("usage") if isinstance(assistant.get("usage"), dict) else {}
            blocks = assistant.get("content")
            state["text"] = "".join(str(block.get("text") or "") for block in blocks
                                    if isinstance(block, dict) and block.get("type") == "text") if isinstance(blocks, list) else ""
            reason = assistant.get("stopReason")
            state["error"] = (str(assistant.get("errorMessage") or f"Pi request {reason}")
                              if reason in {"error", "aborted"} else None)
    return state
