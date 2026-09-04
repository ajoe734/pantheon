"""PersonaCronRegistrar — register WORKFLOW_CATALOG as recurring OpenClaw cron jobs.

One set of catalog jobs is registered per active persona/binding. The gateway
schedules them using the canonical cron expressions from each
WorkflowDefinition so OpenClaw drives the OODA loop on the correct cadence.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .models import utc_now
from .workflows import (
    PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
    WORKFLOW_CATALOG,
    WorkflowDefinition,
)


class AdapterCronRuntime:
    """`gateway_call()` over the openclaw-gateway-adapter HTTP cron proxy.

    The BFF cannot reach the gateway directly (no docker socket, no openclaw
    binary), so it POSTs whitelisted `cron.*` RPCs to the adapter, which has the
    CLI + ws:// reachability. Interface matches OpenClawDockerGatewayRuntime's
    ``gateway_call(method, params) -> dict`` so PersonaCronRegistrar can use
    either transport interchangeably.
    """

    def __init__(
        self,
        adapter_base_url: str,
        *,
        timeout_seconds: int = 30,
        service_token: str | None = None,
    ) -> None:
        self._url = adapter_base_url.rstrip("/") + "/api/openclaw-adapter/gateway/cron"
        self._timeout = timeout_seconds
        token = (
            os.environ.get("PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN", "")
            if service_token is None
            else service_token
        )
        self._service_token = str(token or "").strip()

    def gateway_call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._service_token:
            raise RuntimeError(
                "OpenClaw adapter cron service authentication is required, but "
                "PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN is not configured."
            )
        body = json.dumps({"method": method, "params": params or {}}).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Pantheon-Service-Token": self._service_token,
            },
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


_MAX_JOB_NAME_LEN = 60


def _job_name(workflow_id: str, persona_id: str) -> str:
    wf_slug = re.sub(r"[^a-z0-9]+", "-", workflow_id.lower()).strip("-")
    persona_slug = re.sub(r"[^a-z0-9]+", "-", persona_id.lower()).strip("-")
    prefix = f"pantheon-{wf_slug}-"
    budget = _MAX_JOB_NAME_LEN - len(prefix)
    if len(persona_slug) > budget:
        # A blind fixed-length truncation collides whenever two persona ids
        # share a long common prefix (e.g. "persona-<same-creation-date>-"
        # followed by a distinct id suffix) — that made every persona created
        # on the same day skip real registration once one had already claimed
        # the name. Keep a stable hash of the full id as a suffix so distinct
        # ids never truncate to the same job name.
        digest = hashlib.sha1(persona_slug.encode("utf-8")).hexdigest()[:8]
        keep = max(0, budget - len(digest) - 1)
        persona_slug = f"{persona_slug[:keep]}-{digest}"
    return f"{prefix}{persona_slug}"


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

    Each active persona gets its own recurring catalog jobs, including an
    explicitly identified first-evaluation schedule, so the OODA loop runs on
    the canonical cadence without manual intervention.

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
        # OpenClaw 2026.6.8 only allows systemEvent cron payloads on the main
        # session. Persona/runtime/capital ownership stays in the systemEvent
        # text, which is the authoritative readback key for Pantheon.
        self._session_target = session_target
        self._delivery_mode = delivery_mode

    def _expected_session_target(self, persona_id: str) -> str:
        return self._session_target or "main"

    def _delivery_matches_expected(self, delivery: Any) -> bool:
        if self._delivery_mode == "none":
            # OpenClaw accepts delivery {"mode":"none"} on create/update but
            # normalizes main-session systemEvent rows by omitting delivery on
            # list readback. Treat both as the same no-delivery authority.
            return delivery is None or delivery == {"mode": "none"}
        return delivery == {"mode": self._delivery_mode}

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
        *,
        runtime_id: str | None = None,
        runtime_binding_id: str | None = None,
        capital_pool_id: str | None = None,
        persona_capital_binding_id: str | None = None,
    ) -> str:
        payload = {
            "kind": "pantheon.workflow.dispatch",
            "persona_id": persona_id,
            "policy_id": workflow.policy_id,
            "request_id": request_id,
            "upstream_entrypoint": workflow.upstream_entrypoint,
            "workflow_id": workflow.workflow_id,
        }
        if workflow.workflow_id == PERSONA_FIRST_EVALUATION_WORKFLOW_ID:
            # These five identifiers are the authoritative persona-runtime-
            # capital identity for first evaluation.  Keep every key present
            # even when a legacy caller has not supplied the newer runtime
            # fields: exact readback can then distinguish an explicit null
            # identity from an older/malformed payload that omitted it.
            payload.update(
                {
                    "runtime_id": runtime_id,
                    "runtime_binding_id": runtime_binding_id,
                    "capital_pool_id": capital_pool_id,
                    "persona_capital_binding_id": persona_capital_binding_id,
                }
            )
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    def _build_job_patch(
        self,
        workflow: WorkflowDefinition,
        persona_id: str,
        request_id: str,
        *,
        runtime_id: str | None,
        runtime_binding_id: str | None,
        capital_pool_id: str | None,
        persona_capital_binding_id: str | None,
    ) -> dict[str, Any]:
        """Build the complete owned portion of a cron job.

        The same shape is valid as the body of ``cron.add`` and as the
        ``patch`` member of ``cron.update``. Reusing it prevents a reconciled
        first-evaluation job from retaining stale schedule/target/envelope
        fields while only its embedded runtime identifiers are changed.
        """
        event_text = self._build_system_event_text(
            workflow,
            persona_id,
            request_id,
            runtime_id=runtime_id,
            runtime_binding_id=runtime_binding_id,
            capital_pool_id=capital_pool_id,
            persona_capital_binding_id=persona_capital_binding_id,
        )
        return {
            "name": _job_name(workflow.workflow_id, persona_id),
            "enabled": True,
            "deleteAfterRun": False,
            "schedule": {"kind": "cron", "expr": workflow.schedule},
            "sessionTarget": self._expected_session_target(persona_id),
            "wakeMode": "next-heartbeat",
            "payload": {"kind": "systemEvent", "text": event_text},
            "delivery": {"mode": self._delivery_mode},
        }

    def _register_one(
        self,
        runtime: Any,
        workflow: WorkflowDefinition,
        persona_id: str,
        capital_pool_id: str | None,
        persona_capital_binding_id: str | None,
        runtime_id: str | None,
        runtime_binding_id: str | None,
    ) -> PersonaCronJobRecord:
        # Stable owner correlation survives BFF/adapter response loss and
        # process restart. Persona identity is already tenant-scoped and
        # deterministic, so this token joins every retry to the same schedule
        # intent without depending on an ephemeral mutation receipt.
        request_id = f"persona-provisioning:{persona_id}:{workflow.workflow_id}"
        job_name = _job_name(workflow.workflow_id, persona_id)
        params = self._build_job_patch(
            workflow,
            persona_id,
            request_id,
            runtime_id=runtime_id,
            runtime_binding_id=runtime_binding_id,
            capital_pool_id=capital_pool_id,
            persona_capital_binding_id=persona_capital_binding_id,
        )
        # OpenClaw's cron schema has no metadata property. Persona/runtime/
        # capital authority therefore travels in the systemEvent text.
        add_error: Exception | None = None
        try:
            response = runtime.gateway_call("cron.add", params)
            job_id = str(response.get("id") or "").strip()
            if not job_id:
                raise RuntimeError(
                    f"cron.add for {workflow.workflow_id} / {persona_id} returned no job id"
                )
        except Exception as exc:  # noqa: BLE001
            # A transport error can happen after OpenClaw committed cron.add.
            # Never retry the mutation blind: the unique request_id embedded in
            # the systemEvent lets a fresh authoritative list distinguish this
            # committed attempt from a pre-apply failure or concurrent writer.
            add_error = exc
            try:
                owner_jobs = self._matching_workflow_jobs(
                    self._list_jobs(runtime),
                    persona_id,
                    workflow.workflow_id,
                )
            except Exception as list_exc:  # noqa: BLE001
                raise RuntimeError(
                    f"cron.add outcome unknown for {workflow.workflow_id} / "
                    f"{persona_id}: {exc}; authoritative cron.list failed: {list_exc}"
                ) from list_exc

            committed_jobs = [
                job
                for job in owner_jobs
                if self._is_exact_workflow_job(
                    job,
                    workflow=workflow,
                    persona_id=persona_id,
                    request_id=request_id,
                    runtime_id=runtime_id,
                    runtime_binding_id=runtime_binding_id,
                    capital_pool_id=capital_pool_id,
                    persona_capital_binding_id=persona_capital_binding_id,
                )
            ]
            if len(owner_jobs) != 1 or len(committed_jobs) != 1:
                raise RuntimeError(
                    f"cron.add outcome did not converge for {workflow.workflow_id} / "
                    f"{persona_id}: authoritative cron.list found "
                    f"{len(owner_jobs)} owner rows and {len(committed_jobs)} exact "
                    f"rows for the attempted request; original error: {exc}"
                ) from exc
            job_id = str(committed_jobs[0].get("id") or "").strip()

        if add_error is not None and not job_id:
            # Defensive guard: _is_exact_workflow_job requires a non-empty id,
            # so this path should be unreachable even for malformed gateways.
            raise RuntimeError(
                f"cron.add reconciliation for {workflow.workflow_id} / "
                f"{persona_id} returned no job id: {add_error}"
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
        *,
        workflow_ids: Iterable[str] | None = None,
        runtime_id: str | None = None,
        runtime_binding_id: str | None = None,
        persona_capital_binding_id: str | None = None,
    ) -> PersonaCronRegistrationResult:
        """Register selected WORKFLOW_CATALOG workflows for *persona_id*.

        Returns a :class:`PersonaCronRegistrationResult` describing what was
        registered (or skipped in dry-run / degraded mode).  Never raises —
        failures are captured in ``result.failed`` so persona creation can
        proceed regardless of gateway availability.

        ``workflow_ids=None`` preserves the historical all-catalog behavior.
        Callers with a required provisioning boundary can explicitly select
        only the workflow(s) that boundary owns.  ``binding_id`` remains a
        backwards-compatible alias for ``persona_capital_binding_id``; it is
        never interpreted as a runtime binding identifier.
        """
        if workflow_ids is None:
            selected_workflows = list(WORKFLOW_CATALOG.values())
        else:
            requested_ids = (
                [workflow_ids]
                if isinstance(workflow_ids, str)
                else list(workflow_ids)
            )
            selected_workflows = []
            seen_ids: set[str] = set()
            unknown_ids: list[str] = []
            for requested_id in requested_ids:
                clean_workflow_id = str(requested_id or "").strip()
                if clean_workflow_id in seen_ids:
                    continue
                seen_ids.add(clean_workflow_id)
                workflow = WORKFLOW_CATALOG.get(clean_workflow_id)
                if workflow is None:
                    unknown_ids.append(clean_workflow_id)
                    continue
                selected_workflows.append(workflow)

            if unknown_ids:
                return PersonaCronRegistrationResult(
                    persona_id=persona_id,
                    capital_pool_id=capital_pool_id,
                    binding_id=(
                        persona_capital_binding_id
                        if persona_capital_binding_id is not None
                        else binding_id
                    ),
                    mode="dry_run" if self._dry_run else "gateway_rpc",
                    failed=[
                        {
                            "workflow_id": workflow_id,
                            "error": f"Unknown workflow: {workflow_id}",
                            "persona_id": persona_id,
                        }
                        for workflow_id in unknown_ids
                    ],
                )

        effective_persona_capital_binding_id = (
            persona_capital_binding_id
            if persona_capital_binding_id is not None
            else binding_id
        )
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
                for wf in selected_workflows
            ]
            return PersonaCronRegistrationResult(
                persona_id=persona_id,
                capital_pool_id=capital_pool_id,
                binding_id=effective_persona_capital_binding_id,
                mode=mode,
                registered=registered,
            )

        mode = "gateway_rpc"
        registered: list[PersonaCronJobRecord] = []
        failed: list[dict[str, Any]] = []
        skipped: list[str] = []
        try:
            existing_jobs = self._list_jobs(runtime)
            existing = self._registration_pairs(existing_jobs)
        except Exception as exc:  # noqa: BLE001
            # Fail closed: if we cannot verify what's already registered, do
            # NOT fall through to blindly calling cron.add for every workflow
            # — that would create duplicate jobs for personas that already
            # have them instead of detecting them as already-registered.
            return PersonaCronRegistrationResult(
                persona_id=persona_id,
                capital_pool_id=capital_pool_id,
                binding_id=effective_persona_capital_binding_id,
                mode=mode,
                failed=[
                    {
                        "workflow_id": (
                            "all"
                            if workflow_ids is None
                            else ",".join(
                                workflow.workflow_id for workflow in selected_workflows
                            )
                            or "none"
                        ),
                        "error": f"cron.list failed, refusing to register blind: {exc}",
                        "persona_id": persona_id,
                    }
                ],
            )

        if any(
            workflow.workflow_id == PERSONA_FIRST_EVALUATION_WORKFLOW_ID
            for workflow in selected_workflows
        ):
            ambiguous_jobs = self._ambiguous_first_evaluation_name_jobs(
                existing_jobs,
                persona_id,
            )
            if ambiguous_jobs:
                ambiguous_ids = sorted(
                    str(job.get("id") or "<missing-id>")
                    for job in ambiguous_jobs
                )
                return PersonaCronRegistrationResult(
                    persona_id=persona_id,
                    capital_pool_id=capital_pool_id,
                    binding_id=effective_persona_capital_binding_id,
                    mode=mode,
                    failed=[
                        {
                            "workflow_id": PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
                            "error": (
                                "ambiguous deterministic first-evaluation job name "
                                "does not prove the requested persona/workflow owner; "
                                "refusing to mutate without governed cleanup: "
                                f"{ambiguous_ids}"
                            ),
                            "persona_id": persona_id,
                        }
                    ],
                )

        for workflow in selected_workflows:
            job_name = _job_name(workflow.workflow_id, persona_id)
            if workflow.workflow_id == PERSONA_FIRST_EVALUATION_WORKFLOW_ID:
                owner_jobs = self._matching_first_evaluation_jobs(
                    existing_jobs,
                    persona_id,
                )
                if owner_jobs:
                    converged, error = self._reconcile_first_evaluation_jobs(
                        runtime,
                        owner_jobs,
                        persona_id=persona_id,
                        runtime_id=runtime_id,
                        runtime_binding_id=runtime_binding_id,
                        capital_pool_id=capital_pool_id,
                        persona_capital_binding_id=(
                            effective_persona_capital_binding_id
                        ),
                    )
                    if converged:
                        skipped.append(job_name)
                    else:
                        failed.append(
                            {
                                "workflow_id": workflow.workflow_id,
                                "error": error
                                or "first-evaluation cron reconciliation failed",
                                "persona_id": persona_id,
                            }
                        )
                    continue
            if (persona_id, workflow.workflow_id) in existing:
                # Idempotent: this persona already has a job for this workflow.
                skipped.append(job_name)
                continue
            try:
                record = self._register_one(
                    runtime,
                    workflow,
                    persona_id,
                    capital_pool_id,
                    effective_persona_capital_binding_id,
                    runtime_id,
                    runtime_binding_id,
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
            binding_id=effective_persona_capital_binding_id,
            mode=mode,
            registered=registered,
            failed=failed,
            skipped=skipped,
        )

    def has_workflow_registration(
        self,
        persona_id: str,
        workflow_id: str,
        *,
        runtime: Any = None,
    ) -> bool:
        """Return whether the exact persona/workflow pair is registered.

        This is the fail-closed readback boundary for callers that require a
        particular schedule. A different workflow registered for the same
        persona, or the required workflow registered for another persona,
        never satisfies the check. Dry-run/unavailable runtimes return False.
        """
        clean_persona_id = str(persona_id or "").strip()
        clean_workflow_id = str(workflow_id or "").strip()
        if not clean_persona_id:
            raise ValueError("persona_id is required for cron registration readback")
        if clean_workflow_id not in WORKFLOW_CATALOG:
            raise KeyError(f"Unknown workflow: {clean_workflow_id}")

        resolved_runtime = runtime
        if resolved_runtime is None and not self._dry_run:
            resolved_runtime = self._get_runtime()
        if resolved_runtime is None:
            return False
        return (
            clean_persona_id,
            clean_workflow_id,
        ) in self._existing_registrations(resolved_runtime)

    def has_first_evaluation_registration(
        self,
        persona_id: str,
        *,
        runtime: Any = None,
        runtime_id: str | None = None,
        runtime_binding_id: str | None = None,
        capital_pool_id: str | None = None,
        persona_capital_binding_id: str | None = None,
    ) -> bool:
        return self.get_first_evaluation_registration(
            persona_id,
            runtime=runtime,
            runtime_id=runtime_id,
            runtime_binding_id=runtime_binding_id,
            capital_pool_id=capital_pool_id,
            persona_capital_binding_id=persona_capital_binding_id,
        ) is not None

    def get_first_evaluation_registration(
        self,
        persona_id: str,
        *,
        runtime: Any = None,
        runtime_id: str | None = None,
        runtime_binding_id: str | None = None,
        capital_pool_id: str | None = None,
        persona_capital_binding_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Authoritatively verify one exact first-evaluation cron record.

        Unlike generic workflow discovery, this required provisioning
        boundary must not collapse gateway rows into an identity pair set:
        duplicate jobs are an invalid registration, as are disabled jobs or
        jobs whose schedule, target, payload envelope, or embedded authority
        identity differs from the requested persona/runtime/capital tuple.
        """
        clean_persona_id = str(persona_id or "").strip()
        if not clean_persona_id:
            raise ValueError("persona_id is required for cron registration readback")

        resolved_runtime = runtime
        if resolved_runtime is None and not self._dry_run:
            resolved_runtime = self._get_runtime()
        if resolved_runtime is None:
            return None

        jobs = self._list_jobs(resolved_runtime)
        if self._ambiguous_first_evaluation_name_jobs(jobs, clean_persona_id):
            return None
        candidates = self._matching_first_evaluation_jobs(jobs, clean_persona_id)

        if len(candidates) != 1:
            return None

        if not self._is_exact_first_evaluation_job(
            candidates[0],
            persona_id=clean_persona_id,
            runtime_id=runtime_id,
            runtime_binding_id=runtime_binding_id,
            capital_pool_id=capital_pool_id,
            persona_capital_binding_id=persona_capital_binding_id,
        ):
            return None
        return json.loads(json.dumps(candidates[0]))

    def remove_first_evaluation_registration(
        self,
        persona_id: str,
        *,
        runtime: Any = None,
    ) -> dict[str, Any]:
        """Remove every first-evaluation row owned by *persona_id* and prove it.

        This is the terminal-compensation boundary. A successful mutation
        response is not proof, and a lost response is not automatically a
        failure: only a fresh authoritative ``cron.list`` showing zero owner
        rows permits ``registered=False`` to be returned. Any ambiguous owner
        id or unavailable final readback raises so callers cannot record false
        compensation completion.
        """

        clean_persona_id = str(persona_id or "").strip()
        if not clean_persona_id:
            raise ValueError("persona_id is required for cron registration removal")

        resolved_runtime = runtime
        if resolved_runtime is None and not self._dry_run:
            resolved_runtime = self._get_runtime()
        if resolved_runtime is None:
            raise RuntimeError(
                "authoritative cron runtime is unavailable for first-evaluation removal"
            )

        try:
            jobs = self._list_jobs(resolved_runtime)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"authoritative cron.list failed before first-evaluation removal: {exc}"
            ) from exc

        ambiguous_jobs = self._ambiguous_first_evaluation_name_jobs(
            jobs,
            clean_persona_id,
        )
        if ambiguous_jobs:
            ambiguous_ids = sorted(
                str(job.get("id") or "<missing-id>") for job in ambiguous_jobs
            )
            raise RuntimeError(
                "ambiguous deterministic first-evaluation job name does not prove "
                "the requested persona/workflow owner; refusing removal without "
                f"governed cleanup: {ambiguous_ids}"
            )
        owner_jobs = self._matching_first_evaluation_jobs(jobs, clean_persona_id)

        owner_ids = sorted(str(job.get("id") or "").strip() for job in owner_jobs)
        if any(not job_id for job_id in owner_ids) or len(set(owner_ids)) != len(owner_ids):
            raise RuntimeError(
                "first-evaluation owner rows have missing or duplicate gateway ids; "
                "refusing ambiguous removal"
            )

        mutation_errors: list[str] = []
        for job_id in owner_ids:
            try:
                resolved_runtime.gateway_call("cron.remove", {"id": job_id})
            except Exception as exc:  # noqa: BLE001
                # The remove may have committed before its response was lost.
                # Preserve the error for diagnostics, but let final readback
                # decide whether compensation actually completed.
                mutation_errors.append(f"cron.remove {job_id}: {exc}")

        try:
            final_jobs = self._list_jobs(resolved_runtime)
        except Exception as exc:  # noqa: BLE001
            details = "; ".join(mutation_errors)
            suffix = f"; mutation errors: {details}" if details else ""
            raise RuntimeError(
                f"authoritative cron.list failed after first-evaluation removal: "
                f"{exc}{suffix}"
            ) from exc

        final_ambiguous_jobs = self._ambiguous_first_evaluation_name_jobs(
            final_jobs,
            clean_persona_id,
        )
        if final_ambiguous_jobs:
            ambiguous_ids = sorted(
                str(job.get("id") or "<missing-id>")
                for job in final_ambiguous_jobs
            )
            details = "; ".join(mutation_errors)
            suffix = f"; mutation errors: {details}" if details else ""
            raise RuntimeError(
                "authoritative readback found an ambiguous deterministic "
                "first-evaluation job name after removal; governed cleanup is "
                f"required: {ambiguous_ids}{suffix}"
            )
        remaining = self._matching_first_evaluation_jobs(
            final_jobs,
            clean_persona_id,
        )

        if remaining:
            remaining_ids = [str(job.get("id") or "") for job in remaining]
            details = "; ".join(mutation_errors)
            suffix = f"; mutation errors: {details}" if details else ""
            raise RuntimeError(
                "first-evaluation removal did not converge to registered=false; "
                f"remaining owner rows: {remaining_ids}{suffix}"
            )

        return {
            "registered": False,
            "removed_ids": owner_ids,
        }

    @staticmethod
    def _decode_job_event(job: dict[str, Any]) -> dict[str, Any] | None:
        payload = job.get("payload")
        if not isinstance(payload, dict):
            return None
        text = payload.get("text")
        if not isinstance(text, str):
            return None
        try:
            inner = json.loads(text)
        except (TypeError, ValueError):
            return None
        return inner if isinstance(inner, dict) else None

    def _matching_first_evaluation_jobs(
        self,
        jobs: list[dict[str, Any]],
        persona_id: str,
    ) -> list[dict[str, Any]]:
        """Return rows whose payload proves this persona/workflow owner key.

        The deterministic job name is intentionally not an ownership token.
        Its hash suffix can collide, and legacy or malformed rows can retain a
        name after losing their authoritative payload identity.
        """
        matches: list[dict[str, Any]] = []
        for job in jobs:
            inner = self._decode_job_event(job)
            payload_claims_owner = bool(
                inner is not None
                and inner.get("persona_id") == persona_id
                and inner.get("workflow_id")
                == PERSONA_FIRST_EVALUATION_WORKFLOW_ID
            )
            if payload_claims_owner:
                matches.append(job)
        return matches

    def _ambiguous_first_evaluation_name_jobs(
        self,
        jobs: list[dict[str, Any]],
        persona_id: str,
    ) -> list[dict[str, Any]]:
        """Return same-name rows that do not prove the requested owner key."""

        expected_name = _job_name(
            PERSONA_FIRST_EVALUATION_WORKFLOW_ID,
            persona_id,
        )
        ambiguous: list[dict[str, Any]] = []
        for job in jobs:
            if job.get("name") != expected_name:
                continue
            inner = self._decode_job_event(job)
            if not (
                inner is not None
                and inner.get("persona_id") == persona_id
                and inner.get("workflow_id")
                == PERSONA_FIRST_EVALUATION_WORKFLOW_ID
            ):
                ambiguous.append(job)
        return ambiguous

    def _matching_workflow_jobs(
        self,
        jobs: list[dict[str, Any]],
        persona_id: str,
        workflow_id: str,
    ) -> list[dict[str, Any]]:
        """Return every gateway row claiming one persona/workflow owner key."""

        matches: list[dict[str, Any]] = []
        for job in jobs:
            inner = self._decode_job_event(job)
            if inner is None:
                continue
            if (
                inner.get("persona_id") == persona_id
                and inner.get("workflow_id") == workflow_id
            ):
                matches.append(job)
        return matches

    def _is_exact_workflow_job(
        self,
        job: dict[str, Any],
        *,
        workflow: WorkflowDefinition,
        persona_id: str,
        request_id: str,
        runtime_id: str | None,
        runtime_binding_id: str | None,
        capital_pool_id: str | None,
        persona_capital_binding_id: str | None,
    ) -> bool:
        """Verify the authoritative row produced by one cron.add attempt."""

        inner = self._decode_job_event(job)
        schedule = job.get("schedule")
        payload = job.get("payload")
        if inner is None:
            return False
        if not (
            isinstance(job.get("id"), str)
            and bool(job["id"].strip())
            and job.get("name") == _job_name(workflow.workflow_id, persona_id)
            and job.get("enabled") is True
            and job.get("deleteAfterRun") is False
            and isinstance(schedule, dict)
            and schedule.get("kind") == "cron"
            and schedule.get("expr") == workflow.schedule
            and job.get("sessionTarget") == self._expected_session_target(persona_id)
            and job.get("wakeMode") == "next-heartbeat"
            and self._delivery_matches_expected(job.get("delivery"))
            and isinstance(payload, dict)
            and payload.get("kind") == "systemEvent"
            and inner.get("kind") == "pantheon.workflow.dispatch"
            and inner.get("persona_id") == persona_id
            and inner.get("request_id") == request_id
            and inner.get("workflow_id") == workflow.workflow_id
            and inner.get("policy_id") == workflow.policy_id
            and inner.get("upstream_entrypoint") == workflow.upstream_entrypoint
        ):
            return False

        if workflow.workflow_id != PERSONA_FIRST_EVALUATION_WORKFLOW_ID:
            return True
        return all(
            key in inner and inner.get(key) == value
            for key, value in {
                "runtime_id": runtime_id,
                "runtime_binding_id": runtime_binding_id,
                "capital_pool_id": capital_pool_id,
                "persona_capital_binding_id": persona_capital_binding_id,
            }.items()
        )

    def _is_exact_first_evaluation_job(
        self,
        job: dict[str, Any],
        *,
        persona_id: str,
        runtime_id: str | None,
        runtime_binding_id: str | None,
        capital_pool_id: str | None,
        persona_capital_binding_id: str | None,
    ) -> bool:
        workflow = WORKFLOW_CATALOG[PERSONA_FIRST_EVALUATION_WORKFLOW_ID]
        inner = self._decode_job_event(job)
        if inner is None:
            return False
        schedule = job.get("schedule")
        payload = job.get("payload")
        expected_identity = {
            "persona_id": persona_id,
            "runtime_id": runtime_id,
            "runtime_binding_id": runtime_binding_id,
            "capital_pool_id": capital_pool_id,
            "persona_capital_binding_id": persona_capital_binding_id,
        }
        expected_request_id = (
            f"persona-provisioning:{persona_id}:{workflow.workflow_id}"
        )
        return (
            isinstance(job.get("id"), str)
            and bool(job["id"].strip())
            and job.get("name") == _job_name(workflow.workflow_id, persona_id)
            and job.get("enabled") is True
            and job.get("deleteAfterRun") is False
            and isinstance(schedule, dict)
            and schedule.get("kind") == "cron"
            and schedule.get("expr") == workflow.schedule
            and job.get("sessionTarget") == self._expected_session_target(persona_id)
            and job.get("wakeMode") == "next-heartbeat"
            and self._delivery_matches_expected(job.get("delivery"))
            and isinstance(payload, dict)
            and payload.get("kind") == "systemEvent"
            and inner.get("kind") == "pantheon.workflow.dispatch"
            and inner.get("request_id") == expected_request_id
            and all(
                key in inner and inner.get(key) == value
                for key, value in expected_identity.items()
            )
            and inner.get("workflow_id") == workflow.workflow_id
            and inner.get("policy_id") == workflow.policy_id
            and inner.get("upstream_entrypoint") == workflow.upstream_entrypoint
        )

    def _reconcile_first_evaluation_jobs(
        self,
        runtime: Any,
        owner_jobs: list[dict[str, Any]],
        *,
        persona_id: str,
        runtime_id: str | None,
        runtime_binding_id: str | None,
        capital_pool_id: str | None,
        persona_capital_binding_id: str | None,
    ) -> tuple[bool, str | None]:
        """Converge owned first-evaluation rows, then prove exact readback.

        Canonical ownership is deterministic: retain the lexicographically
        smallest non-empty gateway job id and remove every other owner row.
        Mutation responses are never accepted as proof. In particular, an
        update/remove may have committed before its response was lost; only a
        fresh authoritative ``cron.list`` decides whether convergence won.
        """
        workflow = WORKFLOW_CATALOG[PERSONA_FIRST_EVALUATION_WORKFLOW_ID]

        def canonical_key(job: dict[str, Any]) -> tuple[int, str]:
            job_id = str(job.get("id") or "").strip()
            return (0 if job_id else 1, job_id)

        ordered_jobs = sorted(owner_jobs, key=canonical_key)
        canonical = ordered_jobs[0]
        mutation_errors: list[str] = []

        for duplicate in ordered_jobs[1:]:
            duplicate_id = str(duplicate.get("id") or "").strip()
            if not duplicate_id:
                mutation_errors.append("duplicate owner row has no gateway id")
                continue
            try:
                runtime.gateway_call("cron.remove", {"id": duplicate_id})
            except Exception as exc:  # noqa: BLE001
                mutation_errors.append(f"cron.remove {duplicate_id}: {exc}")

        if len(ordered_jobs) > 1 or not self._is_exact_first_evaluation_job(
            canonical,
            persona_id=persona_id,
            runtime_id=runtime_id,
            runtime_binding_id=runtime_binding_id,
            capital_pool_id=capital_pool_id,
            persona_capital_binding_id=persona_capital_binding_id,
        ):
            canonical_id = str(canonical.get("id") or "").strip()
            if not canonical_id:
                mutation_errors.append("canonical owner row has no gateway id")
            else:
                request_id = (
                    f"persona-provisioning:{persona_id}:{workflow.workflow_id}"
                )
                patch = self._build_job_patch(
                    workflow,
                    persona_id,
                    request_id,
                    runtime_id=runtime_id,
                    runtime_binding_id=runtime_binding_id,
                    capital_pool_id=capital_pool_id,
                    persona_capital_binding_id=persona_capital_binding_id,
                )
                try:
                    runtime.gateway_call(
                        "cron.update",
                        {"id": canonical_id, "patch": patch},
                    )
                except Exception as exc:  # noqa: BLE001
                    mutation_errors.append(f"cron.update {canonical_id}: {exc}")

        try:
            final_jobs = self._list_jobs(runtime)
        except Exception as exc:  # noqa: BLE001
            details = "; ".join(mutation_errors)
            suffix = f"; mutation errors: {details}" if details else ""
            return False, f"authoritative cron.list failed: {exc}{suffix}"

        final_ambiguous_jobs = self._ambiguous_first_evaluation_name_jobs(
            final_jobs,
            persona_id,
        )
        if final_ambiguous_jobs:
            ambiguous_ids = sorted(
                str(job.get("id") or "<missing-id>")
                for job in final_ambiguous_jobs
            )
            details = "; ".join(mutation_errors)
            suffix = f"; mutation errors: {details}" if details else ""
            return (
                False,
                "authoritative cron.list found an ambiguous deterministic "
                "first-evaluation job name; governed cleanup is required: "
                f"{ambiguous_ids}{suffix}",
            )

        final_owner_jobs = self._matching_first_evaluation_jobs(
            final_jobs,
            persona_id,
        )

        if len(final_owner_jobs) == 1 and self._is_exact_first_evaluation_job(
            final_owner_jobs[0],
            persona_id=persona_id,
            runtime_id=runtime_id,
            runtime_binding_id=runtime_binding_id,
            capital_pool_id=capital_pool_id,
            persona_capital_binding_id=persona_capital_binding_id,
        ):
            return True, None

        details = "; ".join(mutation_errors)
        suffix = f"; mutation errors: {details}" if details else ""
        return (
            False,
            "first-evaluation cron reconciliation did not converge to exactly "
            f"one exact owner row (found {len(final_owner_jobs)}){suffix}",
        )

    def _list_jobs(self, runtime: Any) -> list[dict[str, Any]]:
        """Return every raw gateway cron row without identity deduplication."""
        all_jobs: list[dict[str, Any]] = []
        offset = 0
        seen_offsets: set[int] = set()
        while True:
            if offset in seen_offsets:
                raise RuntimeError("authoritative cron.list returned a pagination cycle")
            seen_offsets.add(offset)
            listing = (
                runtime.gateway_call(
                    "cron.list",
                    {"limit": 200, "offset": offset},
                )
                or {}
            )
            if not isinstance(listing, dict):
                raise RuntimeError("authoritative cron.list returned a non-object payload")
            jobs = listing.get("jobs")
            if not isinstance(jobs, list):
                raise RuntimeError("authoritative cron.list returned a non-list jobs field")
            all_jobs.extend(job for job in jobs if isinstance(job, dict))
            if not listing.get("hasMore"):
                break
            next_offset = listing.get("nextOffset", offset + len(jobs))
            if not isinstance(next_offset, int) or next_offset < 0:
                raise RuntimeError("authoritative cron.list returned an invalid nextOffset")
            offset = next_offset
        return all_jobs

    def _existing_registrations(self, runtime: Any) -> set[tuple[str, str]]:
        """(persona_id, workflow_id) pairs already registered in the gateway.

        Identity is read from each job's systemEvent payload text, not its
        gateway "name" field. ``_job_name`` truncates/hashes long ids to stay
        under the gateway's job-name length limit, so two distinct personas
        (or the same persona re-registered after a naming-scheme change) can
        legitimately share a display name — matching on name would then
        either skip a persona that was never actually registered, or (worse)
        fail to recognize an already-registered persona and re-add it under
        a new name every time ``_job_name`` changes. The payload always
        carries the real ``persona_id``/``workflow_id``, so matching on that
        is stable across any naming scheme.

        Paginates because the gateway rejects ``limit`` above 200 (a single
        ``limit=500`` call used to fail outright, and the caller previously
        swallowed that error and treated the gateway as empty — silently
        re-registering every workflow as a duplicate on each rerun instead of
        detecting it as already present). Raises on failure so the caller can
        fail closed instead of registering blind.
        """
        return self._registration_pairs(self._list_jobs(runtime))

    def _registration_pairs(
        self,
        jobs: list[dict[str, Any]],
    ) -> set[tuple[str, str]]:
        """Collapse raw jobs only for legacy, non-authoritative discovery."""
        pairs: set[tuple[str, str]] = set()
        for job in jobs:
            inner = self._decode_job_event(job)
            if inner is None:
                continue
            pid = inner.get("persona_id")
            workflow_id = inner.get("workflow_id")
            if pid and workflow_id:
                pairs.add((pid, workflow_id))
        return pairs

    def reconcile_personas(
        self, persona_ids: list[str]
    ) -> tuple[list[PersonaCronRegistrationResult], list[dict[str, Any]], list[dict[str, Any]]]:
        """Register missing OODA cron jobs for each persona in *persona_ids*
        and remove orphan cron jobs.

        Idempotent backfill for personas that already exist but were created
        before cron registration worked, and clean up jobs that do not belong
        to any persona in *persona_ids* or are not in the workflows catalog.

        Returns a tuple: (registration_results, removed_orphans, remove_failures)
        """
        # 1. Register/backfill missing jobs for eligible personas
        results = [self.register_for_persona(pid) for pid in persona_ids]

        removed_orphans: list[dict[str, Any]] = []
        remove_failures: list[dict[str, Any]] = []

        # 2. Cleanup orphan jobs from the gateway
        runtime = None if self._dry_run else self._get_runtime()
        if runtime is not None:
            try:
                # Get existing registrations to find orphans
                offset = 0
                eligible_set = set(persona_ids)
                while True:
                    listing = runtime.gateway_call("cron.list", {"limit": 200, "offset": offset}) or {}
                    jobs = listing.get("jobs") or []
                    for job in jobs:
                        if not isinstance(job, dict):
                            continue
                        job_id = job.get("id")
                        job_name = job.get("name", "")

                        # Only touch jobs starting with "pantheon-"
                        if not (
                            isinstance(job_name, str)
                            and job_name.startswith("pantheon-")
                        ):
                            continue

                        if not isinstance(job_id, str) or not job_id.strip():
                            remove_failures.append({
                                "job_id": job_id,
                                "job_name": job_name,
                                "error": (
                                    "Pantheon cron ownership is ambiguous; "
                                    "governed cleanup is required"
                                ),
                                "reason": "missing gateway job id",
                            })
                            continue

                        payload = job.get("payload")
                        text = (
                            payload.get("text")
                            if isinstance(payload, dict)
                            else None
                        )
                        is_orphan = False
                        reason = ""
                        ownership_ambiguous = False
                        if not isinstance(text, str) or not text.strip():
                            ownership_ambiguous = True
                            reason = "missing payload text"
                        else:
                            try:
                                inner = json.loads(text)
                                if not isinstance(inner, dict):
                                    ownership_ambiguous = True
                                    reason = "payload text is not a JSON object"
                                else:
                                    pid = inner.get("persona_id")
                                    workflow_id = inner.get("workflow_id")
                                    if not (
                                        isinstance(pid, str)
                                        and pid.strip()
                                        and isinstance(workflow_id, str)
                                        and workflow_id.strip()
                                    ):
                                        ownership_ambiguous = True
                                        reason = (
                                            "missing persona_id or workflow_id "
                                            "in payload"
                                        )
                                    elif pid not in eligible_set:
                                        is_orphan = True
                                        reason = (
                                            f"persona_id '{pid}' not in eligible set"
                                        )
                                    elif workflow_id not in WORKFLOW_CATALOG:
                                        is_orphan = True
                                        reason = (
                                            f"workflow_id '{workflow_id}' not in catalog"
                                        )
                            except (TypeError, ValueError) as exc:
                                ownership_ambiguous = True
                                reason = f"malformed payload text: {exc}"

                        if ownership_ambiguous:
                            remove_failures.append({
                                "job_id": job_id,
                                "job_name": job_name,
                                "error": (
                                    "Pantheon cron ownership is ambiguous; "
                                    "governed cleanup is required"
                                ),
                                "reason": reason,
                            })
                            continue

                        if is_orphan:
                            try:
                                runtime.gateway_call("cron.remove", {"id": job_id})
                                removed_orphans.append({
                                    "job_id": job_id,
                                    "job_name": job_name,
                                    "reason": reason,
                                })
                            except Exception as exc:
                                remove_failures.append({
                                    "job_id": job_id,
                                    "job_name": job_name,
                                    "error": str(exc),
                                    "reason": reason,
                                })
                    if not listing.get("hasMore"):
                        break
                    offset = listing.get("nextOffset", offset + len(jobs))
            except Exception as exc:
                # If listing fails, do not remove any jobs to fail safe
                pass

        return results, removed_orphans, remove_failures
