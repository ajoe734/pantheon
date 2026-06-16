"""PersonaCronRegistrar — register WORKFLOW_CATALOG as recurring OpenClaw cron jobs.

One set of four cron jobs is registered per active persona/binding.  The
gateway schedules them using the canonical cron expressions from each
WorkflowDefinition so OpenClaw drives the OODA loop on the correct cadence.
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models import utc_now
from workflows import WORKFLOW_CATALOG, WorkflowDefinition


def _repo_root() -> str:
    return str(Path(__file__).resolve().parents[3])


def _ensure_adapter_path() -> None:
    root = _repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)


def _job_name(workflow_id: str, persona_id: str) -> str:
    wf_slug = re.sub(r"[^a-z0-9]+", "-", workflow_id.lower()).strip("-")
    persona_slug = re.sub(r"[^a-z0-9]+", "-", persona_id.lower()).strip("-")[:16]
    return f"pantheon-{wf_slug}-{persona_slug}"


@dataclass
class PersonaCronJobRecord:
    workflow_id: str
    job_name: str
    job_id: str
    schedule: str
    registered_at: str


@dataclass
class PersonaCronRegistrationResult:
    persona_id: str
    capital_pool_id: str | None
    binding_id: str | None
    mode: str  # "gateway_rpc" | "dry_run"
    registered: list[PersonaCronJobRecord] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "persona_id": self.persona_id,
            "capital_pool_id": self.capital_pool_id,
            "binding_id": self.binding_id,
            "mode": self.mode,
            "registered": [
                {
                    "workflow_id": r.workflow_id,
                    "job_name": r.job_name,
                    "job_id": r.job_id,
                    "schedule": r.schedule,
                    "registered_at": r.registered_at,
                }
                for r in self.registered
            ],
            "failed": list(self.failed),
        }


class PersonaCronRegistrar:
    """Register all WORKFLOW_CATALOG workflows as recurring cron jobs in the OpenClaw gateway.

    Each active persona gets its own set of four recurring jobs (ingest / review /
    retrain / deploy) so the OODA loop runs on the canonical cadence without
    manual intervention.

    When ``dry_run=True`` (default) or the gateway is unreachable, no RPC calls
    are made and the result is marked ``mode="dry_run"`` — persona creation
    succeeds regardless of gateway availability.
    """

    def __init__(
        self,
        gateway_runtime: Any = None,
        *,
        dry_run: bool = False,
        session_target: str = "main",
        delivery_mode: str = "none",
    ) -> None:
        self._runtime = gateway_runtime
        self._dry_run = dry_run
        self._session_target = session_target
        self._delivery_mode = delivery_mode

    def _get_runtime(self) -> Any:
        if self._runtime is not None:
            return self._runtime
        if os.environ.get("OPENCLAW_PAPER_ADAPTER_ENABLED", "").lower() != "true":
            return None
        try:
            _ensure_adapter_path()
            from integrations.openclaw.adapter import (  # type: ignore[import]
                OpenClawDockerGatewayRuntime,
                OpenClawGatewayConfig,
            )
            return OpenClawDockerGatewayRuntime(
                OpenClawGatewayConfig(
                    host=os.environ.get("OPENCLAW_GATEWAY_HOST", "127.0.0.1"),
                    host_port=int(os.environ.get("OPENCLAW_GATEWAY_PORT", "18789")),
                    gateway_token=os.environ.get("OPENCLAW_GATEWAY_TOKEN", "pantheon-local-token"),
                    container_name=os.environ.get("OPENCLAW_CONTAINER_NAME", "pantheon-openclaw-gateway"),
                )
            )
        except Exception:  # noqa: BLE001
            return None

    def _build_system_event_text(
        self,
        workflow: WorkflowDefinition,
        persona_id: str,
        request_id: str,
    ) -> str:
        payload = {
            "kind": "pantheon.workflow.dispatch",
            "persona_id": persona_id,
            "policy_id": workflow.policy_id,
            "request_id": request_id,
            "upstream_entrypoint": workflow.upstream_entrypoint,
            "workflow_id": workflow.workflow_id,
        }
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    def _register_one(
        self,
        runtime: Any,
        workflow: WorkflowDefinition,
        persona_id: str,
        capital_pool_id: str | None,
        binding_id: str | None,
    ) -> PersonaCronJobRecord:
        request_id = f"persona-{workflow.workflow_id}-{uuid.uuid4()}"
        job_name = _job_name(workflow.workflow_id, persona_id)
        event_text = self._build_system_event_text(workflow, persona_id, request_id)

        params: dict[str, Any] = {
            "name": job_name,
            "enabled": True,
            "deleteAfterRun": False,
            "schedule": {"kind": "cron", "cron": workflow.schedule},
            "sessionTarget": self._session_target,
            "wakeMode": "next-heartbeat",
            "payload": {"kind": "systemEvent", "text": event_text},
            "delivery": {"mode": self._delivery_mode},
            "metadata": {
                "persona_id": persona_id,
                "workflow_id": workflow.workflow_id,
                "policy_id": workflow.policy_id,
                "capital_pool_id": capital_pool_id,
                "binding_id": binding_id,
            },
        }
        response = runtime.gateway_call("cron.add", params)
        job_id = str(response.get("id") or "")
        if not job_id:
            raise RuntimeError(
                f"cron.add for {workflow.workflow_id} / {persona_id} returned no job id"
            )
        return PersonaCronJobRecord(
            workflow_id=workflow.workflow_id,
            job_name=job_name,
            job_id=job_id,
            schedule=workflow.schedule,
            registered_at=utc_now(),
        )

    def register_for_persona(
        self,
        persona_id: str,
        capital_pool_id: str | None = None,
        binding_id: str | None = None,
    ) -> PersonaCronRegistrationResult:
        """Register all four WORKFLOW_CATALOG workflows for *persona_id*.

        Returns a :class:`PersonaCronRegistrationResult` describing what was
        registered (or skipped in dry-run / degraded mode).  Never raises —
        failures are captured in ``result.failed`` so persona creation can
        proceed regardless of gateway availability.
        """
        runtime = None if self._dry_run else self._get_runtime()

        if runtime is None:
            mode = "dry_run"
            registered = [
                PersonaCronJobRecord(
                    workflow_id=wf.workflow_id,
                    job_name=_job_name(wf.workflow_id, persona_id),
                    job_id=f"dry-run-{uuid.uuid4().hex[:8]}",
                    schedule=wf.schedule,
                    registered_at=utc_now(),
                )
                for wf in WORKFLOW_CATALOG.values()
            ]
            return PersonaCronRegistrationResult(
                persona_id=persona_id,
                capital_pool_id=capital_pool_id,
                binding_id=binding_id,
                mode=mode,
                registered=registered,
            )

        mode = "gateway_rpc"
        registered: list[PersonaCronJobRecord] = []
        failed: list[dict[str, Any]] = []

        for workflow in WORKFLOW_CATALOG.values():
            try:
                record = self._register_one(
                    runtime, workflow, persona_id, capital_pool_id, binding_id
                )
                registered.append(record)
            except Exception as exc:  # noqa: BLE001
                failed.append(
                    {
                        "workflow_id": workflow.workflow_id,
                        "error": str(exc),
                        "persona_id": persona_id,
                    }
                )

        return PersonaCronRegistrationResult(
            persona_id=persona_id,
            capital_pool_id=capital_pool_id,
            binding_id=binding_id,
            mode=mode,
            registered=registered,
            failed=failed,
        )
