from __future__ import annotations

import os

from adapters.base import BaseAdapter, DeliveryCapability, DeliveryRequest, DeliveryResult
from common import (
    agent_config_for,
    command_exists,
    delivery_runtime_env,
    delivery_workspace_root,
    new_runtime_id,
    runtime_log_path,
    spawn_background_process,
    worker_runtime_paths,
)


CODEX_INHERITED_SESSION_ENV = (
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "CODEX_CONVERSATION_ID",
    "CODEX_PARENT_THREAD_ID",
)


class CodexAdapter(BaseAdapter):
    name = "codex"

    def _provider_settings(self, agent_id: str) -> tuple[dict, dict]:
        """Return (provider_block, codex_settings) for the given agent_id.

        Looks up the provider key from the agent config first, then falls back
        to the literal agent_id, then to "codex".  This lets codex2 / codex3
        carry their own provider blocks with separate api_key_env values.
        """
        agent_cfg = agent_config_for(self.config, agent_id)
        provider_key = agent_cfg.get("provider") or agent_id or "codex"
        provider = (
            self.config.get("providers", {}).get(provider_key)
            or self.config.get("providers", {}).get("codex")
            or {}
        )
        codex_settings = provider.get("codex", {})
        return provider, codex_settings

    def capability(self, agent_id: str) -> DeliveryCapability:
        _provider, codex_settings = self._provider_settings(agent_id)
        configured_cli = codex_settings.get("cli") or "codex"
        cli = command_exists(configured_cli) or command_exists("codex")
        supported = bool(cli)
        return DeliveryCapability(
            adapter=self.name,
            supported=supported,
            requires_manual_confirmation=not supported,
            can_auto_deliver=supported,
            can_auto_approve_edits=supported,
            delivery_mode="codex",
            verified="verified" if supported else "unavailable",
            host="Codex CLI",
            notes="Uses verified Codex CLI approval flags for orchestrated runs." if supported else "Codex CLI is not installed.",
        )

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        capability = self.capability(request.agent_id)
        if not capability.supported:
            return DeliveryResult(
                ok=False,
                adapter=self.name,
                mode="codex",
                target=request.agent_id,
                auto_delivered=False,
                manual_confirmation_required=True,
                error=capability.notes,
                notes=capability.notes,
            )

        _provider, codex_settings = self._provider_settings(request.agent_id)
        agent_cfg = agent_config_for(self.config, request.agent_id)
        display_name = str(agent_cfg.get("display_name") or request.agent_id)
        cli = codex_settings.get("cli") or "codex"
        workspace_root = delivery_workspace_root(self.config, request.metadata)
        command = [
            cli,
            "exec",
            "-C",
            str(workspace_root),
            "-c",
            f'ask_for_approval="{codex_settings.get("ask_for_approval", "never")}"',
            "-s",
            codex_settings.get("sandbox_mode", "workspace-write"),
            "--skip-git-repo-check",
        ]
        if codex_settings.get("dangerously_bypass"):
            command.append("--dangerously-bypass-approvals-and-sandbox")
        command.append(request.message)

        # Build env: inherit current environment, then apply overrides.
        spawn_env: dict[str, str] = dict(os.environ)
        spawn_env.update(delivery_runtime_env(self.config, request.metadata))
        for key in CODEX_INHERITED_SESSION_ENV:
            spawn_env.pop(key, None)
        spawn_env["AI_NAME"] = display_name
        spawn_env["ORCH_AGENT_ID"] = request.agent_id
        spawn_env["ORCH_PROVIDER"] = request.provider
        if request.task_id:
            spawn_env["ORCH_TASK_ID"] = request.task_id
        if request.reason:
            spawn_env["ORCH_REASON"] = request.reason

        api_key_env = codex_settings.get("api_key_env", "").strip()
        codex_home = codex_settings.get("codex_home", "").strip()

        if api_key_env:
            if api_key_env != "OPENAI_API_KEY":
                api_key_value = os.environ.get(api_key_env, "")
                if api_key_value:
                    spawn_env["OPENAI_API_KEY"] = api_key_value
        else:
            spawn_env.pop("OPENAI_API_KEY", None)
        if codex_home:
            spawn_env["CODEX_HOME"] = os.path.expanduser(codex_home)

        run_id = new_runtime_id("codex")
        spawn_env["ORCH_RUN_ID"] = run_id
        log_path = runtime_log_path("codex", request.agent_id)
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
            mode="codex",
            target=display_name,
            auto_delivered=True,
            manual_confirmation_required=False,
            notes="Codex CLI wake-up started in the background.",
            command=command,
            log_path=str(log_path),
            pid=process.pid,
            run_id=run_id,
            metadata={
                "heartbeat_path": str(runtime_paths["heartbeat_path"]),
                "runner_status_path": str(runtime_paths["status_path"]),
            },
        )
