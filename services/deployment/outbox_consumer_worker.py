"""
Durable deployment saga outbox consumer worker.

Polls the deployment service outbox for pending events and consumes each
one via the service API. Designed to run as a supervised long-lived process
(docker-compose restart: unless-stopped) so that approved DeploymentPlan
transitions do not require manual endpoint stepping.

Acceptance (LOOP-AUTO-DEP-001):
- Deployment outbox events are consumed durably.
- Duplicate outbox events are idempotent (status: duplicate is not an error).
- Consumer exposes health: last success, last failure.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

# Ensure services/deployment is in python path
_DEPLOYMENT_DIR = str(Path(__file__).resolve().parent)
if _DEPLOYMENT_DIR not in sys.path:
    sys.path.insert(0, _DEPLOYMENT_DIR)

from runtime_manager_dispatch_adapter import (
    dispatch_to_runtime_manager,
    DispatchOutcome,
    DispatchResult,
    validate_authoritative_readback,
)
from runtime_manager_client import RuntimeManagerClient




_CONSUMER_NAME = "deployment-outbox-consumer"
_TERMINAL_SAGA_STATUSES = {"failed", "aborted"}


class SequenceBlockedError(RuntimeError):
    """An earlier aggregate event must be replayed before this side effect."""


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def fetch_pending_outbox(*, api_url: str, timeout_seconds: float = 10.0) -> list[dict[str, Any]]:
    """Return all pending outbox records from the deployment service."""
    url = api_url.rstrip("/") + "/api/deployment/outbox?status=pending"
    request = urllib.request.Request(
        url, headers={"Accept": "application/json"}, method="GET"
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else []


def fetch_applied_inbox(
    *,
    api_url: str,
    consumer_name: str,
    aggregate_id: str,
    timeout_seconds: float = 10.0,
) -> list[dict[str, Any]]:
    """Read applied receipts used to gate side effects on DEP-002 ordering."""
    query = urllib.parse.urlencode(
        {
            "consumer_name": consumer_name,
            "aggregate_id": aggregate_id,
            "status": "applied",
        }
    )
    url = api_url.rstrip("/") + f"/api/deployment/inbox?{query}"
    request = urllib.request.Request(
        url, headers={"Accept": "application/json"}, method="GET"
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else []


def consume_event(
    *,
    api_url: str,
    event_id: str,
    consumer_name: str,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """
    Consume one outbox event. Returns the inbox receipt.

    The deployment service returns HTTP 200 with status="duplicate" when the
    event has already been consumed by this consumer — this is not an error.
    """
    url = api_url.rstrip("/") + f"/api/deployment/outbox/{event_id}/consume"
    payload = json.dumps({"consumer_name": consumer_name}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def record_delivery_failure(
    *,
    api_url: str,
    event_id: str,
    consumer_name: str,
    reason: str,
    retryable: bool,
    max_attempts: int,
    retry_delay_seconds: int,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Persist one failed delivery attempt into the deployment outbox record."""
    url = api_url.rstrip("/") + f"/api/deployment/outbox/{event_id}/failure"
    payload = json.dumps(
        {
            "consumer_name": consumer_name,
            "reason": reason,
            "retryable": retryable,
            "max_attempts": max_attempts,
            "retry_delay_seconds": retry_delay_seconds,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def _parse_rfc3339(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _retry_due(record: dict[str, Any]) -> bool:
    next_retry = _parse_rfc3339(record.get("next_retry_at"))
    if next_retry is None:
        return True
    return next_retry <= datetime.now(timezone.utc)


def _http_error_retryable(exc: urllib.error.HTTPError) -> bool:
    if exc.code in {408, 409, 425, 429}:
        return True
    return 500 <= exc.code <= 599


def _record_failure_best_effort(
    *,
    api_url: str,
    event_id: str,
    consumer_name: str,
    reason: str,
    retryable: bool,
    max_attempts: int,
    retry_delay_seconds: int,
    timeout_seconds: float,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return (
            record_delivery_failure(
                api_url=api_url,
                event_id=event_id,
                consumer_name=consumer_name,
                reason=reason,
                retryable=retryable,
                max_attempts=max_attempts,
                retry_delay_seconds=retry_delay_seconds,
                timeout_seconds=timeout_seconds,
            ),
            None,
        )
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def fetch_saga(*, api_url: str, saga_id: str, timeout_seconds: float = 10.0) -> dict[str, Any]:
    url = api_url.rstrip("/") + f"/api/deployment/sagas/{saga_id}"
    request = urllib.request.Request(
        url, headers={"Accept": "application/json"}, method="GET"
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def fetch_plan(*, api_url: str, plan_id: str, timeout_seconds: float = 10.0) -> dict[str, Any]:
    url = api_url.rstrip("/") + f"/api/deployment/plans/{plan_id}"
    request = urllib.request.Request(
        url, headers={"Accept": "application/json"}, method="GET"
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def fetch_projection(
    *, api_url: str, plan_id: str, timeout_seconds: float = 10.0
) -> dict[str, Any]:
    """Read the joined DEP-003 deployment projection for terminal verification."""
    url = api_url.rstrip("/") + f"/api/deployment/projections/{plan_id}"
    request = urllib.request.Request(
        url, headers={"Accept": "application/json"}, method="GET"
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def update_plan_status(
    *,
    api_url: str,
    plan_id: str,
    status: str,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Advance DeploymentPlan status through its canonical writer API."""
    url = api_url.rstrip("/") + f"/api/deployment/plans/{plan_id}/status"
    payload = json.dumps({"status": status}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def finalize_compensation(
    *,
    api_url: str,
    saga_id: str,
    note: str,
    terminal_status: str,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Finalize a compensated saga only after its side effect is read back."""
    url = (
        api_url.rstrip("/")
        + f"/api/deployment/sagas/{saga_id}/compensation/finalize"
    )
    payload = json.dumps(
        {"note": note, "terminal_status": terminal_status}
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def fetch_incident(
    *, base_url: str, incident_id: str, timeout_seconds: float = 10.0
) -> dict[str, Any]:
    url = base_url.rstrip("/") + f"/api/incidents/{incident_id}"
    request = urllib.request.Request(
        url, headers={"Accept": "application/json"}, method="GET"
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def create_incident(
    *, base_url: str, payload: Mapping[str, Any], timeout_seconds: float = 10.0
) -> dict[str, Any]:
    """Create a stable incident, recovering a duplicate POST via authoritative GET."""
    url = base_url.rstrip("/") + "/api/incidents"
    body = json.dumps(dict(payload)).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        if exc.code != 409:
            raise
        return fetch_incident(
            base_url=base_url,
            incident_id=str(payload["incident_id"]),
            timeout_seconds=timeout_seconds,
        )


def fetch_paper_fleet_state(
    *, base_url: str, timeout_seconds: float = 10.0
) -> dict[str, Any]:
    """Read actual paper worker/controller state from the fleet reconciler."""
    url = base_url.rstrip("/") + "/api/fleet/state"
    request = urllib.request.Request(
        url, headers={"Accept": "application/json"}, method="GET"
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def validate_paper_fleet_readback(
    *, saga: dict[str, Any], binding: dict[str, Any], fleet: dict[str, Any]
) -> str | None:
    """Return why paper controller truth is not yet the requested running state."""
    binding_id = binding.get("binding_id")
    matches = [
        worker
        for worker in fleet.get("workers", [])
        if worker.get("binding_id") == binding_id
    ]
    mismatches: list[str] = []
    if fleet.get("last_error"):
        mismatches.append(f"fleet last_error={fleet.get('last_error')!r}")
    if not fleet.get("last_reconcile_at") or int(fleet.get("cycle_count") or 0) < 1:
        mismatches.append("fleet has not completed an authoritative reconcile cycle")
    if len(matches) != 1:
        mismatches.append(
            f"expected one fleet worker for binding_id={binding_id!r}, found {len(matches)}"
        )
    else:
        worker = matches[0]
        expected = {
            "runtime_id": binding.get("runtime_id"),
            "capital_pool_id": saga.get("capital_pool_id"),
            "status": "running",
        }
        mismatches.extend(
            f"fleet worker {field} expected {value!r}, got {worker.get(field)!r}"
            for field, value in expected.items()
            if worker.get(field) != value
        )
        if not worker.get("pid"):
            mismatches.append("fleet worker is missing a live process id")
    return "; ".join(mismatches) or None


def run_compatibility_check(
    *,
    api_url: str,
    capital_pool_id: str,
    sponsor_persona_id: str,
    target_stage: str,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    url = api_url.rstrip("/") + "/api/deployment/plans/compatibility-check"
    payload = json.dumps({
        "capital_pool_id": capital_pool_id,
        "sponsor_persona_id": sponsor_persona_id,
        "target_stage": target_stage,
    }).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def record_binding_created(
    *,
    api_url: str,
    saga_id: str,
    binding_id: str,
    runtime_id: str | None,
    note: str | None,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    url = api_url.rstrip("/") + f"/api/deployment/sagas/{saga_id}/binding-created"
    payload = json.dumps({
        "binding_id": binding_id,
        "runtime_id": runtime_id,
        "note": note,
    }).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def record_runtime_active(
    *,
    api_url: str,
    saga_id: str,
    binding_id: str,
    runtime_id: str | None,
    note: str | None,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Advance a saga only after authoritative active RuntimeBinding readback."""
    url = api_url.rstrip("/") + f"/api/deployment/sagas/{saga_id}/runtime-active"
    payload = json.dumps({
        "binding_id": binding_id,
        "runtime_id": runtime_id,
        "note": note,
    }).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def record_saga_failure(
    *,
    api_url: str,
    saga_id: str,
    reason: str,
    failed_step: str | None,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    url = api_url.rstrip("/") + f"/api/deployment/sagas/{saga_id}/failure"
    payload = json.dumps({
        "reason": reason,
        "failed_step": failed_step,
    }).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def loader_checks_attested(plan: Mapping[str, Any]) -> bool:
    """Accept only an explicit literal-True loader assertion from plan truth.

    Promotion persists the assertion in ``DeploymentPlan.metadata``.  A
    top-level field is accepted only for wire compatibility; conflicting
    top-level/metadata values fail closed.
    """
    metadata = plan.get("metadata")
    metadata_is_mapping = isinstance(metadata, Mapping)
    metadata_has_value = metadata_is_mapping and "loader_checks_passed" in metadata
    top_level_has_value = "loader_checks_passed" in plan
    if metadata_has_value and top_level_has_value:
        return (
            metadata.get("loader_checks_passed") is True
            and plan.get("loader_checks_passed") is True
        )
    if metadata_has_value:
        return metadata.get("loader_checks_passed") is True
    return top_level_has_value and plan.get("loader_checks_passed") is True


def _assert_side_effect_sequence(
    *,
    api_url: str,
    consumer_name: str,
    aggregate_id: str,
    sequence_no: int,
    timeout_seconds: float,
) -> None:
    """Refuse mutation while an earlier aggregate event remains unreplayed."""
    receipts = fetch_applied_inbox(
        api_url=api_url,
        consumer_name=consumer_name,
        aggregate_id=aggregate_id,
        timeout_seconds=timeout_seconds,
    )
    last_applied = max(
        (int(receipt.get("sequence_no") or 0) for receipt in receipts),
        default=0,
    )
    if sequence_no != last_applied + 1:
        raise SequenceBlockedError(
            f"DEP-002 side-effect sequence blocked for aggregate_id={aggregate_id!r}: "
            f"expected sequence {last_applied + 1}, got {sequence_no}; replay the "
            "earlier DLQ event before compensation or activation"
        )


def _binding_identity_error(
    *,
    saga: Mapping[str, Any],
    binding: Mapping[str, Any],
    expected_binding_id: str | None = None,
) -> str | None:
    expected = {
        "binding_id": expected_binding_id or saga.get("binding_id"),
        "plan_id": saga.get("plan_id"),
        "capital_pool_id": saga.get("capital_pool_id"),
        "artifact_id": saga.get("artifact_id"),
        "artifact_version": saga.get("artifact_version"),
        "deployment_mode": saga.get("target_stage"),
    }
    mismatches = [
        f"{field} expected {value!r}, got {binding.get(field)!r}"
        for field, value in expected.items()
        if value not in (None, "") and binding.get(field) != value
    ]
    return "; ".join(mismatches) or None


def validate_success_projection(
    *, saga: Mapping[str, Any], binding: Mapping[str, Any], projection: Mapping[str, Any]
) -> str | None:
    """Validate joined plan/saga/runtime terminal success before inbox ack."""
    expected = {
        "projection_contract": "DEP-003",
        "plan_id": saga.get("plan_id"),
        "artifact_id": saga.get("artifact_id"),
        "artifact_version": saga.get("artifact_version"),
        "capital_pool_id": saga.get("capital_pool_id"),
        "target_stage": saga.get("target_stage"),
        "actual_stage": saga.get("target_stage"),
        "plan_status": "executed",
        "runtime_binding_id": binding.get("binding_id"),
        "runtime_id": binding.get("runtime_id"),
        "runtime_status": "active",
        "deployment_saga_id": saga.get("saga_id"),
        "deployment_saga_status": "completed",
    }
    mismatches = [
        f"projection {field} expected {value!r}, got {projection.get(field)!r}"
        for field, value in expected.items()
        if projection.get(field) != value
    ]
    source_status = projection.get("source_status")
    for source in ("deployment_plan", "runtime_binding", "deployment_saga"):
        actual = source_status.get(source) if isinstance(source_status, Mapping) else None
        if actual != "canonical":
            mismatches.append(
                f"projection source_status.{source} expected 'canonical', got {actual!r}"
            )
    plan = projection.get("plan")
    if not isinstance(plan, Mapping) or plan.get("binding_id") != binding.get("binding_id"):
        mismatches.append("projection plan does not carry the authoritative binding_id")
    return "; ".join(mismatches) or None


def validate_compensation_projection(
    *,
    saga: Mapping[str, Any],
    projection: Mapping[str, Any],
    expected_plan_status: str,
) -> str | None:
    """Validate the terminal saga and owner-scoped plan post-state."""
    expected = {
        "projection_contract": "DEP-003",
        "plan_id": saga.get("plan_id"),
        "artifact_id": saga.get("artifact_id"),
        "artifact_version": saga.get("artifact_version"),
        "capital_pool_id": saga.get("capital_pool_id"),
        "plan_status": expected_plan_status,
        "deployment_saga_id": saga.get("saga_id"),
        "deployment_saga_status": saga.get("status"),
    }
    mismatches = [
        f"projection {field} expected {value!r}, got {projection.get(field)!r}"
        for field, value in expected.items()
        if projection.get(field) != value
    ]
    if saga.get("status") not in _TERMINAL_SAGA_STATUSES:
        mismatches.append(f"saga status is not terminal: {saga.get('status')!r}")
    if saga.get("current_step") != "compensated":
        mismatches.append(
            f"saga current_step expected 'compensated', got {saga.get('current_step')!r}"
        )
    source_status = projection.get("source_status")
    for source in ("deployment_plan", "deployment_saga"):
        actual = source_status.get(source) if isinstance(source_status, Mapping) else None
        if actual != "canonical":
            mismatches.append(
                f"projection source_status.{source} expected 'canonical', got {actual!r}"
            )
    return "; ".join(mismatches) or None


def _incident_payload(
    *, saga: Mapping[str, Any], binding: Mapping[str, Any], event_id: str, reason: str
) -> dict[str, Any]:
    saga_id = str(saga.get("saga_id") or "")
    return {
        "incident_id": f"inc-deployment-comp-{saga_id}",
        "title": f"Deployment compensation entered safe mode for {saga_id}",
        "status": "open",
        "severity": "critical",
        "binding_id": binding.get("binding_id"),
        "deployment_stage": binding.get("deployment_mode"),
        "deployment_plan_id": saga.get("plan_id"),
        "capital_pool_id": saga.get("capital_pool_id"),
        "persona_capital_binding_id": binding.get("persona_capital_binding_id"),
        "artifact_id": binding.get("artifact_id"),
        "artifact_version": binding.get("artifact_version"),
        "runtime_id": binding.get("runtime_id"),
        "trace_id": saga.get("trace_id"),
        "incident_cluster_id": f"deployment-compensation:{saga_id}",
        "evidence_summary": (
            f"event_id={event_id}; saga_id={saga_id}; fail-closed containment: {reason}"
        ),
        "lineage_ref": f"{binding.get('artifact_id')}@{binding.get('artifact_version')}",
    }


def _incident_mismatch(
    expected: Mapping[str, Any], actual: Mapping[str, Any]
) -> str | None:
    fields = (
        "incident_id",
        "binding_id",
        "deployment_stage",
        "deployment_plan_id",
        "capital_pool_id",
        "persona_capital_binding_id",
        "artifact_id",
        "artifact_version",
        "runtime_id",
        "trace_id",
    )
    mismatches = [
        f"incident {field} expected {expected.get(field)!r}, got {actual.get(field)!r}"
        for field in fields
        if actual.get(field) != expected.get(field)
    ]
    if actual.get("status") not in {"open", "investigating"}:
        mismatches.append(
            f"incident status expected open/investigating, got {actual.get('status')!r}"
        )
    return "; ".join(mismatches) or None


def _contain_and_raise_incident(
    *,
    client: RuntimeManagerClient,
    saga: Mapping[str, Any],
    binding: Mapping[str, Any],
    event_id: str,
    event_idempotency_key: str,
    incident_url: str,
    reason: str,
    timeout_seconds: float,
) -> str:
    """Enter paused safe mode, prove it, then create an exact incident record."""
    if not incident_url:
        raise RuntimeError(
            "PANTHEON_INCIDENTS_API_URL is required for compensation escalation"
        )
    if not binding.get("binding_id") or not binding.get("runtime_id"):
        raise RuntimeError(
            "safe-mode incident requires a real RuntimeBinding and runtime identity"
        )
    kill_result = client.execute_kill_switch(
        {
            "reason": "severity_1_incident",
            "capital_pool_id": saga.get("capital_pool_id"),
            "binding_id": binding.get("binding_id"),
            "actor_id": _CONSUMER_NAME,
            "severity": 1,
            "action_override": "pause",
            "idempotency_key": event_idempotency_key or event_id,
            "trace_context": {"trace_id": saga.get("trace_id")},
            "context": {
                "saga_id": saga.get("saga_id"),
                "plan_id": saga.get("plan_id"),
                "compensation_event_id": event_id,
                "reason": reason,
            },
        }
    )
    ack = kill_result.get("telemetry_ack")
    if not isinstance(ack, Mapping) or ack.get("ack_status") != "acknowledged":
        raise RuntimeError(
            f"kill-switch compensation did not produce acknowledged runtime follow-through: {ack!r}"
        )
    safe_mode = client.get_safe_mode(str(saga.get("capital_pool_id") or ""))
    if safe_mode.get("safe_mode_state") != "paused":
        raise RuntimeError(
            f"safe-mode readback expected 'paused', got {safe_mode.get('safe_mode_state')!r}"
        )
    contained = client.get(str(binding.get("binding_id")))
    if contained is None or contained.get("status") != "paused":
        raise RuntimeError(
            "kill-switch readback did not prove the targeted RuntimeBinding is paused"
        )
    incident_payload = _incident_payload(
        saga=saga,
        binding=contained,
        event_id=event_id,
        reason=reason,
    )
    incident = create_incident(
        base_url=incident_url,
        payload=incident_payload,
        timeout_seconds=timeout_seconds,
    )
    mismatch = _incident_mismatch(incident_payload, incident)
    if mismatch:
        raise RuntimeError(f"incident readback mismatch: {mismatch}")
    return (
        f"safe mode paused binding {contained.get('binding_id')} and opened "
        f"incident {incident.get('incident_id')}"
    )


def _rollback_loader_attestation_error(
    *, plan: Mapping[str, Any], rollback: Mapping[str, Any]
) -> str | None:
    metadata = plan.get("metadata")
    attestation = (
        metadata.get("rollback_loader_attestation")
        if isinstance(metadata, Mapping)
        else None
    )
    if not isinstance(attestation, Mapping):
        return "missing metadata.rollback_loader_attestation"
    expected = {
        "artifact_id": rollback.get("target_artifact_id"),
        "artifact_version": rollback.get("target_version"),
    }
    mismatches = [
        f"rollback loader {field} expected {value!r}, got {attestation.get(field)!r}"
        for field, value in expected.items()
        if attestation.get(field) != value
    ]
    if attestation.get("passed") is not True:
        mismatches.append("rollback loader passed must be literal true")
    if not str(attestation.get("proof_ref") or "").strip():
        mismatches.append("rollback loader proof_ref is required")
    return "; ".join(mismatches) or None


def _rollback_binding_error(
    *,
    old_binding: Mapping[str, Any],
    new_binding: Mapping[str, Any],
    rollback: Mapping[str, Any],
    action_type: str,
) -> str | None:
    expected = {
        "capital_pool_id": old_binding.get("capital_pool_id"),
        "artifact_id": rollback.get("target_artifact_id"),
        "artifact_version": rollback.get("target_version"),
        "deployment_mode": old_binding.get("deployment_mode"),
        "execution_mode": old_binding.get("execution_mode")
        or old_binding.get("deployment_mode"),
        "persona_capital_binding_id": old_binding.get(
            "persona_capital_binding_id"
        ),
        "rollback_parent": old_binding.get("binding_id"),
        "rollback_action_type": action_type,
        "status": "active",
    }
    mismatches = [
        f"rollback child {field} expected {value!r}, got {new_binding.get(field)!r}"
        for field, value in expected.items()
        if new_binding.get(field) != value
    ]
    return "; ".join(mismatches) or None


def _execute_rollback_compensation(
    *,
    api_url: str,
    client: RuntimeManagerClient,
    saga: Mapping[str, Any],
    plan: Mapping[str, Any],
    binding: Mapping[str, Any],
    event_id: str,
    event_idempotency_key: str,
    incident_url: str,
    timeout_seconds: float,
) -> str:
    rollback = plan.get("rollback")
    if not isinstance(rollback, Mapping):
        return _contain_and_raise_incident(
            client=client,
            saga=saga,
            binding=binding,
            event_id=event_id,
            event_idempotency_key=event_idempotency_key,
            incident_url=incident_url,
            reason="rollback compensation has no DeploymentPlan.rollback target",
            timeout_seconds=timeout_seconds,
        )
    action_type = str(rollback.get("action_type") or "replace")
    attestation_error = _rollback_loader_attestation_error(
        plan=plan, rollback=rollback
    )
    safe_mode_readback = client.get_safe_mode(
        str(saga.get("capital_pool_id") or "")
    )
    safe_mode_state = (
        safe_mode_readback.get("safe_mode_state")
        if isinstance(safe_mode_readback, Mapping)
        else None
    )
    if safe_mode_state and safe_mode_state not in {"normal", "normal_restored"}:
        return _contain_and_raise_incident(
            client=client,
            saga=saga,
            binding=binding,
            event_id=event_id,
            event_idempotency_key=event_idempotency_key,
            incident_url=incident_url,
            reason=(
                "kill-switch safe mode won before rollback compensation: "
                f"{safe_mode_state}"
            ),
            timeout_seconds=timeout_seconds,
        )
    pool_bindings = client.list_by_pool(str(saga.get("capital_pool_id") or ""))
    children = [
        candidate
        for candidate in pool_bindings
        if candidate.get("rollback_parent") == binding.get("binding_id")
        and candidate.get("rollback_action_type") == action_type
        and candidate.get("artifact_id") == rollback.get("target_artifact_id")
        and candidate.get("artifact_version") == rollback.get("target_version")
    ]
    if len(children) > 1:
        return _contain_and_raise_incident(
            client=client,
            saga=saga,
            binding=binding,
            event_id=event_id,
            event_idempotency_key=event_idempotency_key,
            incident_url=incident_url,
            reason=f"rollback recovery found {len(children)} matching children",
            timeout_seconds=timeout_seconds,
        )
    new_binding: Mapping[str, Any] | None = children[0] if children else None
    if new_binding is None:
        if binding.get("status") in {"retired", "failed"}:
            return _contain_and_raise_incident(
                client=client,
                saga=saga,
                binding=binding,
                event_id=event_id,
                event_idempotency_key=event_idempotency_key,
                incident_url=incident_url,
                reason="rollback old binding is terminal but no replacement exists",
                timeout_seconds=timeout_seconds,
            )
        if attestation_error:
            return _contain_and_raise_incident(
                client=client,
                saga=saga,
                binding=binding,
                event_id=event_id,
                event_idempotency_key=event_idempotency_key,
                incident_url=incident_url,
                reason=f"rollback loader proof failed closed: {attestation_error}",
                timeout_seconds=timeout_seconds,
            )
        prior_candidates = [
            candidate
            for candidate in pool_bindings
            if candidate.get("binding_id") != binding.get("binding_id")
            and candidate.get("artifact_id") == rollback.get("target_artifact_id")
            and candidate.get("artifact_version") == rollback.get("target_version")
            and candidate.get("deployment_mode") == binding.get("deployment_mode")
            and candidate.get("plan_id")
        ]
        if not prior_candidates:
            return _contain_and_raise_incident(
                client=client,
                saga=saga,
                binding=binding,
                event_id=event_id,
                event_idempotency_key=event_idempotency_key,
                incident_url=incident_url,
                reason="rollback target has no prior authoritative RuntimeBinding lineage",
                timeout_seconds=timeout_seconds,
            )
        prior = max(
            prior_candidates,
            key=lambda candidate: str(candidate.get("effective_at") or ""),
        )
        target_plan = fetch_plan(
            api_url=api_url,
            plan_id=str(prior.get("plan_id")),
            timeout_seconds=timeout_seconds,
        )
        if (
            target_plan.get("artifact_id") != rollback.get("target_artifact_id")
            or target_plan.get("artifact_version") != rollback.get("target_version")
        ):
            return _contain_and_raise_incident(
                client=client,
                saga=saga,
                binding=binding,
                event_id=event_id,
                event_idempotency_key=event_idempotency_key,
                incident_url=incident_url,
                reason="rollback prior binding does not resolve to an exact fallback DeploymentPlan",
                timeout_seconds=timeout_seconds,
            )
        metadata = binding.get("metadata")
        allowed_scope = (
            metadata.get("allowed_deployment_scope")
            if isinstance(metadata, Mapping)
            else None
        ) or binding.get("deployment_mode")
        result = client.rollback(
            {
                "current_binding_id": binding.get("binding_id"),
                "action_type": action_type,
                "replacement_plan_id": target_plan.get("plan_id"),
                "replacement_plan_status": "approved",
                "replacement_artifact_id": rollback.get("target_artifact_id"),
                "replacement_artifact_version": rollback.get("target_version"),
                "replacement_persona_capital_binding_id": binding.get(
                    "persona_capital_binding_id"
                ),
                "replacement_persona_capital_binding_status": "active",
                "replacement_allowed_deployment_scope": allowed_scope,
                "replacement_deployment_mode": binding.get("deployment_mode"),
                "loader_checks_passed": True,
                "opened_by_artifact_id": binding.get("artifact_id"),
                "replacement_strategy_id": saga.get("strategy_id"),
                "replacement_metadata": {
                    "compensation_event_id": event_id,
                    "compensation_idempotency_key": event_idempotency_key,
                    "deployment_saga_id": saga.get("saga_id"),
                    "rollback_source_plan_id": saga.get("plan_id"),
                    "rollback_loader_attestation": dict(
                        plan.get("metadata", {}).get(
                            "rollback_loader_attestation", {}
                        )
                    ),
                },
            }
        )
        new_binding = result.get("new_binding")
        if not isinstance(new_binding, Mapping):
            raise RuntimeError("runtime-manager rollback response omitted new_binding")

    child_error = _rollback_binding_error(
        old_binding=binding,
        new_binding=new_binding,
        rollback=rollback,
        action_type=action_type,
    )
    if child_error:
        raise RuntimeError(f"rollback child readback mismatch: {child_error}")
    refreshed_old = client.get(str(binding.get("binding_id")))
    if refreshed_old is None:
        raise RuntimeError("rollback old RuntimeBinding disappeared during readback")
    if refreshed_old.get("status") != "retired":
        client.retire(str(binding.get("binding_id")))
        refreshed_old = client.get(str(binding.get("binding_id")))
    if refreshed_old is None or refreshed_old.get("status") != "retired":
        raise RuntimeError("rollback did not converge old RuntimeBinding to retired")
    refreshed_new = client.get(str(new_binding.get("binding_id")))
    if refreshed_new is None:
        raise RuntimeError("rollback replacement RuntimeBinding is not readable")
    child_error = _rollback_binding_error(
        old_binding=binding,
        new_binding=refreshed_new,
        rollback=rollback,
        action_type=action_type,
    )
    if child_error:
        raise RuntimeError(f"rollback replacement readback mismatch: {child_error}")
    active = client.get_active_for_pool(str(saga.get("capital_pool_id") or ""))
    if active is None or active.get("binding_id") != refreshed_new.get("binding_id"):
        raise RuntimeError("rollback replacement is not authoritative active pool owner")
    return (
        f"rollback {action_type} retired {binding.get('binding_id')} and activated "
        f"{refreshed_new.get('binding_id')}"
    )


def execute_compensation(
    *,
    api_url: str,
    saga: Mapping[str, Any],
    plan: Mapping[str, Any],
    event: Mapping[str, Any],
    client: RuntimeManagerClient,
    incident_url: str,
    timeout_seconds: float,
) -> tuple[str, str]:
    """Execute one DEP-002 compensation command and finalize after readback."""
    decision = saga.get("compensation")
    if not isinstance(decision, Mapping):
        payload = event.get("payload")
        decision = payload.get("compensation") if isinstance(payload, Mapping) else None
    if not isinstance(decision, Mapping):
        raise RuntimeError("compensation event has no canonical compensation decision")
    command = str(decision.get("command_type") or "")
    event_id = str(event.get("event_id") or "")
    event_idempotency_key = str(event.get("idempotency_key") or event_id)
    binding_id = str(saga.get("binding_id") or "")
    note: str
    terminal_status = "failed"
    expected_plan_status = str(plan.get("status") or "")

    if command == "abort_plan":
        bindings = client.list_by_plan(str(saga.get("plan_id") or ""))
        if binding_id or bindings:
            target = client.get(binding_id) if binding_id else bindings[0]
            if not isinstance(target, Mapping):
                raise RuntimeError("abort compensation found a binding identity but no record")
            note = _contain_and_raise_incident(
                client=client,
                saga=saga,
                binding=target,
                event_id=event_id,
                event_idempotency_key=event_idempotency_key,
                incident_url=incident_url,
                reason="abort_plan found an unexpected RuntimeBinding",
                timeout_seconds=timeout_seconds,
            )
        else:
            if plan.get("status") != "aborted":
                update_plan_status(
                    api_url=api_url,
                    plan_id=str(saga.get("plan_id") or ""),
                    status="aborted",
                    timeout_seconds=timeout_seconds,
                )
            refreshed_plan = fetch_plan(
                api_url=api_url,
                plan_id=str(saga.get("plan_id") or ""),
                timeout_seconds=timeout_seconds,
            )
            if refreshed_plan.get("status") != "aborted" or client.list_by_plan(
                str(saga.get("plan_id") or "")
            ):
                raise RuntimeError("abort_plan post-state did not remain aborted/no-binding")
            terminal_status = "aborted"
            expected_plan_status = "aborted"
            note = "aborted DeploymentPlan after proving no RuntimeBinding exists"
    elif command == "mark_binding_failed_inactive":
        binding = client.get(binding_id) if binding_id else None
        if binding is None:
            if client.list_by_plan(str(saga.get("plan_id") or "")):
                raise RuntimeError(
                    "saga binding_id is missing but authoritative plan bindings exist"
                )
            note = "no RuntimeBinding existed; plan-owned state left unchanged"
        else:
            identity_error = _binding_identity_error(
                saga=saga, binding=binding, expected_binding_id=binding_id
            )
            if identity_error:
                raise RuntimeError(f"failed-binding identity mismatch: {identity_error}")
            if binding.get("status") in {"active", "pending_pause", "paused"}:
                client.transition(binding_id, "failed")
            elif binding.get("status") != "failed":
                raise RuntimeError(
                    f"mark_binding_failed_inactive cannot converge status={binding.get('status')!r}"
                )
            failed = client.get(binding_id)
            if failed is None or failed.get("status") != "failed":
                raise RuntimeError("RuntimeBinding failed-state readback did not converge")
            note = f"marked RuntimeBinding {binding_id} failed/inactive"
    elif command == "request_rollback":
        binding = client.get(binding_id) if binding_id else None
        if binding is None:
            raise RuntimeError("request_rollback requires the saga RuntimeBinding")
        identity_error = _binding_identity_error(
            saga=saga, binding=binding, expected_binding_id=binding_id
        )
        if identity_error:
            raise RuntimeError(f"rollback source identity mismatch: {identity_error}")
        note = _execute_rollback_compensation(
            api_url=api_url,
            client=client,
            saga=saga,
            plan=plan,
            binding=binding,
            event_id=event_id,
            event_idempotency_key=event_idempotency_key,
            incident_url=incident_url,
            timeout_seconds=timeout_seconds,
        )
    elif command == "enter_safe_mode_and_raise_incident":
        binding = client.get(binding_id) if binding_id else None
        if binding is None:
            raise RuntimeError(
                "safe-mode compensation cannot create a canonical incident without a RuntimeBinding"
            )
        identity_error = _binding_identity_error(
            saga=saga, binding=binding, expected_binding_id=binding_id
        )
        if identity_error:
            raise RuntimeError(f"safe-mode source identity mismatch: {identity_error}")
        note = _contain_and_raise_incident(
            client=client,
            saga=saga,
            binding=binding,
            event_id=event_id,
            event_idempotency_key=event_idempotency_key,
            incident_url=incident_url,
            reason=str(decision.get("reason") or saga.get("failure_reason") or command),
            timeout_seconds=timeout_seconds,
        )
    else:
        raise RuntimeError(f"unsupported compensation command_type={command!r}")

    finalize_compensation(
        api_url=api_url,
        saga_id=str(saga.get("saga_id") or ""),
        note=note,
        terminal_status=terminal_status,
        timeout_seconds=timeout_seconds,
    )
    return terminal_status, expected_plan_status


def _apply_receipt_counts(
    *, receipt: dict[str, Any], event_id: str
) -> tuple[int, int, str | None]:
    status = receipt.get("status")
    if status == "applied":
        return 1, 0, None
    if status == "duplicate":
        return 0, 1, None
    return 0, 0, f"event_id={event_id} unexpected_receipt_status={status!r}"


def run_poll(
    *,
    api_url: str,
    consumer_name: str,
    timeout_seconds: float = 10.0,
    record_failures: bool = False,
    max_attempts: int = 3,
    retry_delay_seconds: int = 30,
) -> dict[str, Any]:
    """
    Fetch pending outbox events and consume each one.

    Returns a summary: events_found, consumed, duplicates, errors, retry_scheduled, dead_lettered.
    """
    events = fetch_pending_outbox(api_url=api_url, timeout_seconds=timeout_seconds)
    consumed = 0
    duplicates = 0
    skipped_not_due = 0
    retry_scheduled = 0
    dead_lettered = 0
    errors: list[str] = []

    for record in events:
        if not _retry_due(record):
            skipped_not_due += 1
            continue
        event = record.get("event", {})
        event_id = event.get("event_id", "")
        if not event_id:
            errors.append(f"missing event_id in record: {record}")
            continue
        try:
            event_type = event.get("event_type", "")
            aggregate_id = str(event.get("aggregate_id") or "")
            sequence_no = int(event.get("sequence_no") or 0)
            if (
                event_type
                in {
                    "runtime.load.requested",
                    "deployment.compensation.requested",
                }
                and sequence_no > 1
            ):
                if not aggregate_id:
                    raise ValueError("side-effect event is missing aggregate_id")
                _assert_side_effect_sequence(
                    api_url=api_url,
                    consumer_name=consumer_name,
                    aggregate_id=aggregate_id,
                    sequence_no=sequence_no,
                    timeout_seconds=timeout_seconds,
                )
            if event_type == "runtime.binding.requested":
                # --- CANONICAL DISPATCH TO RUNTIME-MANAGER ---
                saga_id = event.get("aggregate_id")
                if not saga_id:
                    raise ValueError("missing aggregate_id (saga_id) in event")

                # 1. Fetch saga
                saga = fetch_saga(api_url=api_url, saga_id=saga_id, timeout_seconds=timeout_seconds)
                saga_status = str(saga.get("status") or "")
                plan_id = saga.get("plan_id")
                if not plan_id:
                    raise ValueError(f"Saga '{saga_id}' is missing plan_id")

                # A prior attempt may have completed the side effect and then
                # lost its inbox response.  Once compensation/terminal state is
                # durable, replay only advances the inbox sequence; it must not
                # dispatch a second RuntimeBinding.
                if saga_status in {"compensating", "failed", "aborted"}:
                    receipt = consume_event(
                        api_url=api_url,
                        event_id=event_id,
                        consumer_name=consumer_name,
                        timeout_seconds=timeout_seconds,
                    )
                    applied, duplicate, receipt_error = _apply_receipt_counts(
                        receipt=receipt, event_id=event_id
                    )
                    consumed += applied
                    duplicates += duplicate
                    if receipt_error:
                        errors.append(receipt_error)
                    continue

                # 2. Fetch plan
                plan = fetch_plan(api_url=api_url, plan_id=plan_id, timeout_seconds=timeout_seconds)
                sponsor_persona_id = plan.get("sponsor_persona_id")
                capital_pool_id = plan.get("capital_pool_id")
                target_stage = plan.get("target_stage")

                # 3. Call compatibility-check
                compat = run_compatibility_check(
                    api_url=api_url,
                    capital_pool_id=capital_pool_id,
                    sponsor_persona_id=sponsor_persona_id,
                    target_stage=target_stage,
                    timeout_seconds=timeout_seconds,
                )

                if not compat.get("ok"):
                    compat_errors = compat.get("errors", [])
                    reason = f"Compatibility check failed: {'; '.join(compat_errors)}"
                    # Record terminal saga failure
                    record_saga_failure(
                        api_url=api_url,
                        saga_id=saga_id,
                        reason=reason,
                        failed_step="binding_requested",
                        timeout_seconds=timeout_seconds,
                    )
                    # Dead-letter outbox event
                    _record_failure_best_effort(
                        api_url=api_url,
                        event_id=event_id,
                        consumer_name=consumer_name,
                        reason=reason,
                        retryable=False,
                        max_attempts=max_attempts,
                        retry_delay_seconds=retry_delay_seconds,
                        timeout_seconds=timeout_seconds,
                    )
                    dead_lettered += 1
                    errors.append(reason)
                    continue

                if not loader_checks_attested(plan):
                    reason = (
                        "Artifact loader checks are not authoritatively attested by "
                        "DeploymentPlan.metadata.loader_checks_passed=true"
                    )
                    record_saga_failure(
                        api_url=api_url,
                        saga_id=saga_id,
                        reason=reason,
                        failed_step="binding_requested",
                        timeout_seconds=timeout_seconds,
                    )
                    _record_failure_best_effort(
                        api_url=api_url,
                        event_id=event_id,
                        consumer_name=consumer_name,
                        reason=reason,
                        retryable=False,
                        max_attempts=max_attempts,
                        retry_delay_seconds=retry_delay_seconds,
                        timeout_seconds=timeout_seconds,
                    )
                    dead_lettered += 1
                    errors.append(reason)
                    continue

                # 4. Construct deploy_context
                plan_metadata = (
                    dict(plan.get("metadata"))
                    if isinstance(plan.get("metadata"), Mapping)
                    else {}
                )
                deploy_context = {
                    "persona_capital_binding_id": compat.get("persona_binding_id"),
                    "persona_capital_binding_status": "active" if compat.get("persona_scope_ok") else "inactive",
                    "allowed_deployment_scope": compat.get("allowed_deployment_scope"),
                    "loader_checks_passed": True,
                    "plan_status": plan.get("status") or "approved",
                    "idempotency_key": event.get("idempotency_key") or event_id,
                    "promotion_gate": plan_metadata.get("promotion_gate") or {},
                    "metadata": {
                        **plan_metadata,
                        "deployment_saga_id": saga_id,
                        "deployment_outbox_event_id": event_id,
                        "deployment_trace_id": saga.get("trace_id"),
                    },
                }

                # Check for downstream-success-before-receipt idempotency:
                # If the saga doesn't have a binding_id recorded, but the binding already exists downstream
                # in the runtime-manager with our plan_id, reuse it.
                existing_binding_id = saga.get("binding_id")

                # Construct client
                client = RuntimeManagerClient()

                preflight_error: str | None = None
                preflight_terminal_error: str | None = None
                if not existing_binding_id:
                    try:
                        existing_bindings = client.list_by_plan(plan_id)
                        if len(existing_bindings) > 1:
                            preflight_terminal_error = (
                                f"authoritative recovery found {len(existing_bindings)} "
                                f"RuntimeBindings for plan_id={plan_id!r}; manual reconciliation required"
                            )
                        elif existing_bindings:
                            existing_binding_id = existing_bindings[0].get("binding_id")
                    except Exception as exc:
                        # Fail closed: dispatching while recovery readback is
                        # unavailable can duplicate a binding whose POST response
                        # was lost before the saga receipt was recorded.
                        preflight_error = str(exc)

                if existing_binding_id:
                    # Saga or downstream has binding_id already
                    saga["binding_id"] = existing_binding_id

                # 5. Dispatch, or schedule retry when the authoritative recovery
                # query could not prove that no downstream binding already exists.
                dispatch_result = (
                    DispatchResult(
                        outcome=DispatchOutcome.TERMINAL_ERROR,
                        error_message=preflight_terminal_error,
                        error_code="MULTIPLE_BINDINGS_FOR_PLAN",
                    )
                    if preflight_terminal_error
                    else DispatchResult(
                        outcome=DispatchOutcome.RETRYABLE_ERROR,
                        error_message=(
                            "authoritative pre-dispatch binding recovery failed: "
                            f"{preflight_error}"
                        ),
                        error_code="BINDING_RECOVERY_READ_FAILED",
                    )
                    if preflight_error
                    else dispatch_to_runtime_manager(
                        saga=saga,
                        deploy_context=deploy_context,
                        client=client,
                    )
                )

                if dispatch_result.succeeded():
                    # Record binding creation only on the first attempt.  If the
                    # state transition succeeded before a crash/response loss,
                    # the saga already points at the same authoritative binding.
                    if saga_status == "awaiting_binding":
                        record_binding_created(
                            api_url=api_url,
                            saga_id=saga_id,
                            binding_id=dispatch_result.binding_id,
                            runtime_id=dispatch_result.binding.get("runtime_id") if dispatch_result.binding else None,
                            note="binding created/verified via deployment outbox consumer dispatch",
                            timeout_seconds=timeout_seconds,
                        )
                    elif saga.get("binding_id") != dispatch_result.binding_id:
                        raise RuntimeError(
                            f"Saga {saga_id!r} status={saga_status!r} points at "
                            f"binding_id={saga.get('binding_id')!r}, but authoritative "
                            f"readback returned {dispatch_result.binding_id!r}"
                        )
                    # Consume event
                    receipt = consume_event(
                        api_url=api_url,
                        event_id=event_id,
                        consumer_name=consumer_name,
                        timeout_seconds=timeout_seconds,
                    )
                    applied, duplicate, receipt_error = _apply_receipt_counts(
                        receipt=receipt, event_id=event_id
                    )
                    consumed += applied
                    duplicates += duplicate
                    if receipt_error:
                        errors.append(receipt_error)
                elif dispatch_result.is_retryable():
                    reason = f"transient dispatch failure: {dispatch_result.error_message}"
                    errors.append(reason)
                    failure_record, failure_error = _record_failure_best_effort(
                        api_url=api_url,
                        event_id=event_id,
                        consumer_name=consumer_name,
                        reason=reason,
                        retryable=True,
                        max_attempts=max_attempts,
                        retry_delay_seconds=retry_delay_seconds,
                        timeout_seconds=timeout_seconds,
                    )
                    if failure_error:
                        errors.append(f"event_id={event_id} failure_record_error={failure_error}")
                    elif failure_record and failure_record.get("status") == "dead_lettered":
                        dead_lettered += 1
                    else:
                        retry_scheduled += 1
                else: # Terminal error
                    reason = f"terminal dispatch failure: {dispatch_result.error_message}"
                    errors.append(reason)
                    # Record terminal saga failure
                    record_saga_failure(
                        api_url=api_url,
                        saga_id=saga_id,
                        reason=reason,
                        failed_step=(
                            "runtime_load_requested"
                            if saga.get("binding_id")
                            else "binding_requested"
                        ),
                        timeout_seconds=timeout_seconds,
                    )
                    # Dead-letter outbox event
                    _record_failure_best_effort(
                        api_url=api_url,
                        event_id=event_id,
                        consumer_name=consumer_name,
                        reason=reason,
                        retryable=False,
                        max_attempts=max_attempts,
                        retry_delay_seconds=retry_delay_seconds,
                        timeout_seconds=timeout_seconds,
                    )
                    dead_lettered += 1
            elif event_type == "runtime.load.requested":
                saga_id = event.get("aggregate_id")
                if not saga_id:
                    raise ValueError("missing aggregate_id (saga_id) in event")
                saga = fetch_saga(
                    api_url=api_url,
                    saga_id=saga_id,
                    timeout_seconds=timeout_seconds,
                )
                saga_status = str(saga.get("status") or "")

                if saga_status in {"compensating", "failed", "aborted"}:
                    receipt = consume_event(
                        api_url=api_url,
                        event_id=event_id,
                        consumer_name=consumer_name,
                        timeout_seconds=timeout_seconds,
                    )
                    applied, duplicate, receipt_error = _apply_receipt_counts(
                        receipt=receipt, event_id=event_id
                    )
                    consumed += applied
                    duplicates += duplicate
                    if receipt_error:
                        errors.append(receipt_error)
                    continue

                binding_id = (
                    event.get("payload", {}).get("binding_id")
                    or saga.get("binding_id")
                )
                if not binding_id:
                    raise ValueError(f"Saga {saga_id!r} is missing binding_id for runtime readback")

                client = RuntimeManagerClient()
                binding = client.get(binding_id)
                if binding is None:
                    raise RuntimeError(
                        f"authoritative RuntimeBinding {binding_id!r} is not readable yet"
                    )
                mismatch = validate_authoritative_readback(
                    saga=saga,
                    binding=binding,
                    expected_binding_id=binding_id,
                )
                if mismatch:
                    reason = f"RuntimeBinding activation readback failed: {mismatch}"
                    record_saga_failure(
                        api_url=api_url,
                        saga_id=saga_id,
                        reason=reason,
                        failed_step="runtime_load_requested",
                        timeout_seconds=timeout_seconds,
                    )
                    _record_failure_best_effort(
                        api_url=api_url,
                        event_id=event_id,
                        consumer_name=consumer_name,
                        reason=reason,
                        retryable=False,
                        max_attempts=max_attempts,
                        retry_delay_seconds=retry_delay_seconds,
                        timeout_seconds=timeout_seconds,
                    )
                    dead_lettered += 1
                    errors.append(reason)
                    continue

                if saga.get("target_stage") == "paper":
                    fleet_url = os.getenv("PANTHEON_PAPER_FLEET_RECONCILER_URL", "").strip()
                    if not fleet_url:
                        raise RuntimeError(
                            "PANTHEON_PAPER_FLEET_RECONCILER_URL is required for "
                            "authoritative paper controller readback"
                        )
                    fleet = fetch_paper_fleet_state(
                        base_url=fleet_url,
                        timeout_seconds=timeout_seconds,
                    )
                    fleet_mismatch = validate_paper_fleet_readback(
                        saga=saga,
                        binding=binding,
                        fleet=fleet,
                    )
                    if fleet_mismatch:
                        raise RuntimeError(
                            f"paper fleet post-state is not running yet: {fleet_mismatch}"
                        )

                if saga_status in {"awaiting_binding", "awaiting_runtime_load"}:
                    record_runtime_active(
                        api_url=api_url,
                        saga_id=saga_id,
                        binding_id=binding_id,
                        runtime_id=binding.get("runtime_id"),
                        note="runtime active confirmed by authoritative RuntimeBinding readback",
                        timeout_seconds=timeout_seconds,
                    )
                elif saga_status != "completed":
                    raise RuntimeError(
                        f"Saga {saga_id!r} cannot apply runtime readback from status={saga_status!r}"
                    )

                terminal_saga = fetch_saga(
                    api_url=api_url,
                    saga_id=saga_id,
                    timeout_seconds=timeout_seconds,
                )
                projection = fetch_projection(
                    api_url=api_url,
                    plan_id=str(terminal_saga.get("plan_id") or saga.get("plan_id") or ""),
                    timeout_seconds=timeout_seconds,
                )
                projection_mismatch = validate_success_projection(
                    saga=terminal_saga,
                    binding=binding,
                    projection=projection,
                )
                if projection_mismatch:
                    raise RuntimeError(
                        f"terminal deployment projection did not converge: {projection_mismatch}"
                    )

                receipt = consume_event(
                    api_url=api_url,
                    event_id=event_id,
                    consumer_name=consumer_name,
                    timeout_seconds=timeout_seconds,
                )
                applied, duplicate, receipt_error = _apply_receipt_counts(
                    receipt=receipt, event_id=event_id
                )
                consumed += applied
                duplicates += duplicate
                if receipt_error:
                    errors.append(receipt_error)
            elif event_type == "deployment.compensation.requested":
                saga_id = event.get("aggregate_id")
                if not saga_id:
                    raise ValueError("missing aggregate_id (saga_id) in compensation event")
                saga = fetch_saga(
                    api_url=api_url,
                    saga_id=saga_id,
                    timeout_seconds=timeout_seconds,
                )
                plan_id = str(saga.get("plan_id") or "")
                if not plan_id:
                    raise ValueError(f"Saga {saga_id!r} is missing plan_id")
                plan = fetch_plan(
                    api_url=api_url,
                    plan_id=plan_id,
                    timeout_seconds=timeout_seconds,
                )
                saga_status = str(saga.get("status") or "")
                if saga_status in _TERMINAL_SAGA_STATUSES:
                    expected_plan_status = str(plan.get("status") or "")
                elif saga_status == "compensating":
                    client = RuntimeManagerClient()
                    _, expected_plan_status = execute_compensation(
                        api_url=api_url,
                        saga=saga,
                        plan=plan,
                        event=event,
                        client=client,
                        incident_url=os.getenv("PANTHEON_INCIDENTS_API_URL", "").strip(),
                        timeout_seconds=timeout_seconds,
                    )
                    saga = fetch_saga(
                        api_url=api_url,
                        saga_id=saga_id,
                        timeout_seconds=timeout_seconds,
                    )
                else:
                    raise RuntimeError(
                        f"Saga {saga_id!r} cannot execute compensation from status={saga_status!r}"
                    )
                projection = fetch_projection(
                    api_url=api_url,
                    plan_id=plan_id,
                    timeout_seconds=timeout_seconds,
                )
                projection_mismatch = validate_compensation_projection(
                    saga=saga,
                    projection=projection,
                    expected_plan_status=expected_plan_status,
                )
                if projection_mismatch:
                    raise RuntimeError(
                        f"terminal compensation projection did not converge: {projection_mismatch}"
                    )
                receipt = consume_event(
                    api_url=api_url,
                    event_id=event_id,
                    consumer_name=consumer_name,
                    timeout_seconds=timeout_seconds,
                )
                applied, duplicate, receipt_error = _apply_receipt_counts(
                    receipt=receipt, event_id=event_id
                )
                consumed += applied
                duplicates += duplicate
                if receipt_error:
                    errors.append(receipt_error)
            else:
                # --- RECEIPT-ONLY CONSUMER FOR ALL OTHER EVENTS ---
                receipt = consume_event(
                    api_url=api_url,
                    event_id=event_id,
                    consumer_name=consumer_name,
                    timeout_seconds=timeout_seconds,
                )
                applied, duplicate, receipt_error = _apply_receipt_counts(
                    receipt=receipt, event_id=event_id
                )
                consumed += applied
                duplicates += duplicate
                if receipt_error:
                    errors.append(receipt_error)
        except SequenceBlockedError as exc:
            skipped_not_due += 1
            errors.append(f"event_id={event_id} sequence_blocked={exc}")
        except urllib.error.HTTPError as exc:
            reason = f"event_id={event_id} http_error={exc.code} {exc.reason}"
            errors.append(reason)
            if record_failures:
                failure_record, failure_error = _record_failure_best_effort(
                    api_url=api_url,
                    event_id=event_id,
                    consumer_name=consumer_name,
                    reason=reason,
                    retryable=_http_error_retryable(exc),
                    max_attempts=max_attempts,
                    retry_delay_seconds=retry_delay_seconds,
                    timeout_seconds=timeout_seconds,
                )
                if failure_error:
                    errors.append(f"event_id={event_id} failure_record_error={failure_error}")
                elif failure_record and failure_record.get("status") == "dead_lettered":
                    dead_lettered += 1
                else:
                    retry_scheduled += 1
        except Exception as exc:  # noqa: BLE001
            reason = f"event_id={event_id} error={exc}"
            errors.append(reason)
            if record_failures:
                failure_record, failure_error = _record_failure_best_effort(
                    api_url=api_url,
                    event_id=event_id,
                    consumer_name=consumer_name,
                    reason=reason,
                    retryable=True,
                    max_attempts=max_attempts,
                    retry_delay_seconds=retry_delay_seconds,
                    timeout_seconds=timeout_seconds,
                )
                if failure_error:
                    errors.append(f"event_id={event_id} failure_record_error={failure_error}")
                elif failure_record and failure_record.get("status") == "dead_lettered":
                    dead_lettered += 1
                else:
                    retry_scheduled += 1

    return {
        "events_found": len(events),
        "consumed": consumed,
        "duplicates": duplicates,
        "skipped_not_due": skipped_not_due,
        "retry_scheduled": retry_scheduled,
        "dead_lettered": dead_lettered,
        "errors": errors,
    }


def _write_health(path: str, state: dict[str, Any]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except OSError:
        pass


def main() -> int:
    api_url = os.getenv("DEPLOYMENT_API_URL", "http://127.0.0.1:8095")
    consumer_name = os.getenv("DEPLOYMENT_OUTBOX_CONSUMER_NAME", _CONSUMER_NAME)
    interval_seconds = _env_int("DEPLOYMENT_OUTBOX_CONSUMER_INTERVAL_SECONDS", 10, minimum=1)
    max_ticks = _env_int("DEPLOYMENT_OUTBOX_CONSUMER_MAX_TICKS", 0, minimum=0)
    timeout_seconds = float(os.getenv("DEPLOYMENT_OUTBOX_CONSUMER_TIMEOUT_SECONDS", "10"))
    max_attempts = _env_int("DEPLOYMENT_OUTBOX_CONSUMER_MAX_ATTEMPTS", 3, minimum=1)
    retry_delay_seconds = _env_int("DEPLOYMENT_OUTBOX_CONSUMER_RETRY_DELAY_SECONDS", 30, minimum=0)
    health_file = os.getenv("DEPLOYMENT_OUTBOX_CONSUMER_HEALTH_FILE", "")

    health: dict[str, Any] = {
        "consumer_name": consumer_name,
        "status": "starting",
        "total_consumed": 0,
        "total_duplicates": 0,
        "total_errors": 0,
        "total_retry_scheduled": 0,
        "total_dead_lettered": 0,
        "ticks": 0,
        "last_success": None,
        "last_failure": None,
        "last_failure_reason": None,
        "retry_policy": {
            "max_attempts": max_attempts,
            "retry_delay_seconds": retry_delay_seconds,
        },
    }

    tick = 0
    while True:
        tick += 1
        try:
            result = run_poll(
                api_url=api_url,
                consumer_name=consumer_name,
                timeout_seconds=timeout_seconds,
                record_failures=True,
                max_attempts=max_attempts,
                retry_delay_seconds=retry_delay_seconds,
            )
            health["ticks"] = tick
            health["total_consumed"] += result["consumed"]
            health["total_duplicates"] += result["duplicates"]
            health["total_retry_scheduled"] += result["retry_scheduled"]
            health["total_dead_lettered"] += result["dead_lettered"]
            if result["errors"]:
                health["total_errors"] += len(result["errors"])
                health["status"] = "degraded"
                health["last_failure"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                health["last_failure_reason"] = "; ".join(result["errors"])
            else:
                if health["status"] != "degraded" or result["consumed"] > 0:
                    health["status"] = "ok"
                health["last_success"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            if health_file:
                _write_health(health_file, health)
        except Exception as exc:  # noqa: BLE001
            health["ticks"] = tick
            health["total_errors"] += 1
            health["status"] = "degraded"
            health["last_failure"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            health["last_failure_reason"] = str(exc)
            result = {
                "events_found": 0,
                "consumed": 0,
                "duplicates": 0,
                "skipped_not_due": 0,
                "retry_scheduled": 0,
                "dead_lettered": 0,
                "errors": [str(exc)],
            }
            if health_file:
                _write_health(health_file, health)

        print(
            json.dumps({"tick": tick, "health": health, "result": result}, sort_keys=True),
            flush=True,
        )

        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover - exercised through compose/smoke.
    raise SystemExit(main())
