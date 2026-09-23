from __future__ import annotations

from adapters.base import BaseAdapter, DeliveryCapability, DeliveryRequest, DeliveryResult
from common import (agent_config_for, delivery_runtime_env, delivery_workspace_root,
                    new_runtime_id, runtime_log_path, spawn_background_process, worker_runtime_paths)
import pi_runtime


class PiAdapter(BaseAdapter):
    name = "pi"

    def _profile(self, agent_id: str) -> dict:
        provider_id = agent_config_for(self.config, agent_id).get("provider") or agent_id
        return pi_runtime.settings(self.config, provider_id)

    def capability(self, agent_id: str) -> DeliveryCapability:
        installed = bool(pi_runtime.binary(self._profile(agent_id)))
        return DeliveryCapability(
            adapter=self.name, supported=installed, requires_manual_confirmation=not installed,
            can_auto_deliver=installed, can_auto_approve_edits=installed, delivery_mode=self.name,
            verified="partial" if installed else "unavailable", host="Pi CLI",
            notes="Pi runs inside the existing worker sandbox; delivery health checks model access."
                  if installed else "Configured Pi CLI is not installed.",
        )

    def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        profile = self._profile(request.agent_id)
        cli = pi_runtime.binary(profile)
        if not cli:
            return DeliveryResult(ok=False, adapter=self.name, mode=self.name, target=request.agent_id,
                                  auto_delivered=False, manual_confirmation_required=False,
                                  error="Configured Pi CLI is not installed.")
        workspace = delivery_workspace_root(self.config, request.metadata)
        env = pi_runtime.environment(profile)
        env.update(delivery_runtime_env(self.config, request.metadata))
        display_name = str(agent_config_for(self.config, request.agent_id).get("display_name") or request.agent_id)
        run_id = new_runtime_id("pi")
        env.update(AI_NAME=display_name, ORCH_AGENT_ID=request.agent_id, ORCH_PROVIDER=request.provider,
                   ORCH_RUN_ID=run_id)
        if request.task_id:
            env["ORCH_TASK_ID"] = request.task_id
        if request.reason:
            env["ORCH_REASON"] = request.reason
        argv = pi_runtime.command(cli, profile, request.message)
        log_path = runtime_log_path("pi", request.agent_id, config=self.config)
        paths = worker_runtime_paths(self.config, run_id)
        process, _ = spawn_background_process(
            argv, cwd=workspace, log_path=log_path, env=env, run_id=run_id,
            heartbeat_path=paths["heartbeat_path"], status_path=paths["status_path"],
        )
        return DeliveryResult(
            ok=True, adapter=self.name, mode=self.name, target=display_name, auto_delivered=True,
            manual_confirmation_required=False, command=argv, log_path=str(log_path),
            pid=process.pid, run_id=run_id,
            metadata={"heartbeat_path": str(paths["heartbeat_path"]),
                      "runner_status_path": str(paths["status_path"])},
        )
