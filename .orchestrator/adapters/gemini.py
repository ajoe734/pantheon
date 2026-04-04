from __future__ import annotations

import os
from pathlib import Path

from adapters.base import BaseAdapter, DeliveryCapability, DeliveryRequest, DeliveryResult
from adapters.file_inbox import FileInboxAdapter
from common import (
    agent_config_for,
    command_exists,
    config_path,
    load_json,
    new_runtime_id,
    runtime_log_path,
    spawn_background_process,
)


GEMINI_SETTINGS_PATH = Path.home() / ".gemini" / "settings.json"
GEMINI_OAUTH_CREDS_PATH = Path.home() / ".gemini" / "oauth_creds.json"


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _gemini_settings() -> dict:
    return load_json(GEMINI_SETTINGS_PATH, default={}) or {}


def _gemini_selected_auth_type() -> str | None:
    if _truthy_env("GOOGLE_GENAI_USE_GCA"):
        return "oauth-personal"
    if _truthy_env("GEMINI_CLI_USE_COMPUTE_ADC"):
        return "compute-default-credentials"
    if _truthy_env("GOOGLE_GENAI_USE_VERTEXAI"):
        return "vertex-ai"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini-api-key"
    settings = _gemini_settings()
    return settings.get("security", {}).get("auth", {}).get("selectedType") or (
        "oauth-personal" if GEMINI_OAUTH_CREDS_PATH.exists() else None
    )


def _gemini_auth_ready() -> bool:
    auth_type = _gemini_selected_auth_type()
    if auth_type == "oauth-personal":
        return GEMINI_OAUTH_CREDS_PATH.exists()
    if auth_type == "gemini-api-key":
        return bool(os.environ.get("GEMINI_API_KEY"))
    if auth_type == "vertex-ai":
        return bool(
            os.environ.get("GOOGLE_API_KEY")
            or (os.environ.get("GOOGLE_CLOUD_PROJECT") and os.environ.get("GOOGLE_CLOUD_LOCATION"))
        )
    if auth_type == "compute-default-credentials":
        return bool(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or command_exists("gcloud"))
    return False


class GeminiAdapter(BaseAdapter):
    name = "gemini"

    def capability(self, agent_id: str) -> DeliveryCapability:
        cli = command_exists("gemini")
        auth_ready = _gemini_auth_ready()
        supported = bool(cli and auth_ready)
        if cli and auth_ready:
            notes = "Uses the verified Gemini CLI `--prompt`, local auth config, and approval mode settings."
        elif cli:
            notes = "Gemini CLI is installed but not authenticated for non-interactive use, so delivery falls back to inbox."
        else:
            notes = "Gemini CLI is not installed."
        return DeliveryCapability(
            adapter=self.name,
            supported=bool(cli),
            requires_manual_confirmation=not supported,
            can_auto_deliver=supported,
            can_auto_approve_edits=supported,
            delivery_mode="gemini" if supported else "file_inbox",
            verified="verified" if supported else ("partial" if cli else "unavailable"),
            host="Gemini CLI" if cli else "Gemini CLI + inbox fallback",
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

        provider = self.config.get("providers", {}).get("gemini", {})
        gemini_settings = provider.get("gemini", {})
        approval = provider.get("approval", {})
        cli = gemini_settings.get("cli") or "gemini"
        command = [cli, "--prompt", request.message]
        approval_mode = approval.get("default_approval_mode")
        if approval_mode:
            command.extend(["--approval-mode", approval_mode])
        if gemini_settings.get("include_directories"):
            command.extend(["--include-directories", str(config_path(self.config, "status_file").parents[0])])

        run_id = new_runtime_id("gemini")
        log_path = runtime_log_path("gemini", request.agent_id)
        process, _ = spawn_background_process(
            command,
            cwd=config_path(self.config, "status_file").parents[0],
            log_path=log_path,
        )

        return DeliveryResult(
            ok=True,
            adapter=self.name,
            mode="gemini",
            target=agent_config_for(self.config, request.agent_id).get("display_name", request.agent_id),
            auto_delivered=True,
            manual_confirmation_required=False,
            notes="Gemini CLI wake-up started in the background.",
            command=command,
            log_path=str(log_path),
            pid=process.pid,
            run_id=run_id,
        )
