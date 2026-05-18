from __future__ import annotations

import json
import os

from adapters.base import DeliveryCapability, DeliveryRequest, DeliveryResult
from adapters.claude_code import ClaudeCodeAdapter
from common import (
    agent_config_for,
    apply_claude_oauth_token_file,
    claude_auth_ready as shared_claude_auth_ready,
    config_path,
    delivery_runtime_env,
    delivery_workspace_root,
    preserve_github_cli_auth_env,
    new_runtime_id,
    runtime_log_path,
    shell_quote,
    spawn_background_process,
    command_exists,
    run_command,
    worker_runtime_paths,
)


def _provider_key(config: dict | None, agent_id: str | None = None, provider_id: str | None = None) -> str:
    if provider_id:
        return str(provider_id).strip() or "claude"
    if agent_id:
        agent = agent_config_for(config or {}, agent_id)
        return str(agent.get("provider") or agent.get("id") or agent_id).strip() or "claude"
    return "claude"


def _provider_settings(config: dict | None = None, provider_id: str | None = None) -> dict:
    providers = (config or {}).get("providers", {}) or {}
    key = _provider_key(config, provider_id=provider_id)
    return providers.get(key) or providers.get("claude") or {}


def _runtime_settings(config: dict | None = None, provider_id: str | None = None) -> dict:
    return _provider_settings(config, provider_id).get("runtime", {}) or {}


def _spawn_env(config: dict | None = None, provider_id: str | None = None) -> dict[str, str]:
    base_env = dict(os.environ)
    env = dict(base_env)
    runtime = _runtime_settings(config, provider_id)
    home = str(runtime.get("home") or "").strip()
    if home:
        env["HOME"] = os.path.expanduser(home)
    extra_env = runtime.get("env", {}) or {}
    for key, value in extra_env.items():
        if value is None:
            continue
        env[str(key)] = os.path.expanduser(str(value))
    preserve_github_cli_auth_env(env, base_env)
    apply_claude_oauth_token_file(env, runtime)
    return env


def _claude_auth_ready(
    cli: str | None,
    *,
    env: dict[str, str] | None = None,
    refresh_if_needed: bool = True,
) -> bool:
    return shared_claude_auth_ready(cli, env=env, refresh_if_needed=refresh_if_needed)


def _configured_claude_cli(config: dict | None = None, provider_id: str | None = None) -> str | None:
    runtime = _runtime_settings(config, provider_id)
    return command_exists(runtime.get("cli") or "claude")


def _allow_inbox_fallback(config: dict | None = None, provider_id: str | None = None) -> bool:
    provider = _provider_settings(config, provider_id)
    return bool(provider.get("allow_inbox_fallback", True))


