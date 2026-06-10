from __future__ import annotations

import os
from pathlib import Path

from adapters.base import BaseAdapter, DeliveryCapability, DeliveryRequest, DeliveryResult
from adapters.file_inbox import FileInboxAdapter
from common import (
    agent_config_for,
    command_exists,
    delivery_runtime_env,
    delivery_workspace_root,
    load_json,
    new_runtime_id,
    run_command,
    runtime_log_path,
    shell_quote,
    spawn_background_process,
    worker_runtime_paths,
)


QWEN_SETTINGS_PATH = Path.home() / ".qwen" / "settings.json"


def _qwen_settings() -> dict:
    return load_json(QWEN_SETTINGS_PATH, default={}) or {}


def _configured_value(settings: dict, key: str, env_name: str | None = None) -> str | None:
    direct = str(settings.get(key) or "").strip()
    if direct:
        return direct
    configured_env_name = str(settings.get(f"{key}_env") or env_name or "").strip()
    if configured_env_name and os.environ.get(configured_env_name):
        return str(os.environ.get(configured_env_name) or "").strip() or None
    return None


def _saved_auth_ready(cli: str | None) -> bool:
    if not cli:
        return False
    result = run_command([cli, "auth", "status"])
    output = ((result.stdout or "") + (result.stderr or "")).lower()
    return bool(output) and "no authentication method configured" not in output


def _qwen_auth_ready(runtime: dict) -> bool:
    cli = command_exists(runtime.get("cli") or "qwen")
    if _saved_auth_ready(cli):
        return True

    settings = _qwen_settings()
    auth_type = str(runtime.get("auth_type") or settings.get("security", {}).get("auth", {}).get("selectedType") or "").strip()
    if auth_type == "openai":
        return bool(_configured_value(runtime, "openai_api_key", "OPENAI_API_KEY"))
    if auth_type == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    if auth_type == "gemini":
        return bool(os.environ.get("GEMINI_API_KEY"))
    if auth_type == "vertex-ai":
        return bool(os.environ.get("GOOGLE_API_KEY") or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))
    return False


def _resolved_model(runtime: dict) -> str | None:
    settings = _qwen_settings()
    configured = _configured_value(runtime, "model", "OPENAI_MODEL")
    if configured:
        return configured
    settings_model = str(settings.get("model", {}).get("name") or "").strip()
    return settings_model or None


def _runtime_env(runtime: dict) -> dict[str, str]:
    env: dict[str, str] = {}
    openai_api_key = _configured_value(runtime, "openai_api_key", "OPENAI_API_KEY")
    openai_base_url = _configured_value(runtime, "openai_base_url", "OPENAI_BASE_URL")
    if openai_api_key:
        env["OPENAI_API_KEY"] = openai_api_key
    if openai_base_url:
        env["OPENAI_BASE_URL"] = openai_base_url
    return env


class QwenAdapter(BaseAdapter):
    name = "qwen"

    def capability(self, agent_id: str) -> DeliveryCapability:
        provider = self.config.get("providers", {}).get("qwen", {})
        runtime = provider.get("qwen", {})
        cli = command_exists(runtime.get("cli") or "qwen")
        auth_ready = _qwen_auth_ready(runtime)
        supported = bool(cli and auth_ready)
        if cli and auth_ready:
            notes = "Uses the official Qwen Code CLI in non-interactive mode with standalone Qwen authentication."
        elif cli:
            notes = "Qwen Code CLI is installed but not authenticated/configured for non-interactive use, so delivery falls back to inbox."
        else:
            notes = "Qwen Code CLI is not installed."
        return DeliveryCapability(
            adapter=self.name,
            supported=bool(cli),
            requires_manual_confirmation=not supported,
            can_auto_deliver=supported,
            can_auto_approve_edits=supported,
            delivery_mode="qwen" if supported else "file_inbox",
            verified="verified" if supported else ("partial" if cli else "unavailable"),
            host="Official Qwen Code CLI" if cli else "Qwen Code CLI + inbox fallback",
            notes=notes,
        )

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        capability = self.capability(request.agent_id)
        if not capability.supported or not capability.can_auto_deliver:
            fallback = FileInboxAdapter(config=self.config, provider_capabilities=self.provider_capabilities)
            result = fallback.deliver(request)
            result.adapter = self.name
            result.mode = "file_inbox"
            result.notes = f"{result.notes}. {capability.notes}"
            if not capability.supported:
                result.error = capability.notes
            return DeliveryResult(
                ok=result.ok,
                adapter=result.adapter,
                mode=result.mode,
                target=result.target,
                auto_delivered=result.auto_delivered,
                manual_confirmation_required=result.manual_confirmation_required,
                error=result.error,
                notes=result.notes,
                command=result.command,
                log_path=result.log_path,
                payload_path=result.payload_path,
                pid=result.pid,
                run_id=result.run_id,
                metadata=result.metadata,
            )

        provider = self.config.get("providers", {}).get("qwen", {})
        runtime = provider.get("qwen", {})
        cli = runtime.get("cli") or "qwen"
        workspace_root = delivery_workspace_root(self.config, request.metadata)
        command = [cli, "-p", request.message]
        command.extend(["--approval-mode", str(runtime.get("approval_mode", "yolo"))])
        command.extend(["--output-format", str(runtime.get("output_format", "stream-json"))])
        if runtime.get("include_partial_messages", False):
            command.append("--include-partial-messages")
        if runtime.get("include_directories", True):
            command.extend(["--include-directories", str(workspace_root)])
        if runtime.get("channel"):
            command.extend(["--channel", str(runtime.get("channel"))])
        auth_type = str(runtime.get("auth_type") or "").strip()
        if auth_type:
            command.extend(["--auth-type", auth_type])
        model_name = request.metadata.get("model_preference") or _resolved_model(runtime)
        if model_name:
            command.extend(["--model", str(model_name)])
        if runtime.get("sandbox", False):
            command.append("--sandbox")
        for extra_arg in runtime.get("extra_args", []) or []:
            command.append(str(extra_arg))

        run_id = new_runtime_id("qwen")
        log_path = runtime_log_path("qwen", request.agent_id)
        runtime_paths = worker_runtime_paths(self.config, run_id)
        env = os.environ.copy()
        env.update(delivery_runtime_env(self.config, request.metadata))
        env.update(_runtime_env(runtime))
        process, _ = spawn_background_process(
            command,
            cwd=workspace_root,
            log_path=log_path,
            env=env,
            run_id=run_id,
            heartbeat_path=runtime_paths["heartbeat_path"],
            status_path=runtime_paths["status_path"],
        )

        return DeliveryResult(
            ok=True,
            adapter=self.name,
            mode="qwen",
            target=agent_config_for(self.config, request.agent_id).get("display_name", request.agent_id),
            auto_delivered=True,
            manual_confirmation_required=False,
            notes="Qwen Code CLI wake-up started in the background.",
            command=command,
            log_path=str(log_path),
            pid=process.pid,
            run_id=run_id,
            metadata={
                "shell_command": shell_quote(command),
                "model_preference": model_name,
                "env_keys": sorted(_runtime_env(runtime)),
                "heartbeat_path": str(runtime_paths["heartbeat_path"]),
                "runner_status_path": str(runtime_paths["status_path"]),
            },
        )
