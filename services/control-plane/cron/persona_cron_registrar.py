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
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models import utc_now
from workflows import WORKFLOW_CATALOG, WorkflowDefinition


class AdapterCronRuntime:
    """`gateway_call()` over the openclaw-gateway-adapter HTTP cron proxy.

    The BFF cannot reach the gateway directly (no docker socket, no openclaw
    binary), so it POSTs whitelisted `cron.*` RPCs to the adapter, which has the
    CLI + ws:// reachability. Interface matches OpenClawDockerGatewayRuntime's
    ``gateway_call(method, params) -> dict`` so PersonaCronRegistrar can use
    either transport interchangeably.
    """

    def __init__(self, adapter_base_url: str, *, timeout_seconds: int = 30) -> None:
        self._url = adapter_base_url.rstrip("/") + "/api/openclaw-adapter/gateway/cron"
        self._timeout = timeout_seconds

    def gateway_call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps({"method": method, "params": params or {}}).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if payload.get("status") != "ok":
            raise RuntimeError(
                f"adapter cron proxy error for {method}: "
                f"{payload.get('error_code')}: {payload.get('message')}"
            )
        data = payload.get("data")
        return data if isinstance(data, dict) else {}


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
    skipped: list[str] = field(default_factory=list)  # job names already present

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
            "skipped": list(self.skipped),
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
        session_target: str | None = None,
        delivery_mode: str = "none",
    ) -> None:
        self._runtime = gateway_runtime
        self._dry_run = dry_run
        # None → each persona's OODA jobs wake THAT persona's own agent (agent id
        # == persona_id). An explicit value overrides (e.g. "main" to route every
        # job through the orchestrator agent instead).
        self._session_target = session_target
        self._delivery_mode = delivery_mode

    def _get_runtime(self) -> Any:
        if self._runtime is not None:
            return self._runtime
        # Preferred path from the BFF (which has no docker socket / openclaw
        # binary): proxy cron.* RPCs through the openclaw-gateway-adapter HTTP
        # endpoint. This is what makes creation-time registration actually
        # register live jobs instead of silently no-op'ing in dry_run.
        adapter_url = os.environ.get("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "").strip()
        if adapter_url:
            return AdapterCronRuntime(adapter_url)
        # Host-side fallback: direct docker-exec runtime (only where a docker
        # socket is available and explicitly enabled).
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
            # OpenClaw 2026.6.8 cron schema: the cron variant is
            # {"kind":"cron","expr":"<5-field cron>"} — NOT "cron". Sending
            # {"kind":"cron","cron":...} is rejected with a schema error, which
            # is why no persona OODA job was ever registered.
            "schedule": {"kind": "cron", "expr": workflow.schedule},
            "sessionTarget": self._session_target or persona_id,
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
        skipped: list[str] = []
        existing = self._existing_job_names(runtime)

        for workflow in WORKFLOW_CATALOG.values():
            job_name = _job_name(workflow.workflow_id, persona_id)
            if job_name in existing:
                # Idempotent: a job with this name is already registered.
                skipped.append(job_name)
                continue
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
            skipped=skipped,
        )

    def _existing_job_names(self, runtime: Any) -> set[str]:
        """Names of cron jobs already registered in the gateway (best-effort)."""
        try:
            listing = runtime.gateway_call("cron.list", {"limit": 500})
        except Exception:  # noqa: BLE001
            return set()
        jobs = (listing or {}).get("jobs") or []
        return {str(j.get("name")) for j in jobs if isinstance(j, dict) and j.get("name")}

    def reconcile_personas(
        self, persona_ids: list[str]
    ) -> list[PersonaCronRegistrationResult]:
        """Register missing OODA cron jobs for each persona in *persona_ids*.

        Idempotent backfill for personas that already exist but were created
        before cron registration worked (or whose best-effort creation-time
        registration silently no-op'd in dry_run). Returns one result per persona.
        """
        return [self.register_for_persona(pid) for pid in persona_ids]
