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
    runtime_log_path,
    spawn_background_process,
    worker_runtime_paths,
)


GEMINI_SETTINGS_PATH = Path.home() / ".gemini" / "settings.json"
GEMINI_OAUTH_CREDS_PATH = Path.home() / ".gemini" / "oauth_creds.json"


def _provider_key(config: dict | None, agent_id: str | None = None, provider_id: str | None = None) -> str:
    if provider_id:
        return str(provider_id).strip() or "gemini"
    if agent_id:
        agent = agent_config_for(config or {}, agent_id)
        return str(agent.get("provider") or agent.get("id") or agent_id).strip() or "gemini"
    return "gemini"


def _provider_settings(config: dict | None = None, provider_id: str | None = None) -> dict:
    providers = (config or {}).get("providers", {}) or {}
    key = _provider_key(config, provider_id=provider_id)
    return providers.get(key) or providers.get("gemini") or {}


def _provider_env(config: dict | None = None, provider_id: str | None = None) -> dict[str, str]:
    provider = _provider_settings(config, provider_id)
    env: dict[str, str] = {}
    for block_name in ("runtime", "gemini"):
        block = provider.get(block_name, {}) or {}
        for key, value in (block.get("env", {}) or {}).items():
            if value is None:
                continue
            env[str(key)] = os.path.expanduser(str(value))
    env.setdefault("GEMINI_CLI_TRUST_WORKSPACE", "true")
    return env


def _gemini_home(config: dict | None = None, provider_id: str | None = None) -> Path:
    provider = _provider_settings(config, provider_id)
    runtime = provider.get("gemini", {})
    home = str(runtime.get("config_home") or runtime.get("home") or "").strip()
    return Path(os.path.expanduser(home)) if home else Path.home()


def _gemini_settings_path(config: dict | None = None, provider_id: str | None = None) -> Path:
    return _gemini_home(config, provider_id) / ".gemini" / "settings.json"


def _gemini_oauth_creds_path(config: dict | None = None, provider_id: str | None = None) -> Path:
    return _gemini_home(config, provider_id) / ".gemini" / "oauth_creds.json"


def _configured_gemini_cli(config: dict | None = None, provider_id: str | None = None) -> str | None:
    provider = _provider_settings(config, provider_id)
    runtime = provider.get("gemini", {})
    return command_exists(runtime.get("cli") or "gemini")


def _allow_inbox_fallback(config: dict | None = None, provider_id: str | None = None) -> bool:
    provider = _provider_settings(config, provider_id)
    return bool(provider.get("allow_inbox_fallback", True))


