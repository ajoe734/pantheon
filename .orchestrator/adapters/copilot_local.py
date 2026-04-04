from __future__ import annotations

import os

from adapters.base import BaseAdapter, DeliveryCapability, DeliveryRequest, DeliveryResult
from adapters.file_inbox import FileInboxAdapter
from common import (
    command_exists,
    config_path,
    new_runtime_id,
    run_command,
    runtime_log_path,
    shell_quote,
    spawn_background_process,
)


def _gh_auth_token() -> str | None:
    gh = command_exists("gh")
    if not gh:
        return None
    result = run_command([gh, "auth", "token"])
    token = (result.stdout or "").strip()
    return token or None


def _copilot_auth_ready() -> bool:
    for env_name in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(env_name):
            return True
    return bool(_gh_auth_token())


class CopilotLocalAdapter(BaseAdapter):
    name = "copilot_local"

    def capability(self, agent_id: str) -> DeliveryCapability:
        cli = command_exists("copilot")
        if cli and _copilot_auth_ready():
            return DeliveryCapability(
                adapter=self.name,
                supported=True,
                requires_manual_confirmation=False,
                can_auto_deliver=True,
                can_auto_approve_edits=True,
                delivery_mode="copilot_local",
                verified="verified",
                host="Copilot CLI + VS Code workspace link",
                notes="Uses Copilot CLI autopilot in the current WSL workspace.",
            )
        missing_reason = "Copilot CLI is not installed" if not cli else "Copilot CLI is installed but not authenticated"
        return DeliveryCapability(
            adapter=self.name,
            supported=True,
            requires_manual_confirmation=True,
            can_auto_deliver=False,
            can_auto_approve_edits=False,
            delivery_mode="file_inbox",
            verified="partial",
            host="Copilot CLI + inbox fallback",
            notes=f"{missing_reason}, so delivery falls back to a workspace inbox file.",
        )

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        cli = command_exists("copilot")
        auth_ready = _copilot_auth_ready()
        if not cli or not auth_ready:
            fallback = FileInboxAdapter(config=self.config, provider_capabilities=self.provider_capabilities)
            result = fallback.deliver(request)
            result.adapter = self.name
            result.mode = "file_inbox"
            if not cli:
                result.notes = f"{result.notes}. Copilot CLI is unavailable, so inbox fallback was used."
            else:
                result.notes = f"{result.notes}. Copilot CLI is not authenticated, so inbox fallback was used."
            return result

        provider = self.config.get("providers", {}).get("copilot", {})
        local = provider.get("local", {})
        command = [local.get("cli") or cli]
        if local.get("autopilot", True):
            command.append("--autopilot")
        command.extend(["-p", request.message])
        max_autopilot = local.get("max_autopilot_continues")
        if max_autopilot:
            command.extend(["--max-autopilot-continues", str(max_autopilot)])
        if local.get("allow_all_tools", False):
            command.append("--allow-all-tools")
        if local.get("add_workspace_dir", True):
            command.extend(["--add-dir", str(config_path(self.config, "status_file").parents[0])])
        if local.get("no_ask_user", True):
            command.append("--no-ask-user")
        for tool in local.get("allow_tools", []) or []:
            command.extend(["--allow-tool", tool])
        for tool in local.get("deny_tools", []) or []:
            command.extend(["--deny-tool", tool])
        model_preference = request.metadata.get("model_preference")
        if model_preference:
            command.extend(["--model", str(model_preference)])
        for extra_arg in local.get("extra_args", []) or []:
            command.append(str(extra_arg))

        run_id = new_runtime_id("copilot")
        log_path = runtime_log_path("copilot", request.agent_id)
        env = os.environ.copy()
        if not any(env.get(name) for name in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")):
            gh_token = _gh_auth_token()
            if gh_token:
                env["GH_TOKEN"] = gh_token
        env.update(
            {
                "ORCH_RUN_ID": run_id,
                "ORCH_TASK_ID": request.task_id or "",
                "ORCH_AGENT_ID": request.agent_id,
                "ORCH_PROVIDER": "copilot",
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
            mode="copilot_local",
            target=request.agent_id,
            auto_delivered=True,
            manual_confirmation_required=False,
            notes="Copilot CLI autopilot wake-up started in the background.",
            command=command,
            log_path=str(log_path),
            pid=process.pid,
            run_id=run_id,
            metadata={"shell_command": shell_quote(command), "model_preference": model_preference},
        )
