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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

# Ensure the deployment, runtime-manager, and repository modules are importable
# both from ``python -m`` and from the service Docker entrypoint.
_DEPLOYMENT_DIR = str(Path(__file__).resolve().parent)
_RUNTIME_MANAGER_DIR = str(Path(__file__).resolve().parent.parent / "runtime-manager")
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
for _module_path in (_DEPLOYMENT_DIR, _RUNTIME_MANAGER_DIR, _REPO_ROOT):
    if _module_path not in sys.path:
        sys.path.insert(0, _module_path)

from runtime_manager_dispatch_adapter import (
    dispatch_to_runtime_manager,
    DispatchOutcome,
    DispatchResult,
    validate_authoritative_readback,
)
from runtime_manager_client import RuntimeManagerClient
from deploy_authority import (
    DeployAuthorityError,
    DeployAuthorityUnavailableError,
    verify_deploy_authorities,
)

_CONSUMER_NAME = "deployment-outbox-consumer"
_TERMINAL_SAGA_STATUSES = {"failed", "aborted"}
_CLAIM_TOKENS: dict[str, str] = {}


class SequenceBlockedError(RuntimeError):
    """An earlier aggregate event must be replayed before this side effect."""


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _deployment_headers(*, json_body: bool = False) -> dict[str, str]:
    token = os.getenv("PANTHEON_DEPLOYMENT_SERVICE_TOKEN", "").strip()
    tenant_id = os.getenv("PANTHEON_DEPLOYMENT_TENANT_ID", "").strip()
    if not token:
        raise RuntimeError("PANTHEON_DEPLOYMENT_SERVICE_TOKEN is required")
    if not tenant_id:
        raise RuntimeError("PANTHEON_DEPLOYMENT_TENANT_ID is required")
    headers = {
        "Accept": "application/json",
        "Authorization": (
            token if token.lower().startswith("bearer ") else f"Bearer {token}"
        ),
        "X-Tenant-Id": tenant_id,
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def fetch_pending_outbox(
    *,
    api_url: str,
    consumer_name: str = _CONSUMER_NAME,
    timeout_seconds: float = 10.0,
    aggregate_id: str | None = None,
) -> list[dict[str, Any]]:
    """Claim pending records, optionally isolated to one deployment saga."""
    url = api_url.rstrip("/") + "/api/deployment/outbox/claim"
    payload = json.dumps(
        {
            "consumer_name": consumer_name,
            "lease_seconds": _env_int(
                "DEPLOYMENT_OUTBOX_CONSUMER_LEASE_SECONDS", 60, minimum=1
            ),
            "limit": _env_int(
                "DEPLOYMENT_OUTBOX_CONSUMER_CLAIM_LIMIT", 25, minimum=1
            ),
            "aggregate_id": aggregate_id,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers=_deployment_headers(json_body=True),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    records = json.loads(body) if body else []
    for record in records:
        event = record.get("event") if isinstance(record, Mapping) else None
        event_id = str(event.get("event_id") or "") if isinstance(event, Mapping) else ""
        claim_token = str(record.get("claim_token") or "")
        if event_id and claim_token:
            _CLAIM_TOKENS[event_id] = claim_token
    return records


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
        url, headers=_deployment_headers(), method="GET"
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
    payload = json.dumps(
        {
            "consumer_name": consumer_name,
            "claim_token": _CLAIM_TOKENS.get(event_id),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers=_deployment_headers(json_body=True),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    receipt = json.loads(body) if body else {}
    if receipt.get("status") in {"applied", "duplicate"}:
        _CLAIM_TOKENS.pop(event_id, None)
    return receipt


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
            "claim_token": _CLAIM_TOKENS.get(event_id),
            "reason": reason,
            "retryable": retryable,
            "max_attempts": max_attempts,
            "retry_delay_seconds": retry_delay_seconds,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers=_deployment_headers(json_body=True),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    record = json.loads(body) if body else {}
    _CLAIM_TOKENS.pop(event_id, None)
    return record


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
        url, headers=_deployment_headers(), method="GET"
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def fetch_plan(*, api_url: str, plan_id: str, timeout_seconds: float = 10.0) -> dict[str, Any]:
    url = api_url.rstrip("/") + f"/api/deployment/plans/{plan_id}"
    request = urllib.request.Request(
        url, headers=_deployment_headers(), method="GET"
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
        url, headers=_deployment_headers(), method="GET"
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
        headers=_deployment_headers(json_body=True),
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
        headers=_deployment_headers(json_body=True),
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
        headers=_deployment_headers(json_body=True),
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
        headers=_deployment_headers(json_body=True),
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
        headers=_deployment_headers(json_body=True),
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
        headers=_deployment_headers(json_body=True),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def _is_same_origin(url: str, base_url: str) -> bool:
    if not url or not base_url:
        return False
    target = urllib.parse.urlparse(url)
    base = urllib.parse.urlparse(base_url)
    if not base.netloc:
        return url.startswith(base_url.rstrip("/"))
    return (
        target.scheme == base.scheme
        and target.netloc == base.netloc
        and (not base.path.rstrip("/") or target.path.startswith(base.path.rstrip("/")))
    )


class _AuthorityRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Revalidate origin on 30x redirects and strip credentials when leaving deployment origin."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Mapping[str, str],
        newurl: str,
    ) -> urllib.request.Request | None:
        absolute_url = urllib.parse.urljoin(req.full_url, newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, absolute_url)
        if redirected is not None:
            deployment_url = os.getenv("DEPLOYMENT_API_URL", "http://127.0.0.1:8095").rstrip("/")
            if not _is_same_origin(absolute_url, deployment_url):
                for h in list(redirected.headers.keys()):
                    if h.lower() in {"authorization", "x-tenant-id"}:
                        redirected.headers.pop(h, None)
                for h in list(redirected.unredirected_hdrs.keys()):
                    if h.lower() in {"authorization", "x-tenant-id"}:
                        redirected.unredirected_hdrs.pop(h, None)
        return redirected


def _fetch_authority_json(url: str, timeout_seconds: float) -> Mapping[str, Any]:
    deployment_url = os.getenv("DEPLOYMENT_API_URL", "http://127.0.0.1:8095").rstrip("/")
    headers = {"Accept": "application/json"}
    if _is_same_origin(url, deployment_url):
        headers.update(_deployment_headers())
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(_AuthorityRedirectHandler())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_type = (
            DeployAuthorityUnavailableError
            if exc.code in {408, 425, 429} or 500 <= exc.code <= 599
            else DeployAuthorityError
        )
        raise error_type(f"authoritative read {url!r} returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DeployAuthorityUnavailableError(
            f"authoritative read {url!r} is unavailable: {getattr(exc, 'reason', exc)}"
        ) from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeployAuthorityError(
            f"authoritative read {url!r} did not return JSON"
        ) from exc
    if not isinstance(payload, Mapping):
        raise DeployAuthorityError(
            f"authoritative read {url!r} must return a JSON object"
        )
    return payload


def verify_binding_deploy_authorities(
    *,
    saga: Mapping[str, Any],
    plan: Mapping[str, Any],
    persona_capital_binding_id: str,
    persona_capital_binding_status: str,
    allowed_deployment_scope: str,
    deployment_base_url: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Bind canonical plan/saga identity to Registry and Governance truth.

    Neither a plan metadata boolean nor an outbox payload is loader or approval
    proof.  The worker first proves the canonical DeploymentPlan and
    DeploymentSaga describe the same immutable target, then performs exact GET
    readbacks from the Registry and Governance owners.  Runtime-manager repeats
    the same proof at its write boundary to close the caller-forgery path.
    """

    identity_fields = (
        "plan_id",
        "approval_decision_id",
        "strategy_id",
        "artifact_id",
        "artifact_version",
        "capital_pool_id",
        "target_stage",
    )
    mismatches: list[str] = []
    for field in identity_fields:
        saga_value = str(saga.get(field) or "").strip()
        plan_value = str(plan.get(field) or "").strip()
        if not saga_value:
            mismatches.append(f"DeploymentSaga.{field} is required")
        if not plan_value:
            mismatches.append(f"DeploymentPlan.{field} is required")
        if saga_value and plan_value and saga_value != plan_value:
            mismatches.append(
                f"{field} mismatch: saga={saga_value!r}, plan={plan_value!r}"
            )
    sponsor_persona_id = str(plan.get("sponsor_persona_id") or "").strip()
    if not sponsor_persona_id:
        mismatches.append("DeploymentPlan.sponsor_persona_id is required")
    if mismatches:
        raise DeployAuthorityError(
            "deployment canonical identity mismatch: " + "; ".join(mismatches)
        )

    request = {
        field: str(saga.get(field) or "").strip() for field in identity_fields
    }
    request.update(
        {
            "plan_status": str(plan.get("status") or "").strip(),
            "sponsor_persona_id": sponsor_persona_id,
            "persona_capital_binding_id": str(
                persona_capital_binding_id or ""
            ).strip(),
            "persona_capital_binding_status": str(
                persona_capital_binding_status or ""
            ).strip(),
            "allowed_deployment_scope": str(
                allowed_deployment_scope or ""
            ).strip(),
        }
    )
    return verify_deploy_authorities(
        request,
        deployment_base_url=deployment_base_url,
        registry_base_url=(
            os.getenv("PANTHEON_REGISTRY_API_URL")
            or os.getenv("PANTHEON_REGISTRY_SERVICE_URL", "")
        ),
        governance_base_url=os.getenv(
            "PANTHEON_GOVERNANCE_APPROVAL_API_URL", ""
        ),
        capital_base_url=os.getenv("PANTHEON_CAPITAL_API_URL", ""),
        timeout_seconds=timeout_seconds,
        fetch_json=_fetch_authority_json,
        allowed_target_stages=(str(plan.get("target_stage") or ""),),
        allowed_registry_deployment_stages=(
            (str(plan.get("current_stage") or ""),)
            if str(plan.get("target_stage") or "") in {"canary", "live"}
            else ("none", "paper")
        ),
    )


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


def _compensation_kill_payload(
    *,
    saga: Mapping[str, Any],
    binding: Mapping[str, Any],
    event_id: str,
    event_idempotency_key: str,
) -> dict[str, Any]:
    """Build the immutable kill command used by every containment replay.

    The diagnostic reason that led to containment can change after a
    response-loss replay (for example, from a loader failure to an already
    paused safe-mode observation).  Runtime-manager idempotency hashes the
    complete request, so that diagnostic must stay in the IncidentCase rather
    than the kill command payload.
    """
    return {
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
            "reason": "deployment_compensation_fail_closed",
        },
    }


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
        _compensation_kill_payload(
            saga=saga,
            binding=binding,
            event_id=event_id,
            event_idempotency_key=event_idempotency_key,
        )
    )
    ack = kill_result.get("telemetry_ack")
    if not isinstance(ack, Mapping) or ack.get("ack_status") != "acknowledged":
        raise RuntimeError(
            f"kill-switch compensation did not produce acknowledged runtime follow-through: {ack!r}"
        )
    expected_pool_id = str(saga.get("capital_pool_id") or "")
    contained_binding_id = str(ack.get("runtime_binding_id") or "")
    ack_mismatches = []
    if ack.get("capital_pool_id") != expected_pool_id:
        ack_mismatches.append(
            f"capital_pool_id expected {expected_pool_id!r}, got {ack.get('capital_pool_id')!r}"
        )
    if ack.get("safe_mode_after") != "paused":
        ack_mismatches.append(
            f"safe_mode_after expected 'paused', got {ack.get('safe_mode_after')!r}"
        )
    if ack.get("runtime_status_after") != "paused":
        ack_mismatches.append(
            f"runtime_status_after expected 'paused', got {ack.get('runtime_status_after')!r}"
        )
    if not contained_binding_id:
        ack_mismatches.append("runtime_binding_id is required")
    if ack_mismatches:
        raise RuntimeError(
            "kill-switch compensation acknowledgement mismatch: "
            + "; ".join(ack_mismatches)
        )
    safe_mode = client.get_safe_mode(str(saga.get("capital_pool_id") or ""))
    if safe_mode.get("safe_mode_state") != "paused":
        raise RuntimeError(
            f"safe-mode readback expected 'paused', got {safe_mode.get('safe_mode_state')!r}"
        )
    contained = client.get(contained_binding_id)
    if contained is None or contained.get("status") != "paused":
        raise RuntimeError(
            "kill-switch readback did not prove the targeted RuntimeBinding is paused"
        )
    if contained.get("capital_pool_id") != expected_pool_id:
        raise RuntimeError(
            "kill-switch readback RuntimeBinding belongs to a different capital pool"
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


def _rollback_prior_authority_error(
    *,
    prior: Mapping[str, Any],
    source_binding: Mapping[str, Any],
    rollback: Mapping[str, Any],
    target_plan: Mapping[str, Any],
) -> str | None:
    """Validate the persisted four-owner proof on an exact retired target."""

    metadata = prior.get("metadata")
    attestation = (
        metadata.get("authoritative_loader_attestation")
        if isinstance(metadata, Mapping)
        else None
    )
    if not isinstance(attestation, Mapping):
        return "prior RuntimeBinding is missing canonical authority proof"
    expected = {
        "status": "passed",
        "authority": "canonical_deployment_registry_governance_capital",
        "plan_id": prior.get("plan_id"),
        "target_stage": source_binding.get("deployment_mode"),
        "artifact_id": rollback.get("target_artifact_id"),
        "artifact_version": rollback.get("target_version"),
        "capital_pool_id": source_binding.get("capital_pool_id"),
        "persona_capital_binding_id": source_binding.get(
            "persona_capital_binding_id"
        ),
    }
    mismatches = [
        f"rollback authority {field} expected {value!r}, got {attestation.get(field)!r}"
        for field, value in expected.items()
        if attestation.get(field) != value
    ]
    digest_fields = (
        "deployment_plan_sha256",
        "registry_entry_sha256",
        "approval_decision_sha256",
        "capital_pool_sha256",
        "capital_admissibility_sha256",
        "persona_capital_binding_sha256",
    )
    mismatches.extend(
        f"rollback authority {field} is missing or invalid"
        for field in digest_fields
        if not str(attestation.get(field) or "").startswith("sha256:")
        or len(str(attestation.get(field) or "")) != 71
    )
    strategy_id = str(attestation.get("strategy_id") or "")
    if not strategy_id or not isinstance(metadata, Mapping) or metadata.get(
        "strategy_id"
    ) != strategy_id:
        mismatches.append("rollback authority strategy_id is missing or inconsistent")
    allowed_scope = str(attestation.get("allowed_deployment_scope") or "")
    deployment_scope = str(source_binding.get("deployment_mode") or "")
    scope_rank = {"none": 0, "paper": 1, "canary": 2, "live": 3}
    if (
        allowed_scope not in scope_rank
        or deployment_scope not in scope_rank
        or scope_rank[allowed_scope] < scope_rank[deployment_scope]
    ):
        mismatches.append(
            "rollback authority allowed_deployment_scope must permit the "
            f"source deployment_mode; scope={allowed_scope!r}, "
            f"deployment_mode={deployment_scope!r}"
        )
    target_expected = {
        "plan_id": prior.get("plan_id"),
        "artifact_id": rollback.get("target_artifact_id"),
        "artifact_version": rollback.get("target_version"),
        "target_stage": source_binding.get("deployment_mode"),
        "capital_pool_id": source_binding.get("capital_pool_id"),
        "strategy_id": strategy_id,
    }
    mismatches.extend(
        f"fallback DeploymentPlan {field} expected {value!r}, got {target_plan.get(field)!r}"
        for field, value in target_expected.items()
        if target_plan.get(field) != value
    )
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
    safe_mode_readback = client.get_safe_mode(
        str(saga.get("capital_pool_id") or "")
    )
    safe_mode_state = (
        safe_mode_readback.get("safe_mode_state")
        if isinstance(safe_mode_readback, Mapping)
        else None
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
    if safe_mode_state and safe_mode_state not in {"normal", "normal_restored"}:
        # Keep the immutable compensation kill request pointed at the saga
        # source.  Runtime-manager resolves a stale retired source to the sole
        # current non-terminal child and returns that identity in telemetry.
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
        prior_candidates = [
            candidate
            for candidate in pool_bindings
            if candidate.get("binding_id") != binding.get("binding_id")
            and candidate.get("artifact_id") == rollback.get("target_artifact_id")
            and candidate.get("artifact_version") == rollback.get("target_version")
            and candidate.get("deployment_mode") == binding.get("deployment_mode")
            and candidate.get("execution_mode")
            == (binding.get("execution_mode") or binding.get("deployment_mode"))
            and candidate.get("capital_pool_id") == binding.get("capital_pool_id")
            and candidate.get("persona_capital_binding_id")
            == binding.get("persona_capital_binding_id")
            and candidate.get("status") == "retired"
            and candidate.get("plan_id")
        ]
        if len(prior_candidates) != 1:
            return _contain_and_raise_incident(
                client=client,
                saga=saga,
                binding=binding,
                event_id=event_id,
                event_idempotency_key=event_idempotency_key,
                incident_url=incident_url,
                reason=(
                    "rollback target must resolve to exactly one retired prior "
                    f"RuntimeBinding; found {len(prior_candidates)}"
                ),
                timeout_seconds=timeout_seconds,
            )
        prior = prior_candidates[0]
        target_plan = fetch_plan(
            api_url=api_url,
            plan_id=str(prior.get("plan_id")),
            timeout_seconds=timeout_seconds,
        )
        authority_error = _rollback_prior_authority_error(
            prior=prior,
            source_binding=binding,
            rollback=rollback,
            target_plan=target_plan,
        )
        if authority_error:
            return _contain_and_raise_incident(
                client=client,
                saga=saga,
                binding=binding,
                event_id=event_id,
                event_idempotency_key=event_idempotency_key,
                incident_url=incident_url,
                reason=f"rollback prior authority failed closed: {authority_error}",
                timeout_seconds=timeout_seconds,
            )
        prior_metadata = prior.get("metadata")
        prior_attestation = (
            prior_metadata.get("authoritative_loader_attestation")
            if isinstance(prior_metadata, Mapping)
            else None
        )
        if not isinstance(prior_attestation, Mapping):  # defensive; validated above
            raise RuntimeError("rollback prior canonical authority proof disappeared")
        rollback_authority_request = {
            "plan_id": target_plan.get("plan_id"),
            "plan_status": target_plan.get("status"),
            "target_stage": target_plan.get("target_stage"),
            "artifact_id": target_plan.get("artifact_id"),
            "artifact_version": target_plan.get("artifact_version"),
            "strategy_id": target_plan.get("strategy_id"),
            "approval_decision_id": target_plan.get("approval_decision_id"),
            "sponsor_persona_id": target_plan.get("sponsor_persona_id"),
            "capital_pool_id": target_plan.get("capital_pool_id"),
            "persona_capital_binding_id": prior.get(
                "persona_capital_binding_id"
            ),
            "persona_capital_binding_status": "active",
            "allowed_deployment_scope": prior_attestation.get(
                "allowed_deployment_scope"
            ),
        }
        try:
            current_rollback_authority = verify_deploy_authorities(
                rollback_authority_request,
                deployment_base_url=api_url,
                registry_base_url=(
                    os.getenv("PANTHEON_REGISTRY_API_URL")
                    or os.getenv("PANTHEON_REGISTRY_SERVICE_URL", "")
                ),
                governance_base_url=os.getenv(
                    "PANTHEON_GOVERNANCE_APPROVAL_API_URL", ""
                ),
                capital_base_url=os.getenv("PANTHEON_CAPITAL_API_URL", ""),
                timeout_seconds=timeout_seconds,
                fetch_json=_fetch_authority_json,
                allowed_plan_statuses=("approved", "executing", "executed"),
            )
        except DeployAuthorityUnavailableError:
            raise
        except DeployAuthorityError as exc:
            return _contain_and_raise_incident(
                client=client,
                saga=saga,
                binding=binding,
                event_id=event_id,
                event_idempotency_key=event_idempotency_key,
                incident_url=incident_url,
                reason=f"rollback current authority rejected: {exc}",
                timeout_seconds=timeout_seconds,
            )
        result = client.rollback(
            {
                "current_binding_id": binding.get("binding_id"),
                "action_type": action_type,
                "replacement_plan_id": target_plan.get("plan_id"),
                "replacement_plan_status": target_plan.get("status"),
                "replacement_artifact_id": rollback.get("target_artifact_id"),
                "replacement_artifact_version": rollback.get("target_version"),
                "replacement_persona_capital_binding_id": binding.get(
                    "persona_capital_binding_id"
                ),
                "replacement_persona_capital_binding_status": "active",
                "replacement_allowed_deployment_scope": prior_attestation[
                    "allowed_deployment_scope"
                ],
                "replacement_deployment_mode": binding.get("deployment_mode"),
                "opened_by_artifact_id": binding.get("artifact_id"),
                "replacement_strategy_id": prior_attestation["strategy_id"],
                "replacement_authority_attestation": current_rollback_authority,
                "replacement_metadata": {
                    "compensation_event_id": event_id,
                    "compensation_idempotency_key": event_idempotency_key,
                    "deployment_saga_id": saga.get("saga_id"),
                    "rollback_source_plan_id": saga.get("plan_id"),
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
    replacement_status = str(refreshed_new.get("status") or "")
    if replacement_status in {"pending_pause", "paused"}:
        return _contain_and_raise_incident(
            client=client,
            saga=saga,
            binding=binding,
            event_id=event_id,
            event_idempotency_key=event_idempotency_key,
            incident_url=incident_url,
            reason=(
                "kill-switch containment won during rollback; replacement "
                f"{refreshed_new.get('binding_id')} status={replacement_status}"
            ),
            timeout_seconds=timeout_seconds,
        )
    if replacement_status != "active":
        raise RuntimeError(
            "rollback replacement status expected active/contained, got "
            f"{replacement_status!r}"
        )
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


def _verify_containment_readback(
    *,
    client: RuntimeManagerClient,
    saga: Mapping[str, Any],
    binding: Mapping[str, Any],
    event: Mapping[str, Any],
    incident_url: str,
    timeout_seconds: float,
    require_saga_identity: bool = True,
) -> None:
    """Re-prove paused safe mode and the exact IncidentCase without writes."""
    if not incident_url:
        raise RuntimeError(
            "PANTHEON_INCIDENTS_API_URL is required to verify compensation containment"
        )
    binding_id = str(binding.get("binding_id") or "")
    if not binding_id:
        raise RuntimeError("containment verification requires a RuntimeBinding identity")
    safe_mode = client.get_safe_mode(str(saga.get("capital_pool_id") or ""))
    if safe_mode.get("safe_mode_state") != "paused":
        raise RuntimeError(
            "terminal compensation replay did not prove paused safe mode: "
            f"{safe_mode.get('safe_mode_state')!r}"
        )
    contained = client.get(binding_id)
    if contained is None:
        raise RuntimeError(
            f"terminal compensation RuntimeBinding {binding_id!r} is not readable"
        )
    if require_saga_identity:
        identity_error = _binding_identity_error(
            saga=saga,
            binding=contained,
            expected_binding_id=binding_id,
        )
        if identity_error:
            raise RuntimeError(
                f"terminal containment binding identity mismatch: {identity_error}"
            )
    elif contained.get("capital_pool_id") != saga.get("capital_pool_id"):
        raise RuntimeError(
            "terminal containment binding belongs to a different capital pool"
        )
    if contained.get("status") != "paused":
        raise RuntimeError(
            "terminal compensation replay did not prove the RuntimeBinding is paused"
        )
    event_id = str(event.get("event_id") or "")
    expected_incident = _incident_payload(
        saga=saga,
        binding=contained,
        event_id=event_id,
        reason="terminal compensation replay verification",
    )
    incident = fetch_incident(
        base_url=incident_url,
        incident_id=str(expected_incident["incident_id"]),
        timeout_seconds=timeout_seconds,
    )
    incident_error = _incident_mismatch(expected_incident, incident)
    if incident_error:
        raise RuntimeError(
            f"terminal compensation incident readback mismatch: {incident_error}"
        )


def verify_terminal_compensation_side_effects(
    *,
    saga: Mapping[str, Any],
    plan: Mapping[str, Any],
    event: Mapping[str, Any],
    client: RuntimeManagerClient,
    incident_url: str,
    timeout_seconds: float,
) -> str:
    """Re-prove the command owner's terminal state before replay is consumed.

    A terminal saga/projection says orchestration finalized; it cannot stand in
    for the runtime-manager or incident owner readback that justified that
    finalization.
    """
    decision = saga.get("compensation")
    if not isinstance(decision, Mapping):
        payload = event.get("payload")
        decision = payload.get("compensation") if isinstance(payload, Mapping) else None
    if not isinstance(decision, Mapping):
        raise RuntimeError("terminal compensation replay has no compensation decision")
    command = str(decision.get("command_type") or "")
    plan_id = str(saga.get("plan_id") or "")
    binding_id = str(saga.get("binding_id") or "")
    expected_plan_status = str(plan.get("status") or "")

    if command == "abort_plan":
        bindings = client.list_by_plan(plan_id)
        if not binding_id and not bindings:
            if plan.get("status") != "aborted":
                raise RuntimeError(
                    "terminal abort replay did not prove DeploymentPlan.status='aborted'"
                )
            return "aborted"
        target = client.get(binding_id) if binding_id else bindings[0]
        if not isinstance(target, Mapping):
            raise RuntimeError(
                "terminal abort replay found a binding identity but no authoritative record"
            )
        _verify_containment_readback(
            client=client,
            saga=saga,
            binding=target,
            event=event,
            incident_url=incident_url,
            timeout_seconds=timeout_seconds,
        )
        return expected_plan_status

    if command == "mark_binding_failed_inactive":
        binding = client.get(binding_id) if binding_id else None
        if binding is None:
            if client.list_by_plan(plan_id):
                raise RuntimeError(
                    "terminal failed-binding replay found unexpected plan bindings"
                )
            return expected_plan_status
        identity_error = _binding_identity_error(
            saga=saga,
            binding=binding,
            expected_binding_id=binding_id,
        )
        if identity_error:
            raise RuntimeError(
                f"terminal failed-binding identity mismatch: {identity_error}"
            )
        if binding.get("status") != "failed":
            raise RuntimeError(
                "terminal failed-binding replay expected status='failed', got "
                f"{binding.get('status')!r}"
            )
        return expected_plan_status

    if command == "request_rollback":
        binding = client.get(binding_id) if binding_id else None
        if not isinstance(binding, Mapping):
            raise RuntimeError(
                "terminal rollback replay requires the source RuntimeBinding"
            )
        identity_error = _binding_identity_error(
            saga=saga,
            binding=binding,
            expected_binding_id=binding_id,
        )
        if identity_error:
            raise RuntimeError(
                f"terminal rollback source identity mismatch: {identity_error}"
            )
        rollback = plan.get("rollback")
        children: list[Mapping[str, Any]] = []
        if isinstance(rollback, Mapping):
            action_type = str(rollback.get("action_type") or "replace")
            children = [
                candidate
                for candidate in client.list_by_pool(
                    str(saga.get("capital_pool_id") or "")
                )
                if candidate.get("rollback_parent") == binding_id
                and candidate.get("rollback_action_type") == action_type
                and candidate.get("artifact_id") == rollback.get("target_artifact_id")
                and candidate.get("artifact_version") == rollback.get("target_version")
            ]
            if len(children) > 1:
                raise RuntimeError(
                    f"terminal rollback replay found {len(children)} matching children"
                )
            if children:
                if binding.get("status") != "retired":
                    raise RuntimeError(
                        "terminal rollback replay did not prove the source binding retired"
                    )
                child = client.get(str(children[0].get("binding_id") or ""))
                if not isinstance(child, Mapping):
                    raise RuntimeError(
                        "terminal rollback replacement RuntimeBinding is not readable"
                    )
                child_error = _rollback_binding_error(
                    old_binding=binding,
                    new_binding=child,
                    rollback=rollback,
                    action_type=action_type,
                )
                if child_error:
                    raise RuntimeError(
                        f"terminal rollback replacement mismatch: {child_error}"
                    )
                child_status = str(child.get("status") or "")
                if child_status == "paused":
                    _verify_containment_readback(
                        client=client,
                        saga=saga,
                        binding=child,
                        event=event,
                        incident_url=incident_url,
                        timeout_seconds=timeout_seconds,
                        require_saga_identity=False,
                    )
                    return expected_plan_status
                if child_status != "active":
                    raise RuntimeError(
                        "terminal rollback replacement expected active/paused, got "
                        f"{child_status!r}"
                    )
                active = client.get_active_for_pool(
                    str(saga.get("capital_pool_id") or "")
                )
                if active is None or active.get("binding_id") != child.get("binding_id"):
                    raise RuntimeError(
                        "terminal rollback replacement is not authoritative active pool owner"
                    )
                return expected_plan_status
        _verify_containment_readback(
            client=client,
            saga=saga,
            binding=binding,
            event=event,
            incident_url=incident_url,
            timeout_seconds=timeout_seconds,
        )
        return expected_plan_status

    if command == "enter_safe_mode_and_raise_incident":
        binding = client.get(binding_id) if binding_id else None
        if not isinstance(binding, Mapping):
            raise RuntimeError(
                "terminal safe-mode replay requires the source RuntimeBinding"
            )
        _verify_containment_readback(
            client=client,
            saga=saga,
            binding=binding,
            event=event,
            incident_url=incident_url,
            timeout_seconds=timeout_seconds,
        )
        return expected_plan_status

    raise RuntimeError(
        f"unsupported terminal compensation command_type={command!r}"
    )


def _delivery_will_dead_letter(
    *, record: Mapping[str, Any], retryable: bool, max_attempts: int
) -> bool:
    attempts = int(record.get("delivery_attempts") or 0)
    return not retryable or attempts + 1 >= max_attempts


def _trigger_delivery_compensation(
    *,
    api_url: str,
    saga_id: str,
    event_type: str,
    reason: str,
    timeout_seconds: float,
) -> None:
    """Durably hand a terminal side-effect failure to saga compensation.

    The failed event is acknowledged only *after* this decision is durable.
    That acknowledgement is the causal predecessor of the compensation event;
    dead-lettering it would leave the successor permanently sequence-blocked.
    """
    latest = fetch_saga(
        api_url=api_url,
        saga_id=saga_id,
        timeout_seconds=timeout_seconds,
    )
    latest_status = str(latest.get("status") or "")
    if latest_status in {"compensating", "failed", "aborted"}:
        if not isinstance(latest.get("compensation"), Mapping):
            raise RuntimeError(
                "runtime-load saga is terminal/compensating without a durable compensation decision"
            )
        return
    if event_type == "runtime.binding.requested":
        failed_step = (
            "runtime_load_requested"
            if latest.get("binding_id")
            or latest_status in {"awaiting_runtime_load", "completed"}
            else "binding_requested"
        )
    elif event_type == "runtime.load.requested":
        failed_step = (
            "runtime_active"
            if latest_status == "completed"
            or latest.get("current_step") == "runtime_active"
            else "runtime_load_requested"
        )
    else:
        raise RuntimeError(
            f"terminal compensation handoff does not support event_type={event_type!r}"
        )
    try:
        record_saga_failure(
            api_url=api_url,
            saga_id=saga_id,
            reason=reason,
            failed_step=failed_step,
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        # A POST response can be lost after the service commits.  Authoritative
        # readback decides whether it is safe to proceed to DLQ.
        committed = fetch_saga(
            api_url=api_url,
            saga_id=saga_id,
            timeout_seconds=timeout_seconds,
        )
        if (
            str(committed.get("status") or "") == "compensating"
            and isinstance(committed.get("compensation"), Mapping)
        ):
            return
        raise
    committed = fetch_saga(
        api_url=api_url,
        saga_id=saga_id,
        timeout_seconds=timeout_seconds,
    )
    if (
        str(committed.get("status") or "") != "compensating"
        or not isinstance(committed.get("compensation"), Mapping)
    ):
        raise RuntimeError(
            "side-effect compensation request did not converge before acknowledgement"
        )


def _acknowledge_compensation_handoff(
    *,
    api_url: str,
    event_id: str,
    consumer_name: str,
    timeout_seconds: float,
) -> tuple[int, int]:
    """Advance the inbox sequence after durable compensation owns the failure."""
    receipt = consume_event(
        api_url=api_url,
        event_id=event_id,
        consumer_name=consumer_name,
        timeout_seconds=timeout_seconds,
    )
    applied, duplicate, receipt_error = _apply_receipt_counts(
        receipt=receipt,
        event_id=event_id,
    )
    if receipt_error:
        raise RuntimeError(receipt_error)
    return applied, duplicate


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
    aggregate_id: str | None = None,
) -> dict[str, Any]:
    """
    Fetch pending outbox events and consume each one.

    Returns a summary: events_found, consumed, duplicates, errors, retry_scheduled, dead_lettered.
    """
    events = fetch_pending_outbox(
        api_url=api_url,
        consumer_name=consumer_name,
        timeout_seconds=timeout_seconds,
        aggregate_id=aggregate_id,
    )
    consumed = 0
    duplicates = 0
    skipped_not_due = 0
    retry_scheduled = 0
    dead_lettered = 0
    errors: list[str] = []

    for record in events:
        compensation_committed = False
        if not _retry_due(record):
            skipped_not_due += 1
            continue
        event = record.get("event", {})
        event_id = event.get("event_id", "")
        if not event_id:
            errors.append(f"missing event_id in record: {record}")
            continue
        event_type = str(event.get("event_type") or "")
        aggregate_id = str(event.get("aggregate_id") or "")
        sequence_no = int(event.get("sequence_no") or 0)
        try:
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
                if saga_status in {"completed", "compensating", "failed", "aborted"}:
                    # This event no longer owns a mutable side effect.  If its
                    # receipt write fails, retain it for retry rather than
                    # converting terminal history into a DLQ record.
                    compensation_committed = True
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
                saga_metadata = (
                    dict(saga.get("metadata"))
                    if isinstance(saga.get("metadata"), Mapping)
                    else {}
                )
                plan_metadata = (
                    dict(plan.get("metadata"))
                    if isinstance(plan.get("metadata"), Mapping)
                    else {}
                )
                configured_tenant_id = os.getenv(
                    "PANTHEON_DEPLOYMENT_TENANT_ID", ""
                ).strip()
                saga_tenant_id = str(saga_metadata.get("tenant_id") or "").strip()
                plan_tenant_id = str(plan_metadata.get("tenant_id") or "").strip()
                if not configured_tenant_id:
                    raise ValueError("PANTHEON_DEPLOYMENT_TENANT_ID is required")
                if (
                    saga_tenant_id != configured_tenant_id
                    or plan_tenant_id != configured_tenant_id
                ):
                    raise ValueError(
                        "Deployment tenant correlation mismatch: "
                        f"configured={configured_tenant_id!r}, "
                        f"saga={saga_tenant_id!r}, plan={plan_tenant_id!r}"
                    )
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
                    _trigger_delivery_compensation(
                        api_url=api_url,
                        saga_id=saga_id,
                        reason=reason,
                        event_type=event_type,
                        timeout_seconds=timeout_seconds,
                    )
                    compensation_committed = True
                    applied, duplicate = _acknowledge_compensation_handoff(
                        api_url=api_url,
                        event_id=event_id,
                        consumer_name=consumer_name,
                        timeout_seconds=timeout_seconds,
                    )
                    consumed += applied
                    duplicates += duplicate
                    errors.append(reason)
                    continue

                try:
                    authority_report = verify_binding_deploy_authorities(
                        saga=saga,
                        plan=plan,
                        persona_capital_binding_id=str(
                            compat.get("persona_binding_id") or ""
                        ),
                        persona_capital_binding_status=(
                            "active" if compat.get("persona_scope_ok") else "inactive"
                        ),
                        allowed_deployment_scope=str(
                            compat.get("allowed_deployment_scope") or ""
                        ),
                        deployment_base_url=api_url,
                        timeout_seconds=timeout_seconds,
                    )
                except DeployAuthorityUnavailableError as exc:
                    reason = f"deploy authority unavailable: {exc}"
                    # Exhausting the retry budget is a durable orchestration
                    # failure, not a bare DLQ transition.  Persist the saga
                    # failure first so compensation/abort truth exists before
                    # sequence 1 is replayed by an operator.
                    if _delivery_will_dead_letter(
                        record=record,
                        retryable=True,
                        max_attempts=max_attempts,
                    ):
                        _trigger_delivery_compensation(
                            api_url=api_url,
                            saga_id=saga_id,
                            reason=reason,
                            event_type=event_type,
                            timeout_seconds=timeout_seconds,
                        )
                        compensation_committed = True
                        applied, duplicate = _acknowledge_compensation_handoff(
                            api_url=api_url,
                            event_id=event_id,
                            consumer_name=consumer_name,
                            timeout_seconds=timeout_seconds,
                        )
                        consumed += applied
                        duplicates += duplicate
                        errors.append(reason)
                        continue
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
                    errors.append(reason)
                    if failure_error:
                        errors.append(
                            f"event_id={event_id} failure_record_error={failure_error}"
                        )
                    elif failure_record and failure_record.get("status") == "dead_lettered":
                        dead_lettered += 1
                    else:
                        retry_scheduled += 1
                    continue
                except DeployAuthorityError as exc:
                    reason = f"deploy authority rejected: {exc}"
                    _trigger_delivery_compensation(
                        api_url=api_url,
                        saga_id=saga_id,
                        reason=reason,
                        event_type=event_type,
                        timeout_seconds=timeout_seconds,
                    )
                    compensation_committed = True
                    applied, duplicate = _acknowledge_compensation_handoff(
                        api_url=api_url,
                        event_id=event_id,
                        consumer_name=consumer_name,
                        timeout_seconds=timeout_seconds,
                    )
                    consumed += applied
                    duplicates += duplicate
                    errors.append(reason)
                    continue

                # 4. Construct deploy_context
                # Legacy caller assertions are deliberately not propagated as
                # proof.  Only the exact canonical report can occupy the
                # attestation slot persisted in RuntimeBinding metadata.
                plan_metadata.pop("loader_checks_passed", None)
                plan_metadata.pop("authoritative_loader_attestation", None)
                is_stage_promotion = (
                    (str(plan.get("current_stage") or ""), str(target_stage or ""))
                    in {("paper", "canary"), ("canary", "live")}
                    and str(plan.get("runtime_action") or "") == "replace_binding"
                )
                promotion_gate = (
                    dict(plan_metadata.get("promotion_gate"))
                    if isinstance(plan_metadata.get("promotion_gate"), Mapping)
                    else {}
                )
                human_gate_decision_id = str(
                    plan_metadata.get("human_gate_decision_id")
                    or promotion_gate.get("human_gate_decision_id")
                    or ""
                ).strip()
                authority_metadata_key = (
                    "authoritative_promotion_base_attestation"
                    if is_stage_promotion
                    else "authoritative_loader_attestation"
                )
                deploy_context = {
                    "sponsor_persona_id": sponsor_persona_id,
                    "persona_capital_binding_id": compat.get("persona_binding_id"),
                    "persona_capital_binding_status": "active" if compat.get("persona_scope_ok") else "inactive",
                    "allowed_deployment_scope": compat.get("allowed_deployment_scope"),
                    "loader_checks_passed": True,
                    "plan_status": plan.get("status") or "approved",
                    "idempotency_key": event.get("idempotency_key") or event_id,
                    "promotion_gate": promotion_gate,
                    "current_binding_id": plan.get("binding_id"),
                    "human_gate_decision_id": human_gate_decision_id,
                    "environment": str(
                        plan_metadata.get("environment")
                        or os.getenv("PANTHEON_ENVIRONMENT")
                        or ""
                    ).strip(),
                    "metadata": {
                        **plan_metadata,
                        "deployment_saga_id": saga_id,
                        "deployment_outbox_event_id": event_id,
                        "deployment_trace_id": saga.get("trace_id"),
                        "deployment_correlation_id": (
                            (
                                saga_metadata.get("foundation", {}).get(
                                    "trace_context", {}
                                )
                                if isinstance(
                                    saga_metadata.get("foundation"), Mapping
                                )
                                else {}
                            ).get("correlation_id")
                            or saga_metadata.get("correlation_id")
                            or saga.get("trace_id")
                        ),
                        authority_metadata_key: authority_report,
                    },
                }

                # Check for downstream-success-before-receipt idempotency:
                # If the saga doesn't have a binding_id recorded, but the binding already exists downstream
                # in the runtime-manager with our plan_id, reuse it.
                existing_binding_id = saga.get("binding_id")

                # Construct client
                client = RuntimeManagerClient(require_remote=True)

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
                    if _delivery_will_dead_letter(
                        record=record,
                        retryable=True,
                        max_attempts=max_attempts,
                    ):
                        _trigger_delivery_compensation(
                            api_url=api_url,
                            saga_id=saga_id,
                            event_type=event_type,
                            reason=reason,
                            timeout_seconds=timeout_seconds,
                        )
                        compensation_committed = True
                        applied, duplicate = _acknowledge_compensation_handoff(
                            api_url=api_url,
                            event_id=event_id,
                            consumer_name=consumer_name,
                            timeout_seconds=timeout_seconds,
                        )
                        consumed += applied
                        duplicates += duplicate
                        continue
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
                    _trigger_delivery_compensation(
                        api_url=api_url,
                        saga_id=saga_id,
                        reason=reason,
                        event_type=event_type,
                        timeout_seconds=timeout_seconds,
                    )
                    compensation_committed = True
                    applied, duplicate = _acknowledge_compensation_handoff(
                        api_url=api_url,
                        event_id=event_id,
                        consumer_name=consumer_name,
                        timeout_seconds=timeout_seconds,
                    )
                    consumed += applied
                    duplicates += duplicate
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
                    # Compensation owns terminal convergence.  This predecessor
                    # may advance the sequence but must never be DLQ'd if the
                    # receipt response is lost.
                    compensation_committed = True
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

                client = RuntimeManagerClient(require_remote=True)
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
                    _trigger_delivery_compensation(
                        api_url=api_url,
                        saga_id=saga_id,
                        reason=reason,
                        event_type=event_type,
                        timeout_seconds=timeout_seconds,
                    )
                    compensation_committed = True
                    applied, duplicate = _acknowledge_compensation_handoff(
                        api_url=api_url,
                        event_id=event_id,
                        consumer_name=consumer_name,
                        timeout_seconds=timeout_seconds,
                    )
                    consumed += applied
                    duplicates += duplicate
                    errors.append(reason)
                    continue

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
                    client = RuntimeManagerClient(require_remote=True)
                    expected_plan_status = verify_terminal_compensation_side_effects(
                        saga=saga,
                        plan=plan,
                        event=event,
                        client=client,
                        incident_url=os.getenv(
                            "PANTHEON_INCIDENTS_API_URL", ""
                        ).strip(),
                        timeout_seconds=timeout_seconds,
                    )
                elif saga_status == "compensating":
                    client = RuntimeManagerClient(require_remote=True)
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
                if compensation_committed:
                    retry_scheduled += 1
                    continue
                retryable = _http_error_retryable(exc)
                if (
                    event_type
                    in {"runtime.binding.requested", "runtime.load.requested"}
                    and aggregate_id
                    and _delivery_will_dead_letter(
                        record=record,
                        retryable=retryable,
                        max_attempts=max_attempts,
                    )
                ):
                    try:
                        _trigger_delivery_compensation(
                            api_url=api_url,
                            saga_id=aggregate_id,
                            event_type=event_type,
                            reason=reason,
                            timeout_seconds=timeout_seconds,
                        )
                        compensation_committed = True
                        applied, duplicate = _acknowledge_compensation_handoff(
                            api_url=api_url,
                            event_id=event_id,
                            consumer_name=consumer_name,
                            timeout_seconds=timeout_seconds,
                        )
                        consumed += applied
                        duplicates += duplicate
                        continue
                    except Exception as compensation_exc:  # noqa: BLE001
                        errors.append(
                            f"event_id={event_id} compensation_record_error="
                            f"{compensation_exc}"
                        )
                        continue
                failure_record, failure_error = _record_failure_best_effort(
                    api_url=api_url,
                    event_id=event_id,
                    consumer_name=consumer_name,
                    reason=reason,
                    retryable=retryable,
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
                if compensation_committed:
                    retry_scheduled += 1
                    continue
                if (
                    event_type
                    in {"runtime.binding.requested", "runtime.load.requested"}
                    and aggregate_id
                    and _delivery_will_dead_letter(
                        record=record,
                        retryable=True,
                        max_attempts=max_attempts,
                    )
                ):
                    try:
                        _trigger_delivery_compensation(
                            api_url=api_url,
                            saga_id=aggregate_id,
                            event_type=event_type,
                            reason=reason,
                            timeout_seconds=timeout_seconds,
                        )
                        compensation_committed = True
                        applied, duplicate = _acknowledge_compensation_handoff(
                            api_url=api_url,
                            event_id=event_id,
                            consumer_name=consumer_name,
                            timeout_seconds=timeout_seconds,
                        )
                        consumed += applied
                        duplicates += duplicate
                        continue
                    except Exception as compensation_exc:  # noqa: BLE001
                        errors.append(
                            f"event_id={event_id} compensation_record_error="
                            f"{compensation_exc}"
                        )
                        continue
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


def _write_health(path: str, state: Mapping[str, Any]) -> None:
    if not path:
        return
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(
            f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(dict(state), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except OSError:
        pass
    finally:
        try:
            temporary.unlink()
        except (NameError, FileNotFoundError, OSError):
            pass


def healthcheck(
    *,
    health_file: str | None = None,
    interval_seconds: float | None = None,
    max_age_seconds: float | None = None,
    consumer_name: str | None = None,
    now: float | None = None,
) -> int:
    """Return zero only after a recent successful or idle-success tick without failures."""
    path = health_file or os.getenv("DEPLOYMENT_OUTBOX_CONSUMER_HEALTH_FILE", "")
    name = consumer_name or os.getenv("DEPLOYMENT_OUTBOX_CONSUMER_NAME", _CONSUMER_NAME)
    interval = (
        interval_seconds
        if interval_seconds is not None
        else float(os.getenv("DEPLOYMENT_OUTBOX_CONSUMER_INTERVAL_SECONDS", "10"))
    )
    if not path:
        print(f"{name} health file is not configured", file=sys.stderr)
        return 1

    try:
        with open(path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
        mtime = os.path.getmtime(path)
        current_time = time.time() if now is None else now
        age_seconds = max(0.0, current_time - mtime)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"{name} health unavailable: {exc}", file=sys.stderr)
        return 1

    if not isinstance(state, dict):
        print(f"{name} health payload is not an object", file=sys.stderr)
        return 1

    observed_name = state.get("consumer_name") or state.get("worker_name")
    if observed_name != name:
        print(
            f"{name} health identity mismatch: consumer_name={observed_name!r}",
            file=sys.stderr,
        )
        return 1

    if state.get("status") != "ok" or state.get("ticks", 0) < 1:
        print(
            f"{name} health is not ready: status={state.get('status')} ticks={state.get('ticks')}",
            file=sys.stderr,
        )
        return 1

    if not state.get("last_success"):
        print(f"{name} health has zero recorded successes", file=sys.stderr)
        return 1

    if state.get("consecutive_errors", 0) > 0:
        print(
            f"{name} health has failing streak: consecutive_errors={state.get('consecutive_errors')}",
            file=sys.stderr,
        )
        return 1

    max_age = (
        max_age_seconds
        if max_age_seconds is not None
        else float(
            os.getenv(
                "DEPLOYMENT_OUTBOX_CONSUMER_HEALTH_MAX_AGE_SECONDS",
                str(max(60.0, interval * 3)),
            )
        )
    )
    if age_seconds > max_age:
        print(
            f"{name} health is stale: age_seconds={age_seconds:.1f} max_age_seconds={max_age}",
            file=sys.stderr,
        )
        return 1

    return 0


def main() -> int:
    api_url = os.getenv("DEPLOYMENT_API_URL", "http://127.0.0.1:8095")
    consumer_name = os.getenv("DEPLOYMENT_OUTBOX_CONSUMER_NAME", _CONSUMER_NAME)
    interval_seconds = _env_int("DEPLOYMENT_OUTBOX_CONSUMER_INTERVAL_SECONDS", 10, minimum=1)
    max_ticks = _env_int("DEPLOYMENT_OUTBOX_CONSUMER_MAX_TICKS", 0, minimum=0)
    timeout_seconds = float(os.getenv("DEPLOYMENT_OUTBOX_CONSUMER_TIMEOUT_SECONDS", "10"))
    max_attempts = _env_int("DEPLOYMENT_OUTBOX_CONSUMER_MAX_ATTEMPTS", 3, minimum=1)
    retry_delay_seconds = _env_int("DEPLOYMENT_OUTBOX_CONSUMER_RETRY_DELAY_SECONDS", 30, minimum=0)
    health_file = os.getenv("DEPLOYMENT_OUTBOX_CONSUMER_HEALTH_FILE", "")
    aggregate_id = os.getenv("DEPLOYMENT_OUTBOX_CONSUMER_AGGREGATE_ID", "").strip() or None

    health: dict[str, Any] = {
        "consumer_name": consumer_name,
        "worker_name": consumer_name,
        "aggregate_id": aggregate_id,
        "status": "starting",
        "total_consumed": 0,
        "total_duplicates": 0,
        "total_errors": 0,
        "total_retry_scheduled": 0,
        "total_dead_lettered": 0,
        "consecutive_errors": 0,
        "ticks": 0,
        "last_success": None,
        "last_failure": None,
        "last_failure_reason": None,
        "last_idle_success": None,
        "last_recovered_at": None,
        "recovery_count": 0,
        "retry_policy": {
            "max_attempts": max_attempts,
            "retry_delay_seconds": retry_delay_seconds,
        },
    }

    if health_file:
        try:
            Path(health_file).unlink(missing_ok=True)
        except OSError:
            pass

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
                aggregate_id=aggregate_id,
            )
            health["ticks"] = tick
            health["total_consumed"] += result["consumed"]
            health["total_duplicates"] += result["duplicates"]
            health["total_retry_scheduled"] += result["retry_scheduled"]
            health["total_dead_lettered"] += result["dead_lettered"]
            if result["errors"]:
                failed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                health["total_errors"] += len(result["errors"])
                health["consecutive_errors"] += len(result["errors"])
                health["status"] = "degraded"
                health["last_failure"] = failed_at
                health["last_failure_reason"] = "; ".join(result["errors"])
                if health_file:
                    try:
                        Path(health_file).unlink(missing_ok=True)
                    except OSError:
                        pass
            else:
                succeeded_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                if health["status"] == "degraded":
                    health["recovery_count"] += 1
                    health["last_recovered_at"] = succeeded_at
                health["status"] = "ok"
                health["last_success"] = succeeded_at
                health["consecutive_errors"] = 0
                health["last_failure_reason"] = None
                if result["events_found"] == 0:
                    health["last_idle_success"] = succeeded_at
                if health_file:
                    _write_health(health_file, health)
        except Exception as exc:  # noqa: BLE001
            failed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            health["ticks"] = tick
            health["total_errors"] += 1
            health["consecutive_errors"] += 1
            health["status"] = "degraded"
            health["last_failure"] = failed_at
            health["last_failure_reason"] = str(exc)
            if health_file:
                try:
                    Path(health_file).unlink(missing_ok=True)
                except OSError:
                    pass
            result = {
                "events_found": 0,
                "consumed": 0,
                "duplicates": 0,
                "skipped_not_due": 0,
                "retry_scheduled": 0,
                "dead_lettered": 0,
                "errors": [str(exc)],
            }

        print(
            json.dumps({"tick": tick, "health": health, "result": result}, sort_keys=True),
            flush=True,
        )

        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover - exercised through compose/smoke.
    if sys.argv[1:] == ["healthcheck"]:
        raise SystemExit(healthcheck())
    raise SystemExit(main())
