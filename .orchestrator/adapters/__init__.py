from __future__ import annotations

from adapters.base import BaseAdapter
from adapters.antigravity import AntigravityAdapter
from adapters.claude_cli import ClaudeCLIAdapter
from adapters.copilot_cloud import CopilotCloudAdapter
from adapters.copilot_local import CopilotLocalAdapter
from adapters.codex import CodexAdapter
from adapters.gemini import GeminiAdapter


ADAPTERS: dict[str, type[BaseAdapter]] = {
    "claude_cli": ClaudeCLIAdapter,
    "copilot_local": CopilotLocalAdapter,
    "copilot_cloud": CopilotCloudAdapter,
    "antigravity": AntigravityAdapter,
    "gemini": GeminiAdapter,
    "codex": CodexAdapter,
}


def build_adapter(name: str, config: dict, provider_capabilities: dict | None = None) -> BaseAdapter:
    adapter_cls = ADAPTERS.get(name)
    if adapter_cls is None:
        raise KeyError(f"Unknown adapter: {name}")
    return adapter_cls(config=config, provider_capabilities=provider_capabilities or {})
