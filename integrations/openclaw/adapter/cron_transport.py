from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from .gateway_runtime import OpenClawDockerGatewayRuntime, OpenClawGatewayTransportError

_TERMINAL_RUN_STATUSES = {"ok", "failed", "cancelled", "canceled", "error", "timed_out"}


def build_system_event_text(request: dict[str, Any]) -> str:
    payload = {
        "kind": "pantheon.workflow.dispatch",
        "prepared_at": request["prepared_at"],
        "request_id": request["request_id"],
        "workflow_id": request["workflow"]["workflow_id"],
        "upstream_entrypoint": request["workflow"]["upstream_entrypoint"],
        "policy_id": request["governance"]["policy_id"],
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _job_name(workflow_id: str, request_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", workflow_id.lower()).strip("-")
    suffix = request_id.split("-", 1)[-1][:12]
    return f"pantheon-{slug}-{suffix}"


def _future_schedule_at(prepared_at: str) -> str:
    prepared = datetime.fromisoformat(prepared_at.replace("Z", "+00:00"))
    scheduled = prepared + timedelta(minutes=10)
    return scheduled.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class OpenClawCronGatewayTransport:
    """Dispatch Pantheon workflow envelopes through the pinned OpenClaw cron RPC."""

    def __init__(
        self,
        runtime: OpenClawDockerGatewayRuntime,
        *,
        session_target: str = "main",
        wake_mode: str = "next-heartbeat",
        delivery_mode: str = "none",
        delete_after_run: bool = True,
        poll_timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 1.0,
    ):
        self.runtime = runtime
        self.session_target = session_target
        self.wake_mode = wake_mode
        self.delivery_mode = delivery_mode
        self.delete_after_run = delete_after_run
        self.poll_timeout_seconds = poll_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        gateway_probe = self.runtime.probe()
        event_text = build_system_event_text(request)
        workflow_id = request["workflow"]["workflow_id"]
        job_name = _job_name(workflow_id, request["request_id"])
        schedule_at = _future_schedule_at(request["prepared_at"])

        add_response = self.runtime.gateway_call(
            "cron.add",
            {
                "name": job_name,
                "enabled": True,
                "deleteAfterRun": self.delete_after_run,
                "schedule": {"kind": "at", "at": schedule_at},
                "sessionTarget": self.session_target,
                "wakeMode": self.wake_mode,
                "payload": {"kind": "systemEvent", "text": event_text},
                "delivery": {"mode": self.delivery_mode},
            },
        )
        job_id = add_response.get("id")
        if not job_id:
            raise OpenClawGatewayTransportError(
                "OpenClaw cron.add did not return a job id.",
                error_code="WORKFLOW_TRIGGER_FAILED",
                error_layer="known",
                retryable=False,
                raw_payload={"add_response": add_response},
            )

        run_response = self.runtime.gateway_call("cron.run", {"id": job_id, "mode": "force"})
        run_id = run_response.get("runId")
        latest_run, runs_response = self._wait_for_terminal_run(job_id=job_id, run_id=run_id)

        if latest_run.get("status") != "ok":
            raise OpenClawGatewayTransportError(
                f"OpenClaw cron job {job_id} finished with status {latest_run.get('status')!r}.",
                error_code="WORKFLOW_TRIGGER_FAILED",
                error_layer="known",
                retryable=False,
                raw_payload={
                    "job_id": job_id,
                    "run_id": run_id,
                    "latest_run": latest_run,
                    "runs_response": runs_response,
                },
            )

        return {
            "status": "accepted",
            "mode": "gateway_rpc",
            "workflow_id": workflow_id,
            "request_id": request["request_id"],
            "runtime_pin": request["runtime"],
            "gateway": {
                "container_name": self.runtime.config.container_name,
                "http_base_url": self.runtime.config.http_base_url,
                "ws_url": self.runtime.config.ws_url,
                "probe": gateway_probe,
            },
            "job": {
                "id": job_id,
                "name": job_name,
                "run_id": run_id,
                "wake_mode": self.wake_mode,
                "delivery_mode": self.delivery_mode,
            },
            "run": latest_run,
            "pantheon_event_text": event_text,
            "raw_upstream": {
                "add_response": add_response,
                "run_response": run_response,
                "runs_response": runs_response,
            },
        }

    def _wait_for_terminal_run(self, *, job_id: str, run_id: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
        deadline = time.time() + self.poll_timeout_seconds
        latest_runs: dict[str, Any] = {}
        while time.time() < deadline:
            latest_runs = self.runtime.gateway_call("cron.runs", {"id": job_id, "limit": 5})
            entries = latest_runs.get("entries") or []
            latest_run = self._select_run(entries, run_id=run_id)
            if latest_run and latest_run.get("status") in _TERMINAL_RUN_STATUSES:
                return latest_run, latest_runs
            time.sleep(self.poll_interval_seconds)

        raise OpenClawGatewayTransportError(
            f"Timed out while waiting for OpenClaw cron job {job_id} to reach a terminal state.",
            error_code="TIMEOUT",
            error_layer="known",
            retryable=True,
            raw_payload={
                "job_id": job_id,
                "run_id": run_id,
                "runs_response": latest_runs,
            },
        )

    @staticmethod
    def _select_run(entries: list[dict[str, Any]], *, run_id: str | None) -> dict[str, Any] | None:
        if not entries:
            return None
        if run_id:
            for entry in entries:
                if entry.get("runId") == run_id:
                    return entry
        return entries[0]