def _truthy_env(name: str, env: dict[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    return source.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _gemini_settings(config: dict | None = None, provider_id: str | None = None) -> dict:
    return load_json(_gemini_settings_path(config, provider_id), default={}) or {}


def _gemini_selected_auth_type(
    config: dict | None = None,
    provider_id: str | None = None,
    env: dict[str, str] | None = None,
) -> str | None:
    env = env or {**os.environ, **_provider_env(config, provider_id)}
    if _truthy_env("GOOGLE_GENAI_USE_GCA", env):
        return "oauth-personal"
    if _truthy_env("GEMINI_CLI_USE_COMPUTE_ADC", env):
        return "compute-default-credentials"
    if _truthy_env("GOOGLE_GENAI_USE_VERTEXAI", env):
        return "vertex-ai"
    if env.get("GEMINI_API_KEY"):
        return "gemini-api-key"
    settings = _gemini_settings(config, provider_id)
    return settings.get("security", {}).get("auth", {}).get("selectedType") or (
        "oauth-personal" if _gemini_oauth_creds_path(config, provider_id).exists() else None
    )


def _gemini_auth_ready(config: dict | None = None, provider_id: str | None = None) -> bool:
    env = {**os.environ, **_provider_env(config, provider_id)}
    auth_type = _gemini_selected_auth_type(config, provider_id, env)
    if auth_type == "oauth-personal":
        return _gemini_oauth_creds_path(config, provider_id).exists()
    if auth_type == "gemini-api-key":
        return bool(env.get("GEMINI_API_KEY"))
    if auth_type == "vertex-ai":
        return bool(
            env.get("GOOGLE_API_KEY")
            or (env.get("GOOGLE_CLOUD_PROJECT") and env.get("GOOGLE_CLOUD_LOCATION"))
        )
    if auth_type == "compute-default-credentials":
        return bool(env.get("GOOGLE_APPLICATION_CREDENTIALS") or command_exists("gcloud"))
    return False


class GeminiAdapter(BaseAdapter):
    name = "gemini"

    def capability(self, agent_id: str) -> DeliveryCapability:
        provider_id = _provider_key(self.config, agent_id=agent_id)
        allow_inbox_fallback = _allow_inbox_fallback(self.config, provider_id)
        cli = _configured_gemini_cli(self.config, provider_id)
        auth_ready = _gemini_auth_ready(self.config, provider_id)
        supported = bool(cli and auth_ready)
        if cli and auth_ready:
            notes = "Uses the verified Gemini CLI `--prompt`, local auth config, and approval mode settings."
        elif cli:
            notes = "Gemini CLI is installed but not authenticated for non-interactive use."
        else:
            notes = "Gemini CLI is not installed."
        if not supported and not allow_inbox_fallback:
            notes = f"{notes} Inbox fallback is disabled for this provider."
        return DeliveryCapability(
            adapter=self.name,
            supported=bool(cli),
            requires_manual_confirmation=bool(not supported and allow_inbox_fallback),
            can_auto_deliver=supported,
            can_auto_approve_edits=supported,
            delivery_mode="gemini" if (supported or not allow_inbox_fallback) else "file_inbox",
            verified="verified" if supported else ("partial" if cli else "unavailable"),
            host="Gemini CLI" if (cli or not allow_inbox_fallback) else "Gemini CLI + inbox fallback",
            notes=notes,
        )

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        provider_id = _provider_key(self.config, agent_id=request.agent_id, provider_id=request.provider)
        capability = self.capability(request.agent_id)
        if not capability.supported or not capability.can_auto_deliver:
            if not _allow_inbox_fallback(self.config, provider_id):
                reason = capability.notes or "Gemini auto-delivery is unavailable and inbox fallback is disabled."
                return DeliveryResult(
                    ok=False,
                    adapter=self.name,
                    mode="gemini",
                    target=agent_config_for(self.config, request.agent_id).get("display_name", request.agent_id),
                    auto_delivered=False,
                    manual_confirmation_required=False,
                    error=reason,
                    notes=reason,
                )
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

        provider = _provider_settings(self.config, provider_id)
        gemini_settings = provider.get("gemini", {})
        approval = provider.get("approval", {})
        cli = _configured_gemini_cli(self.config, provider_id) or gemini_settings.get("cli") or "gemini"
        agent_cfg = agent_config_for(self.config, request.agent_id)
        display_name = str(agent_cfg.get("display_name") or request.agent_id)
        workspace_root = delivery_workspace_root(self.config, request.metadata)
        command = [cli]
        model = str(gemini_settings.get("model") or "").strip()
        if model:
            command.extend(["--model", model])
        output_format = str(gemini_settings.get("output_format") or "").strip()
        if output_format:
            command.extend(["--output-format", output_format])
        command.extend(["--prompt", request.message])
        approval_mode = approval.get("default_approval_mode")
        if approval_mode:
            command.extend(["--approval-mode", approval_mode])
        include_directories = gemini_settings.get("include_directories")
        if include_directories:
            root = workspace_root
            paths = [str(root)] if include_directories is True else include_directories
            if isinstance(paths, (str, os.PathLike)):
                paths = [paths]
            for path in paths:
                expanded = Path(os.path.expanduser(str(path)))
                command.extend(["--include-directories", str(expanded if expanded.is_absolute() else root / expanded)])

        spawn_env: dict[str, str] = dict(os.environ)
        spawn_env.update(delivery_runtime_env(self.config, request.metadata))
        spawn_env.update(_provider_env(self.config, provider_id))
        spawn_env["AI_NAME"] = display_name
        spawn_env["ORCH_AGENT_ID"] = request.agent_id
        spawn_env["ORCH_PROVIDER"] = provider_id
        gemini_home = _gemini_home(self.config, provider_id)
        if gemini_home != Path.home():
            spawn_env["GEMINI_CLI_HOME"] = str(gemini_home)
        if request.task_id:
            spawn_env["ORCH_TASK_ID"] = request.task_id
        if request.reason:
            spawn_env["ORCH_REASON"] = request.reason

        run_id = new_runtime_id(provider_id)
        log_path = runtime_log_path(provider_id, request.agent_id)
        runtime_paths = worker_runtime_paths(self.config, run_id)
        process, _ = spawn_background_process(
            command,
            cwd=workspace_root,
            log_path=log_path,
            env=spawn_env,
            run_id=run_id,
            heartbeat_path=runtime_paths["heartbeat_path"],
            status_path=runtime_paths["status_path"],
        )

        return DeliveryResult(
            ok=True,
            adapter=self.name,
            mode="gemini",
            target=display_name,
            auto_delivered=True,
            manual_confirmation_required=False,
            notes="Gemini CLI wake-up started in the background.",
            command=command,
            log_path=str(log_path),
            pid=process.pid,
            run_id=run_id,
            metadata={
                "heartbeat_path": str(runtime_paths["heartbeat_path"]),
                "runner_status_path": str(runtime_paths["status_path"]),
            },
        )