class ClaudeCLIAdapter(ClaudeCodeAdapter):
    name = "claude_cli"

    def capability(self, agent_id: str) -> DeliveryCapability:
        provider_id = _provider_key(self.config, agent_id=agent_id)
        cli = _configured_claude_cli(self.config, provider_id)
        auth_ready = _claude_auth_ready(cli, env=_spawn_env(self.config, provider_id), refresh_if_needed=False)
        if cli and auth_ready:
            return DeliveryCapability(
                adapter=self.name,
                supported=True,
                requires_manual_confirmation=False,
                can_auto_deliver=True,
                can_auto_approve_edits=True,
                delivery_mode="claude_cli",
                verified="verified",
                host="Claude Code CLI",
                notes="Uses non-interactive Claude CLI sessions with the local approval broker hooks.",
            )
        missing_reason = "Claude CLI is not installed" if not cli else "Claude CLI is installed but not authenticated"
        if not _allow_inbox_fallback(self.config, provider_id):
            return DeliveryCapability(
                adapter=self.name,
                supported=bool(cli),
                requires_manual_confirmation=False,
                can_auto_deliver=False,
                can_auto_approve_edits=False,
                delivery_mode="claude_cli",
                verified="partial" if cli else "unavailable",
                host="Claude Code CLI",
                notes=f"{missing_reason}; inbox fallback is disabled for this provider.",
            )
        fallback = super().capability(agent_id)
        return DeliveryCapability(
            adapter=self.name,
            supported=fallback.supported,
            requires_manual_confirmation=True,
            can_auto_deliver=False,
            can_auto_approve_edits=fallback.can_auto_approve_edits,
            delivery_mode="file_inbox",
            verified="partial",
            host="Claude Code CLI + inbox fallback",
            notes=f"{missing_reason}, so delivery falls back to the workspace inbox path.",
        )

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        provider_id = _provider_key(self.config, agent_id=request.agent_id, provider_id=request.provider)
        cli = _configured_claude_cli(self.config, provider_id)
        env = _spawn_env(self.config, provider_id)
        auth_ready = _claude_auth_ready(cli, env=env)
        if not cli or not auth_ready:
            if not _allow_inbox_fallback(self.config, provider_id):
                reason = (
                    "Claude CLI is unavailable; inbox fallback is disabled for this provider."
                    if not cli
                    else "Claude CLI is not authenticated; inbox fallback is disabled for this provider."
                )
                return DeliveryResult(
                    ok=False,
                    adapter=self.name,
                    mode="claude_cli",
                    target=request.agent_id,
                    auto_delivered=False,
                    manual_confirmation_required=False,
                    error=reason,
                    notes=reason,
                )
            result = super().deliver(request)
            result.adapter = self.name
            result.mode = "file_inbox"
            if not cli:
                result.notes = f"{result.notes}. Claude CLI is unavailable, so inbox fallback was used."
            else:
                result.notes = f"{result.notes}. Claude CLI is not authenticated, so inbox fallback was used."
            return result

        provider = _provider_settings(self.config, provider_id)
        runtime = provider.get("runtime", {})
        workspace_root = delivery_workspace_root(self.config, request.metadata)
        output_format = runtime.get("output_format", "stream-json")
        command = [
            runtime.get("cli") or cli,
            "-p",
            request.message,
            "--output-format",
            output_format,
        ]
        if output_format == "stream-json":
            command.append("--verbose")
        if runtime.get("include_hook_events", True):
            command.append("--include-hook-events")

        provider_info = (
            (self.provider_capabilities or {}).get("providers", {}).get(provider_id)
            or (self.provider_capabilities or {}).get("providers", {}).get("claude", {})
        )
        if runtime.get("enable_auto_mode_if_supported", True) and provider_info.get("supports_auto_approve"):
            command.extend(["--permission-mode", runtime.get("auto_permission_mode", "auto")])
        else:
            command.extend(["--permission-mode", runtime.get("permission_mode", "acceptEdits")])

        mcp_config = runtime.get("mcp_config")
        if mcp_config:
            command.extend(["--mcp-config", str(config_path(self.config, "claude_mcp_config"))])

        run_id = new_runtime_id(provider_id)
        log_path = runtime_log_path(provider_id, request.agent_id)
        runtime_paths = worker_runtime_paths(self.config, run_id)
        env.update(delivery_runtime_env(self.config, request.metadata))
        env.update(
            {
                "ORCH_RUN_ID": run_id,
                "ORCH_TASK_ID": request.task_id or "",
                "ORCH_AGENT_ID": request.agent_id,
                "ORCH_PROVIDER": provider_id,
                "ORCH_REASON": request.reason or "",
                "ORCH_CONTEXT_FILES": "\n".join(request.context_files),
                "ORCH_TARGET_FILES": "\n".join(request.target_files),
            }
        )
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
            mode="claude_cli",
            target=request.agent_id,
            auto_delivered=True,
            manual_confirmation_required=False,
            notes="Claude CLI wake-up started in the background.",
            command=command,
            log_path=str(log_path),
            pid=process.pid,
            run_id=run_id,
            metadata={
                "shell_command": shell_quote(command),
                "heartbeat_path": str(runtime_paths["heartbeat_path"]),
                "runner_status_path": str(runtime_paths["status_path"]),
            },
        )
