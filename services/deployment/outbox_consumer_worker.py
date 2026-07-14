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
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

                # 4. Construct deploy_context
                deploy_context = {
                    "persona_capital_binding_id": compat.get("persona_binding_id"),
                    "persona_capital_binding_status": "active" if compat.get("persona_scope_ok") else "inactive",
                    "allowed_deployment_scope": compat.get("allowed_deployment_scope"),
                    "loader_checks_passed": True,
                    "plan_status": plan.get("status") or "approved",
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
