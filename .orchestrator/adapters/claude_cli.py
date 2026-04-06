from __future__ import annotations

import os
import json

from adapters.base import DeliveryCapability, DeliveryRequest, DeliveryResult
from adapters.claude_code import ClaudeCodeAdapter
from common import (
    config_path,
    new_runtime_id,
    runtime_log_path,
    shell_quote,
    spawn_background_process,
    command_exists,
    run_command,
)


def _claude_auth_ready(cli: str | None) -> bool:
    if not cli:
        return False
    status = run_command([cli, "auth", "status"])
    if status.returncode != 0 or not status.stdout:
        return False
    try:
        payload = json.loads(status.stdout)
    except json.JSONDecodeError:
        return False
    return bool(payload.get("loggedIn"))


class ClaudeCLIAdapter(ClaudeCodeAdapter):
    name = "claude_cli"

    def capability(self, agent_id: str) -> DeliveryCapability:
        cli = command_exists("claude")
        if cli and _claude_auth_ready(cli):
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
        fallback = super().capability(agent_id)
        missing_reason = "Claude CLI is not installed" if not cli else "Claude CLI is installed but not authenticated"
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
        cli = command_exists("claude")
        auth_ready = _claude_auth_ready(cli)
        if not cli or not auth_ready:
            result = super().deliver(request)
            result.adapter = self.name
            result.mode = "file_inbox"
            if not cli:
                result.notes = f"{result.notes}. Claude CLI is unavailable, so inbox fallback was used."
            else:
                result.notes = f"{result.notes}. Claude CLI is not authenticated, so inbox fallback was used."
            return result

        provider = self.config.get("providers", {}).get("claude", {})
        runtime = provider.get("runtime", {})
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

        provider_info = (self.provider_capabilities or {}).get("providers", {}).get("claude", {})
        if runtime.get("enable_auto_mode_if_supported", True) and provider_info.get("supports_auto_approve"):
            command.extend(["--permission-mode", runtime.get("auto_permission_mode", "auto")])
        else:
            command.extend(["--permission-mode", runtime.get("permission_mode", "acceptEdits")])

        mcp_config = runtime.get("mcp_config")
        if mcp_config:
            command.extend(["--mcp-config", str(config_path(self.config, "claude_mcp_config"))])

        run_id = new_runtime_id("claude")
        log_path = runtime_log_path("claude", request.agent_id)
        env = os.environ.copy()
        env.update(
            {
                "ORCH_RUN_ID": run_id,
                "ORCH_TASK_ID": request.task_id or "",
                "ORCH_AGENT_ID": request.agent_id,
                "ORCH_REASON": request.reason or "",
                "ORCH_CONTEXT_FILES": "\n".join(request.context_files),
                "ORCH_TARGET_FILES": "\n".join(request.target_files),
            }
        )
        process, _ = spawn_background_process(
            command,
            cwd=config_path(self.config, "status_file").parents[0],
            log_path=log_path,
            env=env,
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
            metadata={"shell_command": shell_quote(command)},
        )
