from __future__ import annotations

from adapters.base import BaseAdapter
from adapters.claude_cli import ClaudeCLIAdapter
from adapters.claude_code import ClaudeCodeAdapter
from adapters.copilot_cloud import CopilotCloudAdapter
from adapters.copilot_local import CopilotLocalAdapter
from adapters.antigravity import AntigravityAdapter
from adapters.codex import CodexAdapter
from adapters.file_inbox import FileInboxAdapter
from adapters.gemini import GeminiAdapter
from adapters.vscode_chat import VSCodeChatAdapter
from adapters.vscode_command import VSCodeCommandAdapter


ADAPTERS: dict[str, type[BaseAdapter]] = {
    "file_inbox": FileInboxAdapter,
    "vscode_chat": VSCodeChatAdapter,
    "vscode_command": VSCodeCommandAdapter,
    "claude_cli": ClaudeCLIAdapter,
    "claude_code": ClaudeCodeAdapter,
    "copilot_local": CopilotLocalAdapter,
    "copilot_cloud": CopilotCloudAdapter,
    "gemini": GeminiAdapter,
    "antigravity": AntigravityAdapter,
    "codex": CodexAdapter,
}


def build_adapter(name: str, config: dict, provider_capabilities: dict | None = None) -> BaseAdapter:
    adapter_cls = ADAPTERS.get(name)
    if adapter_cls is None:
        raise KeyError(f"Unknown adapter: {name}")
    return adapter_cls(config=config, provider_capabilities=provider_capabilities or {})
