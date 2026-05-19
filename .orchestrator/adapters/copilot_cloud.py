from __future__ import annotations

import os
import re
from pathlib import Path

from adapters.base import BaseAdapter, DeliveryCapability, DeliveryRequest, DeliveryResult
from common import (
    command_exists,
    config_path,
    delivery_runtime_env,
    new_runtime_id,
    run_command,
    runtime_log_path,
    shell_quote,
    spawn_background_process,
    worker_runtime_paths,
)


def _configured_gh_cli(config: dict | None = None) -> str | None:
    provider = ((config or {}).get("providers", {}).get("copilot", {}) or {})
    runtime = provider.get("cloud", {})
    return command_exists(runtime.get("cli") or "gh")


def _parse_version(text: str) -> tuple[int, ...]:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        return ()
    return tuple(int(part) for part in match.groups())


def _gh_auth_token(binary: str | None) -> str | None:
    if not binary:
        return None
    result = run_command([binary, "auth", "token"])
    token = (result.stdout or "").strip()
    return token or None


def _repo_slug(root: Path) -> str | None:
    remote = run_command(["git", "config", "--get", "remote.origin.url"], cwd=root)
    value = (remote.stdout or "").strip()
    if not value:
        return None
    match = re.search(r"[:/]([^/:]+/[^/.]+?)(?:\.git)?$", value)
    return match.group(1) if match else None


class CopilotCloudAdapter(BaseAdapter):
    name = "copilot_cloud"

    def capability(self, agent_id: str) -> DeliveryCapability:
        gh = _configured_gh_cli(self.config)
        if not gh:
            return DeliveryCapability(
                adapter=self.name,
                supported=False,
                requires_manual_confirmation=True,
                can_auto_deliver=False,
                can_auto_approve_edits=False,
                delivery_mode="copilot_cloud",
                verified="unavailable",
                host="GitHub CLI coding agent",
                notes="`gh` is not installed, so GitHub cloud agent submission is unavailable.",
            )
        version = _parse_version((run_command([gh, "--version"]).stdout or ""))
        min_version = (2, 80, 0)
        if version and version < min_version:
            return DeliveryCapability(
                adapter=self.name,
                supported=False,
                requires_manual_confirmation=True,
                can_auto_deliver=False,
                can_auto_approve_edits=False,
                delivery_mode="copilot_cloud",
                verified="partial",
                host="GitHub CLI coding agent",
                notes=f"`gh` is installed but too old for cloud agent support ({'.'.join(map(str, version))}).",
            )
        supported = bool(_gh_auth_token(gh))
        return DeliveryCapability(
            adapter=self.name,
            supported=supported,
            requires_manual_confirmation=not supported,
            can_auto_deliver=supported,
            can_auto_approve_edits=False,
            delivery_mode="copilot_cloud",
            verified="verified" if supported else "partial",
            host="GitHub CLI coding agent",
            notes="Uses `gh agent-task create` when GitHub auth and repo context are available."
            if supported
            else "`gh` is installed but `gh auth status` is not ready for cloud agent submission.",
        )

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        capability = self.capability(request.agent_id)
        if not capability.supported:
            return DeliveryResult(
                ok=False,
                adapter=self.name,
                mode="copilot_cloud",
                target=request.agent_id,
                auto_delivered=False,
                manual_confirmation_required=True,
                error=capability.notes,
                notes=capability.notes,
            )

        provider = self.config.get("providers", {}).get("copilot", {})
        cloud = provider.get("cloud", {})
        root = config_path(self.config, "status_file").parents[0]
        repo = cloud.get("repo") or _repo_slug(root)
        if not repo:
            return DeliveryResult(
                ok=False,
                adapter=self.name,
                mode="copilot_cloud",
                target=request.agent_id,
                auto_delivered=False,
                manual_confirmation_required=True,
                error="Unable to derive the GitHub repository slug for cloud agent submission.",
                notes="Set `providers.copilot.cloud.repo` in config.local.json or configure `remote.origin.url`.",
            )

        gh = _configured_gh_cli(self.config) or "gh"
        command = [gh, "agent-task", "create", "--repo", repo]
        base_branch = cloud.get("base_branch")
        if base_branch:
            command.extend(["--base", base_branch])
        if cloud.get("follow", False):
            command.append("--follow")
        for extra_arg in cloud.get("extra_args", []) or []:
            command.append(str(extra_arg))
        command.append(request.message)

        run_id = new_runtime_id("copilot-cloud")
        log_path = runtime_log_path("copilot-cloud", request.agent_id)
        runtime_paths = worker_runtime_paths(self.config, run_id)
        env = os.environ.copy()
        env.update(delivery_runtime_env(self.config, request.metadata))
        env.update(
            {
                "ORCH_RUN_ID": run_id,
                "ORCH_TASK_ID": request.task_id or "",
                "ORCH_AGENT_ID": request.agent_id,
                "ORCH_PROVIDER": "copilot_cloud",
            }
        )
        process, _ = spawn_background_process(
            command,
            cwd=root,
            log_path=log_path,
            env=env,
            run_id=run_id,
            heartbeat_path=runtime_paths["heartbeat_path"],
            status_path=runtime_paths["status_path"],
        )
        return DeliveryResult(
            ok=True,
            adapter=self.name,
            mode="copilot_cloud",
            target=request.agent_id,
            auto_delivered=True,
            manual_confirmation_required=False,
            notes="GitHub cloud agent submission started in the background.",
            command=command,
            log_path=str(log_path),
            pid=process.pid,
            run_id=run_id,
            metadata={
                "shell_command": shell_quote(command),
                "repo": repo,
                "heartbeat_path": str(runtime_paths["heartbeat_path"]),
                "runner_status_path": str(runtime_paths["status_path"]),
            },
        )
