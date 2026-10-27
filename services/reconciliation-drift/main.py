from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import logging
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

_RUNTIME_MANAGER_DIR = Path(__file__).resolve().parents[1] / "runtime-manager"
if str(_RUNTIME_MANAGER_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_MANAGER_DIR))

try:
    from runtime_manager_client import RuntimeManagerClient  # type: ignore
except ImportError:  # pragma: no cover - defensive fallback
    RuntimeManagerClient = None  # type: ignore[assignment]

from consumer import build_drift_report_from_event
from services.foundation.health import register_fastapi_health_routes
from services.foundation.persistence_posture import require_persistence_posture
from services.trade_journey.lifecycle_noop_order import is_noop_order
from services.trade_journey.correlation_envelope import (
    CorrelationEnvelopeError,
    propagate_envelope,
    validate_envelope,
)
from store import build_reconciliation_drift_store
from telemetry_client import (
    TelemetryAuthError,
    TelemetryError,
    append_lifecycle_event,
    fetch_runtime_summaries,
)


DEFAULT_WARNING_RELATIVE_DELTA = 0.2
DEFAULT_CRITICAL_RELATIVE_DELTA = 0.5
# Risk metrics where only an adverse (higher) excursion is drift; observed at or
# below the approved baseline is healthy and never raises an incident.
ONE_SIDED_HIGHER_IS_WORSE_METRICS = frozenset({"drawdown"})
# observed metric -> key in the governed Evolution threshold-sweep baselines.
APPROVED_BASELINE_KEYS = {"drawdown": "expected_drawdown"}
RECONCILIATION_ERROR_SEVERITIES = {"critical", "error"}
_LIFECYCLE_APPEND_PRODUCER = "reconciliation-drift.scheduled"
_RETRYABLE_HTTP_STATUSES = {408, 425, 429}
_TENANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$")
_TENANT_CONTEXT: contextvars.ContextVar[str] = contextvars.ContextVar(
    "reconciliation_tenant_id",
    default="default",
)
_REQUIRED_LIFECYCLE_IDENTITY_FIELDS = (
    "event_id",
    "event_type",
    "created_at",
    "tenant_id",
    "environment",
    "execution_mode",
    "deployment_stage",
    "binding_id",
    "runtime_id",
    "capital_pool_id",
    "artifact_id",
    "artifact_version",
    "plan_id",
    "persona_capital_binding_id",
    "trace_id",
    "signal_id",
    "run_id",
    "loop_run_id",
    "aggregate_type",
    "aggregate_id",
    "sequence_no",
    "source_mode",
    "target",
    "correlation_envelope",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _monotonic() -> float:
    return time.monotonic()


def _data_dir() -> str:
    return os.getenv("RECONCILIATION_DRIFT_DATA_DIR", "/tmp/pantheon/reconciliation-drift")


def _auth_mode() -> str:
    mode = os.getenv("RECONCILIATION_DRIFT_AUTH_MODE", "disabled").strip().lower()
    if mode not in {"disabled", "token"}:
        raise HTTPException(
            status_code=503,
            detail="RECONCILIATION_DRIFT_AUTH_MODE must be disabled or token",
        )
    return mode


def _configured_tenant_id() -> str:
    tenant_id = (
        os.getenv("PANTHEON_TENANT_ID")
        or os.getenv("RECONCILIATION_DRIFT_DEFAULT_TENANT_ID")
        or "default"
    ).strip()
    if not _TENANT_ID_PATTERN.fullmatch(tenant_id):
        raise HTTPException(status_code=503, detail="configured tenant id is invalid")
    return tenant_id


def _current_tenant_id() -> str:
    return _TENANT_CONTEXT.get()


def _authorized_body_tenant(tenant_id: Optional[str]) -> str:
    current = _current_tenant_id()
    supplied = str(tenant_id or "").strip()
    if supplied and supplied != current:
        raise HTTPException(status_code=403, detail="tenant identity mismatch")
    return current


def _mapping_tenant_id(payload: Dict[str, Any]) -> str:
    direct = str(payload.get("tenant_id") or "").strip()
    envelope = payload.get("correlation_envelope")
    enveloped = (
        str(envelope.get("tenant_id") or "").strip()
        if isinstance(envelope, dict)
        else ""
    )
    if direct and enveloped and direct != enveloped:
        raise HTTPException(
            status_code=422,
            detail="payload tenant_id conflicts with correlation envelope",
        )
    return direct or enveloped


def _authorized_payload_tenant(
    payload: Dict[str, Any],
    *,
    require_explicit: bool,
) -> str:
    current = _current_tenant_id()
    supplied = _mapping_tenant_id(payload)
    if supplied and supplied != current:
        raise HTTPException(status_code=403, detail="payload tenant identity mismatch")
    if require_explicit and not supplied:
        raise HTTPException(status_code=422, detail="payload tenant identity is required")
    return current


def _authorize_payloads(
    payloads: List[Dict[str, Any]],
    *,
    require_explicit: bool,
) -> None:
    for payload in payloads:
        _authorized_payload_tenant(
            payload,
            require_explicit=require_explicit,
        )


def _tenant_visible(record: Dict[str, Any]) -> bool:
    record_tenant = str(record.get("tenant_id") or "").strip()
    if record_tenant:
        return record_tenant == _current_tenant_id()
    return _auth_mode() == "disabled"


def _tenant_scoped(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [record for record in records if _tenant_visible(record)]


def _worker_state_id(*, tenant_id: str, worker_kind: str, worker_id: str) -> str:
    identity = f"{tenant_id}:{worker_kind}:{worker_id}"
    return f"worker-{uuid.uuid5(uuid.NAMESPACE_URL, identity).hex}"


def _scheduled_window_id(
    *,
    tenant_id: str,
    timestamp: str,
    window_seconds: int,
) -> str:
    if window_seconds < 1:
        raise HTTPException(status_code=503, detail="scheduler window must be >= 1 second")
    try:
        observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="scheduler clock is invalid") from exc
    start_epoch = int(observed.timestamp())
    start_epoch -= start_epoch % window_seconds
    start = datetime.fromtimestamp(start_epoch, tz=timezone.utc)
    return (
        f"scheduled:{tenant_id}:"
        f"{start.isoformat().replace('+00:00', 'Z')}:PT{window_seconds}S"
    )


def _record_worker_state(
    *,
    tenant_id: str,
    worker_kind: str,
    worker_id: str,
    window_id: str,
    result: Dict[str, Any],
    updated_at: str,
) -> Dict[str, Any]:
    state_id = _worker_state_id(
        tenant_id=tenant_id,
        worker_kind=worker_kind,
        worker_id=worker_id,
    )
    return store.put_worker_state(
        {
            "id": state_id,
            "state_id": state_id,
            "tenant_id": tenant_id,
            "worker_kind": worker_kind,
            "worker_id": worker_id,
            "last_window_id": window_id,
            "status": result.get("status"),
            "controller_status": result.get("controller_status"),
            "last_result": result,
            "updated_at": updated_at,
        }
    )


class _KeyedIds:
    """Set-like id view backed by keyed store reads instead of a table scan."""

    def __init__(self, exists: Any, count: Any) -> None:
        self._exists = exists
        self._count = count
        self._added: set[str] = set()

    def __contains__(self, item: object) -> bool:
        return item in self._added or bool(self._exists(str(item)))

    def __len__(self) -> int:
        return int(self._count()) + len(self._added)

    def __bool__(self) -> bool:
        return True

    def add(self, item: str) -> None:
        self._added.add(item)


def _tenant_arg() -> Optional[str]:
    return _current_tenant_id() or None


def _evaluation_ids() -> _KeyedIds:
    return _KeyedIds(
        lambda item: store.evaluation_exists(item, _tenant_arg()),
        store.count_evaluations,
    )


def _drift_report_ids() -> _KeyedIds:
    return _KeyedIds(
        lambda item: store.drift_report_exists(item, _tenant_arg()),
        store.count_drift_reports,
    )


def _reconciliation_record_ids() -> _KeyedIds:
    return _KeyedIds(
        lambda item: store.reconciliation_record_exists(item, _tenant_arg()),
        store.count_reconciliation_records,
    )


def _next_id(prefix: str, timestamp: str, existing: Any) -> str:
    date_prefix = timestamp[:10].replace("-", "")
    index = len(existing) + 1
    candidate = f"{prefix}-{date_prefix}-{index:03d}"
    while candidate in existing:
        index += 1
        candidate = f"{prefix}-{date_prefix}-{index:03d}"
    return candidate


def _safe_id_component(value: Any, *, fallback: str = "unknown", limit: int = 48) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip())
    safe = safe.strip("-._")
    return (safe or fallback)[:limit]


def _status_rank(status: str) -> int:
    return {"ok": 0, "degraded": 1, "warning": 2, "critical": 3}.get(status, 0)


def _worst_status(statuses: List[str]) -> str:
    if not statuses:
        return "ok"
    return max(statuses, key=_status_rank)


def _incident_severity(status: str) -> str:
    if status == "critical":
        return "critical"
    if status == "warning":
        return "medium"
    if status == "degraded":
        return "low"
    return "low"


def _record_severity(status: str) -> str:
    if status == "ok":
        return "none"
    if status == "critical":
        return "critical"
    if status == "warning":
        return "medium"
    if status == "degraded":
        return "low"
    return "low"


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _metric_values(events: List[Dict[str, Any]]) -> Dict[str, List[float]]:
    values: Dict[str, List[float]] = {}
    for event in events:
        metrics = event.get("metrics")
        if not isinstance(metrics, dict):
            continue
        for key, raw_value in metrics.items():
            number = _numeric(raw_value)
            if number is None:
                continue
            values.setdefault(str(key), []).append(number)
    return values


def _observed_metrics(events: List[Dict[str, Any]]) -> Dict[str, float]:
    observed: Dict[str, float] = {}
    for metric, values in _metric_values(events).items():
        if not values:
            continue
        observed[metric] = mean(values)
        observed[f"{metric}_latest"] = values[-1]
        observed[f"{metric}_sample_count"] = float(len(values))
    return observed


def _threshold_for(metric: str, thresholds: Dict[str, Any]) -> tuple[float, float]:
    raw = thresholds.get(metric)
    if isinstance(raw, dict):
        warning = _numeric(raw.get("warning_relative_delta"))
        critical = _numeric(raw.get("critical_relative_delta"))
        return (
            DEFAULT_WARNING_RELATIVE_DELTA if warning is None else warning,
            DEFAULT_CRITICAL_RELATIVE_DELTA if critical is None else critical,
        )
    return DEFAULT_WARNING_RELATIVE_DELTA, DEFAULT_CRITICAL_RELATIVE_DELTA


def _approved_baselines_path() -> str | None:
    return (
        os.getenv("RECONCILIATION_DRIFT_APPROVED_BASELINES_PATH")
        or os.getenv("EVOCHAIN_THRESHOLD_SWEEP_BASELINES_PATH")
        or None
    )


def _load_approved_baselines() -> Dict[str, Dict[str, Any]]:
    try:
        from services.evolution import threshold_sweep_worker as sweep
    except ImportError:  # pragma: no cover - image always ships services/evolution
        return {}
    return sweep.load_baselines(_approved_baselines_path())


def _approved_artifact_baselines(
    artifact_id: Any,
    all_baselines: Dict[str, Dict[str, Any]] | None = None,
) -> tuple[Dict[str, float], Dict[str, Dict[str, str]]]:
    """Resolve approved baselines for one artifact from the Evolution-governed file.

    Uses the Evolution threshold sweep's own reader so both consumers share one
    source of truth. Fails closed: no artifact, no approved positive finite
    value, or an unreadable file yields no baseline (never a fabricated one).
    """

    aid = str(artifact_id or "").strip()
    if not aid:
        return {}, {}
    try:
        from services.evolution import threshold_sweep_worker as sweep
    except ImportError:  # pragma: no cover - image always ships services/evolution
        return {}, {}
    if all_baselines is None:
        all_baselines = sweep.load_baselines(_approved_baselines_path())
    entry = all_baselines.get(aid)
    if entry is None:
        return {}, {}
    metrics: Dict[str, float] = {}
    sources: Dict[str, Dict[str, str]] = {}
    for metric, key in APPROVED_BASELINE_KEYS.items():
        value = sweep.approved_baseline_value(all_baselines, aid, key)
        if value is None:
            continue
        metrics[metric] = value
        sources[metric] = {
            "artifact_id": aid,
            "baseline_key": key,
            "policy_source": str(entry["policy_source"]).strip(),
        }
    return metrics, sources


def _drift_checks(
    baseline_metrics: Dict[str, Any],
    observed_metrics: Dict[str, float],
    thresholds: Dict[str, Any],
    baseline_sources: Dict[str, Dict[str, str]] | None = None,
) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    for metric, baseline_raw in sorted(baseline_metrics.items()):
        baseline = _numeric(baseline_raw)
        observed = observed_metrics.get(metric)
        if baseline is None:
            continue
        if observed is None:
            checks.append(
                {
                    "metric": metric,
                    "status": "degraded",
                    "baseline": baseline,
                    "observed": None,
                    "relative_delta": None,
                    "detail": "observed metric missing from telemetry evidence",
                }
            )
            continue
        denominator = abs(baseline) if baseline != 0 else 1.0
        signed_delta = (observed - baseline) / denominator
        if metric in ONE_SIDED_HIGHER_IS_WORSE_METRICS:
            relative_delta = max(signed_delta, 0.0)
        else:
            relative_delta = abs(signed_delta)
        warning_threshold, critical_threshold = _threshold_for(metric, thresholds)
        if relative_delta >= critical_threshold:
            status = "critical"
        elif relative_delta >= warning_threshold:
            status = "warning"
        else:
            status = "ok"
        checks.append(
            {
                "metric": metric,
                "status": status,
                "baseline": baseline,
                "observed": observed,
                "relative_delta": relative_delta,
                "warning_relative_delta": warning_threshold,
                "critical_relative_delta": critical_threshold,
                **(
                    {
                        "one_sided": True,
                        "signed_relative_delta": signed_delta,
                    }
                    if metric in ONE_SIDED_HIGHER_IS_WORSE_METRICS
                    else {}
                ),
                **(
                    {"baseline_source": (baseline_sources or {})[metric]}
                    if metric in (baseline_sources or {})
                    else {}
                ),
            }
        )
    return checks


def _reconciliation_checks(
    *,
    binding_id: str,
    runtime_id: str | None,
    telemetry_events: List[Dict[str, Any]],
    lineage_projection: Dict[str, Any] | None,
    runtime_evidence: Dict[str, Any] | None,
) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    if not telemetry_events:
        checks.append(
            {
                "check": "telemetry_presence",
                "status": "degraded",
                "detail": "no telemetry events supplied for reconciliation",
            }
        )
    else:
        mismatched_events = [
            event.get("event_id") or event.get("id") or "<unknown>"
            for event in telemetry_events
            if event.get("binding_id") != binding_id
        ]
        checks.append(
            {
                "check": "telemetry_binding_alignment",
                "status": "critical" if mismatched_events else "ok",
                "detail": "all telemetry events reference requested binding"
                if not mismatched_events
                else "telemetry events reference a different binding",
                "mismatched_event_ids": mismatched_events,
            }
        )

    if lineage_projection is None:
        checks.append(
            {
                "check": "lineage_projection_presence",
                "status": "degraded",
                "detail": "lineage projection unavailable; read model is partial",
            }
        )
    else:
        target_id = lineage_projection.get("target_id") or lineage_projection.get("binding_id")
        checks.append(
            {
                "check": "lineage_binding_alignment",
                "status": "critical" if target_id and target_id != binding_id else "ok",
                "detail": "lineage projection target matches binding"
                if not target_id or target_id == binding_id
                else "lineage projection target does not match requested binding",
                "lineage_target_id": target_id,
            }
        )

    if runtime_evidence is None:
        checks.append(
            {
                "check": "runtime_evidence_presence",
                "status": "degraded",
                "detail": "runtime evidence unavailable; no control-plane action is inferred",
            }
        )
    else:
        runtime_binding_id = runtime_evidence.get("binding_id") or runtime_evidence.get("id")
        runtime_status = str(runtime_evidence.get("status") or "unknown").lower()
        checks.append(
            {
                "check": "runtime_binding_alignment",
                "status": "critical" if runtime_binding_id and runtime_binding_id != binding_id else "ok",
                "detail": "runtime evidence binding matches requested binding"
                if not runtime_binding_id or runtime_binding_id == binding_id
                else "runtime evidence binding does not match requested binding",
                "runtime_binding_id": runtime_binding_id,
            }
        )
        if runtime_id:
            observed_runtime_id = runtime_evidence.get("runtime_id")
            checks.append(
                {
                    "check": "runtime_id_alignment",
                    "status": "critical" if observed_runtime_id and observed_runtime_id != runtime_id else "ok",
                    "detail": "runtime evidence runtime_id matches request"
                    if not observed_runtime_id or observed_runtime_id == runtime_id
                    else "runtime evidence runtime_id does not match request",
                    "runtime_id": observed_runtime_id,
                }
            )
        checks.append(
            {
                "check": "runtime_status_observed",
                "status": "warning" if runtime_status in {"retired", "paused", "degraded"} else "ok",
                "detail": f"runtime status is {runtime_status}",
                "runtime_status": runtime_status,
            }
        )
    return checks


def _build_alert_handoffs(evaluation: Dict[str, Any], timestamp: str) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    alert_index = 1
    for check in evaluation["drift_checks"]:
        if check["status"] not in {"warning", "critical"}:
            continue
        alert_id = f"{evaluation['evaluation_id']}-alert-{alert_index:03d}"
        alert_index += 1
        alerts.append(
            {
                "alert_id": alert_id,
                "evaluation_id": evaluation["evaluation_id"],
                "tenant_id": evaluation.get("tenant_id"),
                "binding_id": evaluation["binding_id"],
                "severity": check["status"],
                "alert_type": "metric_drift",
                "summary": f"{check['metric']} drift {check['status']}",
                "target_service": "evolution",
                "handoff_state": "ready",
                "created_at": timestamp,
                "emergency_control_chain_affected": False,
                "recommended_action": "threshold_evaluate_then_propose",
                "evidence_refs": [
                    {"type": "drift_evaluation", "id": evaluation["evaluation_id"]},
                    {"type": "runtime_binding", "id": evaluation["binding_id"]},
                ],
            }
        )
    for check in evaluation["reconciliation_checks"]:
        if check["status"] not in RECONCILIATION_ERROR_SEVERITIES:
            continue
        alert_id = f"{evaluation['evaluation_id']}-alert-{alert_index:03d}"
        alert_index += 1
        alerts.append(
            {
                "alert_id": alert_id,
                "evaluation_id": evaluation["evaluation_id"],
                "tenant_id": evaluation.get("tenant_id"),
                "binding_id": evaluation["binding_id"],
                "severity": check["status"],
                "alert_type": "reconciliation_mismatch",
                "summary": f"{check['check']} failed",
                "target_service": "incidents",
                "handoff_state": "ready",
                "created_at": timestamp,
                "emergency_control_chain_affected": False,
                "recommended_action": "open_incident_or_operator_review",
                "evidence_refs": [
                    {"type": "drift_evaluation", "id": evaluation["evaluation_id"]},
                    {"type": "runtime_binding", "id": evaluation["binding_id"]},
                ],
            }
        )
    return alerts


def _post_json(url: str, payload: Dict[str, Any], *, timeout_seconds: float | None = None) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if timeout_seconds is None:
        timeout_seconds = float(os.getenv("PANTHEON_INCIDENTS_API_TIMEOUT_SECONDS", "90"))
    started = time.monotonic()
    try:
        with urllib.request.urlopen(  # noqa: S310 - service URL is operator configured.
            request,
            timeout=timeout_seconds,
        ) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"incident service rejected request: {detail}") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"incident service unavailable: {exc.reason}") from exc
    except OSError as exc:  # read timeouts raise bare TimeoutError, not URLError
        raise HTTPException(status_code=502, detail=f"incident service unavailable: {exc!r}") from exc
    finally:
        logging.getLogger(__name__).info("incidents call took %.2fs", time.monotonic() - started)


def _classify_drift_report_incident(report: Dict[str, Any], *, timeout_seconds: float | None = None) -> Dict[str, Any] | None:
    incidents_api_url = os.getenv("PANTHEON_INCIDENTS_API_URL", "").rstrip("/")
    if not incidents_api_url:
        return None
    return _post_json(
        f"{incidents_api_url}/api/incidents/consume-drift-report",
        {"drift_report": report},
        timeout_seconds=timeout_seconds,
    )


def _stage_reconciliation_checks(
    *,
    binding_id: str,
    runtime_id: str,
    deployment_stage: str,
    deployment_plan_id: str,
    artifact_id: str,
    capital_pool_id: str,
    telemetry_events: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Generic per-stage reconciliation checks covering binding, plan, artifact, and pool alignment."""
    checks = _reconciliation_checks(
        binding_id=binding_id,
        runtime_id=runtime_id,
        telemetry_events=telemetry_events,
        lineage_projection={"target_id": binding_id},
        runtime_evidence={"binding_id": binding_id, "runtime_id": runtime_id, "status": "active"},
    )
    # Stage alignment: all telemetry events must carry the expected deployment_stage
    stage_mismatched = [
        event.get("event_id") or event.get("id") or "<unknown>"
        for event in telemetry_events
        if event.get("deployment_stage") and event.get("deployment_stage") != deployment_stage
    ]
    checks.append(
        {
            "check": "telemetry_deployment_stage_alignment",
            "status": "critical" if stage_mismatched else "ok",
            "detail": f"all telemetry events carry deployment_stage={deployment_stage!r}"
            if not stage_mismatched
            else f"telemetry events carry unexpected deployment_stage (expected {deployment_stage!r})",
            "mismatched_event_ids": stage_mismatched,
        }
    )
    for field_name, expected in (
        ("deployment_plan_id", deployment_plan_id),
        ("artifact_id", artifact_id),
        ("capital_pool_id", capital_pool_id),
    ):
        mismatched = [
            event.get("event_id") or event.get("id") or "<unknown>"
            for event in telemetry_events
            if event.get(field_name) and event.get(field_name) != expected
        ]
        checks.append(
            {
                "check": f"telemetry_{field_name}_alignment",
                "status": "critical" if mismatched else "ok",
                "detail": f"all telemetry events reference requested {field_name}"
                if not mismatched
                else f"telemetry events reference a different {field_name}",
                "mismatched_event_ids": mismatched,
            }
        )
    return checks


def _paper_reconciliation_checks(
    *,
    binding_id: str,
    runtime_id: str,
    deployment_plan_id: str,
    artifact_id: str,
    capital_pool_id: str,
    telemetry_events: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    checks = _reconciliation_checks(
        binding_id=binding_id,
        runtime_id=runtime_id,
        telemetry_events=telemetry_events,
        lineage_projection={"target_id": binding_id},
        runtime_evidence={"binding_id": binding_id, "runtime_id": runtime_id, "status": "active"},
    )
    for field_name, expected in (
        ("deployment_plan_id", deployment_plan_id),
        ("artifact_id", artifact_id),
        ("capital_pool_id", capital_pool_id),
    ):
        mismatched = [
            event.get("event_id") or event.get("id") or "<unknown>"
            for event in telemetry_events
            if event.get(field_name) and event.get(field_name) != expected
        ]
        checks.append(
            {
                "check": f"telemetry_{field_name}_alignment",
                "status": "critical" if mismatched else "ok",
                "detail": f"all telemetry events reference requested {field_name}"
                if not mismatched
                else f"telemetry events reference a different {field_name}",
                "mismatched_event_ids": mismatched,
            }
        )
    return checks


class EvaluationBody(BaseModel):
    tenant_id: Optional[str] = None
    binding_id: str
    runtime_id: Optional[str] = None
    evaluation_id: Optional[str] = None
    baseline_metrics: Dict[str, Any] = Field(default_factory=dict)
    telemetry_events: List[Dict[str, Any]] = Field(default_factory=list)
    lineage_projection: Optional[Dict[str, Any]] = None
    runtime_evidence: Optional[Dict[str, Any]] = None
    thresholds: Dict[str, Any] = Field(default_factory=dict)
    evidence_refs: List[Dict[str, Any]] = Field(default_factory=list)
    evaluated_at: Optional[str] = None


class TelemetryEventConsumeBody(BaseModel):
    tenant_id: Optional[str] = None
    worker_id: Optional[str] = None
    event: Optional[Dict[str, Any]] = None
    events: List[Dict[str, Any]] = Field(default_factory=list)
    baseline_metrics: Dict[str, Any] = Field(default_factory=dict)
    thresholds: Dict[str, Any] = Field(default_factory=dict)
    generated_at: Optional[str] = None


class HandoffBody(BaseModel):
    actor_id: str = "reconciliation-drift-service"
    handoff_state: str = "sent"
    target_service: Optional[str] = None
    note: Optional[str] = None


class PaperRunReconciliationBody(BaseModel):
    tenant_id: Optional[str] = None
    binding_id: str
    runtime_id: str
    deployment_plan_id: str
    artifact_id: str
    artifact_version: str
    capital_pool_id: str
    persona_capital_binding_id: str
    trace_id: str
    record_id: Optional[str] = None
    paper_run_id: Optional[str] = None
    baseline_ref: str = "paper_baseline"
    actual_ref: str = "paper_runtime"
    telemetry_events: List[Dict[str, Any]] = Field(default_factory=list)
    baseline_metrics: Dict[str, Any] = Field(default_factory=dict)
    thresholds: Dict[str, Any] = Field(default_factory=dict)
    evidence_refs: List[Dict[str, Any]] = Field(default_factory=list)
    create_incident_on_breach: bool = True
    propose_evolution_on_breach: bool = True
    generated_at: Optional[str] = None


class CanaryRunReconciliationBody(BaseModel):
    tenant_id: Optional[str] = None
    binding_id: str
    runtime_id: str
    deployment_plan_id: str
    artifact_id: str
    artifact_version: str
    capital_pool_id: str
    persona_capital_binding_id: str
    trace_id: str
    record_id: Optional[str] = None
    canary_run_id: Optional[str] = None
    baseline_ref: str = "paper_baseline"
    actual_ref: str = "canary_runtime"
    telemetry_events: List[Dict[str, Any]] = Field(default_factory=list)
    baseline_metrics: Dict[str, Any] = Field(default_factory=dict)
    thresholds: Dict[str, Any] = Field(default_factory=dict)
    evidence_refs: List[Dict[str, Any]] = Field(default_factory=list)
    create_incident_on_breach: bool = True
    propose_evolution_on_breach: bool = True
    generated_at: Optional[str] = None


class LiveRunReconciliationBody(BaseModel):
    tenant_id: Optional[str] = None
    binding_id: str
    runtime_id: str
    deployment_plan_id: str
    artifact_id: str
    artifact_version: str
    capital_pool_id: str
    persona_capital_binding_id: str
    trace_id: str
    record_id: Optional[str] = None
    live_run_id: Optional[str] = None
    baseline_ref: str = "canary_baseline"
    actual_ref: str = "live_runtime"
    telemetry_events: List[Dict[str, Any]] = Field(default_factory=list)
    baseline_metrics: Dict[str, Any] = Field(default_factory=dict)
    thresholds: Dict[str, Any] = Field(default_factory=dict)
    evidence_refs: List[Dict[str, Any]] = Field(default_factory=list)
    create_incident_on_breach: bool = True
    propose_evolution_on_breach: bool = True
    generated_at: Optional[str] = None


DATA_DIR = _data_dir()
store = build_reconciliation_drift_store(DATA_DIR)
STORE_BACKEND = getattr(
    store,
    "backend",
    os.getenv("RECONCILIATION_DRIFT_STORE_BACKEND", "json").strip().lower() or "json",
)
PERSISTENCE_POSTURE = require_persistence_posture("reconciliation-drift")
app = FastAPI(title="Pantheon Reconciliation Drift Service", version="0.1.0")


@app.middleware("http")
async def authenticate_tenant(request: Request, call_next):
    """Bind every reconciliation API request to one authenticated tenant.

    Local compatibility mode remains explicit through the default ``disabled``
    setting. Hosted/internal deployments select ``token`` and must provide
    both a service bearer token and ``X-Tenant-Id``; missing configuration or
    identity fails closed before a handler can read or mutate durable state.
    """

    if not request.url.path.startswith("/api/reconciliation-drift"):
        return await call_next(request)
    try:
        mode = _auth_mode()
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    supplied_tenant = str(request.headers.get("x-tenant-id") or "").strip()
    if supplied_tenant and not _TENANT_ID_PATTERN.fullmatch(supplied_tenant):
        return JSONResponse(
            status_code=400,
            content={"detail": "X-Tenant-Id is invalid"},
        )
    if mode == "token":
        configured_token = os.getenv("RECONCILIATION_DRIFT_AUTH_TOKEN", "")
        if not configured_token:
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "reconciliation tenant authentication is not configured"
                },
            )
        authorization = request.headers.get("authorization") or ""
        scheme, separator, supplied_token = authorization.partition(" ")
        if (
            separator != " "
            or scheme.lower() != "bearer"
            or not supplied_token
            or not hmac.compare_digest(supplied_token, configured_token)
        ):
            return JSONResponse(
                status_code=401,
                content={"detail": "invalid reconciliation bearer token"},
            )
        if not supplied_tenant:
            return JSONResponse(
                status_code=400,
                content={"detail": "X-Tenant-Id is required"},
            )
    try:
        tenant_id = supplied_tenant or _configured_tenant_id()
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    token = _TENANT_CONTEXT.set(tenant_id)
    try:
        response = await call_next(request)
    finally:
        _TENANT_CONTEXT.reset(token)
    response.headers["Vary"] = "Authorization, X-Tenant-Id"
    return response


register_fastapi_health_routes(
    app,
    "reconciliation-drift",
    dependencies=lambda: {
        "persistence": PERSISTENCE_POSTURE.to_dict(),
        "telemetry": {
            "status": "ok" if os.getenv("PANTHEON_TELEMETRY_API_URL", "") else "degraded",
            "url": os.getenv("PANTHEON_TELEMETRY_API_URL", ""),
        },
        "lineage_read": {
            "status": "ok" if os.getenv("PANTHEON_LINEAGE_READ_URL", "") else "degraded",
            "url": os.getenv("PANTHEON_LINEAGE_READ_URL", ""),
        },
        "runtime_manager": {
            "status": "ok" if os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "") else "degraded",
            "url": os.getenv("PANTHEON_RUNTIME_MANAGER_URL", ""),
        },
    },
    metrics=lambda: {
        "evaluation_count": store.count_evaluations(),
        "alert_count": store.count_alert_handoffs(),
        "reconciliation_record_count": store.count_reconciliation_records(),
        "drift_report_count": store.count_drift_reports(),
        "worker_state_count": store.count_worker_states(),
    },
    details=lambda: {
        "data_dir": DATA_DIR,
        "store_backend": STORE_BACKEND,
        "persistence_posture": PERSISTENCE_POSTURE.to_dict(),
        "tenant_auth_mode": os.getenv(
            "RECONCILIATION_DRIFT_AUTH_MODE", "disabled"
        ).strip().lower(),
    },
)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "reconciliation-drift",
        "data_dir": DATA_DIR,
        "evaluation_count": store.count_evaluations(),
        "alert_handoff_count": store.count_alert_handoffs(),
        "reconciliation_record_count": store.count_reconciliation_records(),
        "drift_report_count": store.count_drift_reports(),
        "telemetry_api_url": os.getenv("PANTHEON_TELEMETRY_API_URL", ""),
        "lineage_read_url": os.getenv("PANTHEON_LINEAGE_READ_URL", ""),
        "runtime_manager_url": os.getenv("PANTHEON_RUNTIME_MANAGER_URL", ""),
        "emergency_control_chain_member": False,
    }


@app.post("/api/reconciliation-drift/evaluations", status_code=201)
def create_evaluation(body: EvaluationBody) -> Dict[str, Any]:
    tenant_id = _authorized_body_tenant(body.tenant_id)
    _authorize_payloads(
        body.telemetry_events,
        require_explicit=_auth_mode() == "token",
    )
    timestamp = body.evaluated_at or utc_now()
    evaluation_id = body.evaluation_id or _next_id(
        "rdeval",
        timestamp,
        _evaluation_ids(),
    )
    observed = _observed_metrics(body.telemetry_events)
    drift_checks = _drift_checks(body.baseline_metrics, observed, body.thresholds)
    reconciliation_checks = _reconciliation_checks(
        binding_id=body.binding_id,
        runtime_id=body.runtime_id,
        telemetry_events=body.telemetry_events,
        lineage_projection=body.lineage_projection,
        runtime_evidence=body.runtime_evidence,
    )
    status = _worst_status([check["status"] for check in drift_checks + reconciliation_checks])
    evaluation = {
        "id": evaluation_id,
        "evaluation_id": evaluation_id,
        "tenant_id": tenant_id,
        "binding_id": body.binding_id,
        "runtime_id": body.runtime_id,
        "status": status,
        "summary": {
            "status": status,
            "telemetry_event_count": len(body.telemetry_events),
            "baseline_metric_count": len(body.baseline_metrics),
            "observed_metric_count": len([key for key in observed if not key.endswith("_sample_count") and not key.endswith("_latest")]),
            "drift_check_count": len(drift_checks),
            "reconciliation_check_count": len(reconciliation_checks),
        },
        "baseline_metrics": body.baseline_metrics,
        "observed_metrics": observed,
        "drift_checks": drift_checks,
        "reconciliation_checks": reconciliation_checks,
        "evidence_refs": body.evidence_refs,
        "source_contract": {
            "telemetry_truth_owner": "telemetry-ingest",
            "lineage_truth_owner": "lineage-read",
            "runtime_truth_owner": "runtime-manager",
            "derived_only": True,
            "emergency_control_chain_affected": False,
        },
        "evaluated_at": timestamp,
    }
    stored = store.put_evaluation(evaluation)
    for alert in _build_alert_handoffs(stored, timestamp):
        store.put_alert_handoff(alert)
    return stored


@app.post("/api/reconciliation-drift/telemetry-events/consume", status_code=201)
def consume_telemetry_events(body: TelemetryEventConsumeBody) -> Dict[str, Any]:
    tenant_id = _authorized_body_tenant(body.tenant_id)
    require_explicit_tenant = _auth_mode() == "token"
    worker_id = str(body.worker_id or "reconciliation-consumer").strip()
    if not worker_id:
        raise HTTPException(status_code=422, detail="worker_id must not be empty")
    try:
        lease_seconds = float(
            os.getenv("RECONCILIATION_DRIFT_CONSUMER_LEASE_SECONDS", "120")
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="RECONCILIATION_DRIFT_CONSUMER_LEASE_SECONDS is invalid",
        ) from exc
    if not math.isfinite(lease_seconds) or lease_seconds <= 0:
        raise HTTPException(
            status_code=503,
            detail="RECONCILIATION_DRIFT_CONSUMER_LEASE_SECONDS must be finite and > 0",
        )
    events = list(body.events)
    if body.event:
        events.insert(0, body.event)

    existing_report_ids = _drift_report_ids()
    drift_reports: List[Dict[str, Any]] = []
    incident_cases: List[Dict[str, Any]] = []
    ignored_event_ids: List[str] = []
    duplicate_event_ids: List[str] = []
    deferred_event_ids: List[str] = []
    for event in events:
        event_tenant_id = _authorized_payload_tenant(
            event,
            require_explicit=require_explicit_tenant,
        )
        event_id = str(event.get("event_id") or event.get("id") or "").strip()
        if not event_id:
            canonical = json.dumps(
                event,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            event_id = f"payload-{uuid.uuid5(uuid.NAMESPACE_URL, canonical).hex}"
        claim_result = store.claim_work(
            tenant_id=event_tenant_id,
            work_type="telemetry_consume",
            window_id=event_id,
            owner_id=worker_id,
            lease_seconds=lease_seconds,
        )
        claim = claim_result.get("claim") or {}
        if not claim_result["acquired"]:
            if claim_result["reason"] == "completed":
                duplicate_event_ids.append(event_id)
                completed_result = claim.get("result")
                completed_report_id = str(
                    (completed_result or {}).get("drift_report_id") or ""
                ).strip()
                if completed_report_id:
                    completed_report = store.get_drift_report(
                        completed_report_id,
                        tenant_id=event_tenant_id,
                    )
                    if completed_report is not None:
                        drift_reports.append(completed_report)
            else:
                deferred_event_ids.append(event_id)
            continue
        try:
            report = build_drift_report_from_event(
                event,
                baseline_metrics=body.baseline_metrics,
                thresholds=body.thresholds,
                generated_at=body.generated_at,
                existing_report_ids=existing_report_ids,
            )
            if report is None:
                ignored_event_ids.append(event_id)
                work_result = {
                    "status": "ok",
                    "event_id": event_id,
                    "drift_report_id": None,
                    "incident_id": None,
                }
                store.complete_work(
                    claim_id=str(claim["claim_id"]),
                    lease_token=str(claim["lease_token"]),
                    result=work_result,
                )
                continue
            report["tenant_id"] = event_tenant_id
            stored = store.put_drift_report(report)
            existing_report_ids.add(str(stored.get("drift_report_id") or ""))
            drift_reports.append(stored)
            incident_case = _classify_drift_report_incident(stored)
            if incident_case is not None:
                incident_cases.append(incident_case)
            work_result = {
                "status": "ok",
                "event_id": event_id,
                "drift_report_id": stored.get("drift_report_id"),
                "incident_id": (
                    incident_case.get("incident_id") or incident_case.get("id")
                    if isinstance(incident_case, dict)
                    else None
                ),
            }
            store.complete_work(
                claim_id=str(claim["claim_id"]),
                lease_token=str(claim["lease_token"]),
                result=work_result,
            )
        except Exception as exc:
            store.fail_work(
                claim_id=str(claim["claim_id"]),
                lease_token=str(claim["lease_token"]),
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

    status = "deferred" if deferred_event_ids and not drift_reports else "ok"
    result = {
        "status": status,
        "consumed_event_count": len(events),
        "drift_report_count": len(drift_reports),
        "drift_reports": drift_reports,
        "incident_case_count": len(incident_cases),
        "incident_cases": incident_cases,
        "ignored_event_ids": ignored_event_ids,
        "duplicate_event_ids": duplicate_event_ids,
        "deferred_event_ids": deferred_event_ids,
        "tenant_id": tenant_id,
        "worker_id": worker_id,
        "source_contract": {
            "telemetry_truth_owner": "telemetry-ingest",
            "incident_truth_owner": "incidents",
            "derived_only": True,
            "emergency_control_chain_affected": False,
        },
    }
    _record_worker_state(
        tenant_id=tenant_id,
        worker_kind="telemetry_consumer",
        worker_id=worker_id,
        window_id=",".join(
            str(event.get("event_id") or event.get("id") or "")
            for event in events
        ),
        result=result,
        updated_at=utc_now(),
    )
    return result


@app.post("/api/reconciliation-drift/paper-runs/reconcile", status_code=201)
def create_paper_reconciliation_record(body: PaperRunReconciliationBody) -> Dict[str, Any]:
    tenant_id = _authorized_body_tenant(body.tenant_id)
    _authorize_payloads(
        body.telemetry_events,
        require_explicit=_auth_mode() == "token",
    )
    timestamp = body.generated_at or utc_now()
    record_id = body.record_id or _next_id(
        "recon",
        timestamp,
        _reconciliation_record_ids(),
    )
    observed = _observed_metrics(body.telemetry_events)
    drift_checks = _drift_checks(body.baseline_metrics, observed, body.thresholds)
    reconciliation_checks = _paper_reconciliation_checks(
        binding_id=body.binding_id,
        runtime_id=body.runtime_id,
        deployment_plan_id=body.deployment_plan_id,
        artifact_id=body.artifact_id,
        capital_pool_id=body.capital_pool_id,
        telemetry_events=body.telemetry_events,
    )
    severity_signal = _worst_status([check["status"] for check in drift_checks + reconciliation_checks])
    severity = _record_severity(severity_signal)
    status = "open" if severity_signal in {"degraded", "warning", "critical"} else "resolved"
    telemetry_event_ids = [
        str(event.get("event_id") or event.get("id"))
        for event in body.telemetry_events
        if event.get("event_id") or event.get("id")
    ]
    evidence_refs = [
        *body.evidence_refs,
        {"type": "runtime_binding", "id": body.binding_id},
        {"type": "artifact", "id": body.artifact_id, "version": body.artifact_version},
        {"type": "capital_pool", "id": body.capital_pool_id},
    ]
    record = {
        "id": record_id,
        "record_id": record_id,
        "tenant_id": tenant_id,
        "recon_type": "paper_run",
        "scope_ref": body.binding_id,
        "runtime_binding_id": body.binding_id,
        "binding_id": body.binding_id,
        "runtime_id": body.runtime_id,
        "deployment_stage": "paper",
        "deployment_plan_id": body.deployment_plan_id,
        "artifact_id": body.artifact_id,
        "artifact_version": body.artifact_version,
        "capital_pool_id": body.capital_pool_id,
        "persona_capital_binding_id": body.persona_capital_binding_id,
        "trace_id": body.trace_id,
        "paper_run_id": body.paper_run_id,
        "expected_ref": body.baseline_ref,
        "actual_ref": body.actual_ref,
        "delta_summary": {
            "baseline_metrics": body.baseline_metrics,
            "observed_metrics": observed,
            "drift_checks": drift_checks,
            "reconciliation_checks": reconciliation_checks,
            "telemetry_event_count": len(body.telemetry_events),
        },
        "severity": severity,
        "status": status,
        "evidence_refs": evidence_refs,
        "generated_at": timestamp,
        "source_contract": {
            "telemetry_truth_owner": "telemetry-ingest",
            "incident_truth_owner": "incidents",
            "evolution_truth_owner": "evolution",
            "derived_only": False,
            "emergency_control_chain_affected": False,
        },
    }
    stored = store.put_reconciliation_record(record)

    incident_case = None
    incident_request = None
    evolution_proposal = None
    if status == "open" and body.create_incident_on_breach:
        incident_id = f"{record_id}-incident-001"
        incident_request = {
            "incident_id": incident_id,
            "title": f"Paper reconciliation threshold breach for {body.binding_id}",
            "status": "open",
            "severity": _incident_severity(severity),
            "binding_id": body.binding_id,
            "deployment_stage": "paper",
            "deployment_plan_id": body.deployment_plan_id,
            "capital_pool_id": body.capital_pool_id,
            "persona_capital_binding_id": body.persona_capital_binding_id,
            "artifact_id": body.artifact_id,
            "artifact_version": body.artifact_version,
            "runtime_id": body.runtime_id,
            "trace_id": body.trace_id,
            "telemetry_event_ids": telemetry_event_ids,
            "evidence_summary": f"ReconciliationRecord {record_id} severity={severity}",
            "lineage_ref": f"{body.artifact_id}@{body.artifact_version}",
        }
        incidents_api_url = os.getenv("PANTHEON_INCIDENTS_API_URL", "").rstrip("/")
        if incidents_api_url:
            incident_case = _post_json(f"{incidents_api_url}/api/incidents", incident_request)

    if status == "open" and body.propose_evolution_on_breach:
        evolution_proposal = {
            "decision_id": f"{record_id}-evo-001",
            "decision_state": "proposed",
            "target_type": "candidate_artifact",
            "target_id": body.artifact_id,
            "target_version": body.artifact_version,
            "target_stage": "paper",
            "action_type": "flag_for_review",
            "rationale": f"Paper reconciliation threshold breach from {record_id}; no automatic evolution execution.",
            "capital_pool_id": body.capital_pool_id,
            "linked_incident_id": (incident_case or incident_request or {}).get("incident_id"),
            "evidence_refs": [
                {"ref_type": "telemetry_event", "ref_id": event_id}
                for event_id in telemetry_event_ids
            ]
            + [{"ref_type": "runtime_binding", "ref_id": body.binding_id}],
            "metadata": {
                "source_reconciliation_record_id": record_id,
                "proposed_only": True,
                "automatic_execution_allowed": False,
            },
        }

    return {
        "record": stored,
        "incident_request": incident_request,
        "incident_case": incident_case,
        "evolution_proposal": evolution_proposal,
    }


@app.post("/api/reconciliation-drift/canary-runs/reconcile", status_code=201)
def create_canary_reconciliation_record(body: CanaryRunReconciliationBody) -> Dict[str, Any]:
    tenant_id = _authorized_body_tenant(body.tenant_id)
    _authorize_payloads(
        body.telemetry_events,
        require_explicit=_auth_mode() == "token",
    )
    timestamp = body.generated_at or utc_now()
    record_id = body.record_id or _next_id(
        "recon",
        timestamp,
        _reconciliation_record_ids(),
    )
    observed = _observed_metrics(body.telemetry_events)
    drift_checks = _drift_checks(body.baseline_metrics, observed, body.thresholds)
    reconciliation_checks = _stage_reconciliation_checks(
        binding_id=body.binding_id,
        runtime_id=body.runtime_id,
        deployment_stage="canary",
        deployment_plan_id=body.deployment_plan_id,
        artifact_id=body.artifact_id,
        capital_pool_id=body.capital_pool_id,
        telemetry_events=body.telemetry_events,
    )
    severity_signal = _worst_status([check["status"] for check in drift_checks + reconciliation_checks])
    severity = _record_severity(severity_signal)
    status = "open" if severity_signal in {"degraded", "warning", "critical"} else "resolved"
    telemetry_event_ids = [
        str(event.get("event_id") or event.get("id"))
        for event in body.telemetry_events
        if event.get("event_id") or event.get("id")
    ]
    evidence_refs = [
        *body.evidence_refs,
        {"type": "runtime_binding", "id": body.binding_id},
        {"type": "artifact", "id": body.artifact_id, "version": body.artifact_version},
        {"type": "capital_pool", "id": body.capital_pool_id},
    ]
    record = {
        "id": record_id,
        "record_id": record_id,
        "tenant_id": tenant_id,
        "recon_type": "canary_run",
        "scope_ref": body.binding_id,
        "runtime_binding_id": body.binding_id,
        "binding_id": body.binding_id,
        "runtime_id": body.runtime_id,
        "deployment_stage": "canary",
        "deployment_plan_id": body.deployment_plan_id,
        "artifact_id": body.artifact_id,
        "artifact_version": body.artifact_version,
        "capital_pool_id": body.capital_pool_id,
        "persona_capital_binding_id": body.persona_capital_binding_id,
        "trace_id": body.trace_id,
        "canary_run_id": body.canary_run_id,
        "expected_ref": body.baseline_ref,
        "actual_ref": body.actual_ref,
        "delta_summary": {
            "baseline_metrics": body.baseline_metrics,
            "observed_metrics": observed,
            "drift_checks": drift_checks,
            "reconciliation_checks": reconciliation_checks,
            "telemetry_event_count": len(body.telemetry_events),
        },
        "severity": severity,
        "status": status,
        "evidence_refs": evidence_refs,
        "generated_at": timestamp,
        "source_contract": {
            "telemetry_truth_owner": "telemetry-ingest",
            "incident_truth_owner": "incidents",
            "evolution_truth_owner": "evolution",
            "derived_only": False,
            "emergency_control_chain_affected": False,
        },
    }
    stored = store.put_reconciliation_record(record)

    incident_case = None
    incident_request = None
    evolution_proposal = None
    if status == "open" and body.create_incident_on_breach:
        incident_id = f"{record_id}-incident-001"
        incident_request = {
            "incident_id": incident_id,
            "title": f"Canary reconciliation threshold breach for {body.binding_id}",
            "status": "open",
            "severity": _incident_severity(severity),
            "binding_id": body.binding_id,
            "deployment_stage": "canary",
            "deployment_plan_id": body.deployment_plan_id,
            "capital_pool_id": body.capital_pool_id,
            "persona_capital_binding_id": body.persona_capital_binding_id,
            "artifact_id": body.artifact_id,
            "artifact_version": body.artifact_version,
            "runtime_id": body.runtime_id,
            "trace_id": body.trace_id,
            "telemetry_event_ids": telemetry_event_ids,
            "evidence_summary": f"ReconciliationRecord {record_id} severity={severity}",
            "lineage_ref": f"{body.artifact_id}@{body.artifact_version}",
        }
        incidents_api_url = os.getenv("PANTHEON_INCIDENTS_API_URL", "").rstrip("/")
        if incidents_api_url:
            incident_case = _post_json(f"{incidents_api_url}/api/incidents", incident_request)

    if status == "open" and body.propose_evolution_on_breach:
        evolution_proposal = {
            "decision_id": f"{record_id}-evo-001",
            "decision_state": "proposed",
            "target_type": "candidate_artifact",
            "target_id": body.artifact_id,
            "target_version": body.artifact_version,
            "target_stage": "canary",
            "action_type": "flag_for_review",
            "rationale": f"Canary reconciliation threshold breach from {record_id}; no automatic evolution execution.",
            "capital_pool_id": body.capital_pool_id,
            "linked_incident_id": (incident_case or incident_request or {}).get("incident_id"),
            "evidence_refs": [
                {"ref_type": "telemetry_event", "ref_id": event_id}
                for event_id in telemetry_event_ids
            ]
            + [{"ref_type": "runtime_binding", "ref_id": body.binding_id}],
            "metadata": {
                "source_reconciliation_record_id": record_id,
                "proposed_only": True,
                "automatic_execution_allowed": False,
            },
        }

    return {
        "record": stored,
        "incident_request": incident_request,
        "incident_case": incident_case,
        "evolution_proposal": evolution_proposal,
    }


@app.post("/api/reconciliation-drift/live-runs/reconcile", status_code=201)
def create_live_reconciliation_record(body: LiveRunReconciliationBody) -> Dict[str, Any]:
    tenant_id = _authorized_body_tenant(body.tenant_id)
    _authorize_payloads(
        body.telemetry_events,
        require_explicit=_auth_mode() == "token",
    )
    timestamp = body.generated_at or utc_now()
    record_id = body.record_id or _next_id(
        "recon",
        timestamp,
        _reconciliation_record_ids(),
    )
    observed = _observed_metrics(body.telemetry_events)
    drift_checks = _drift_checks(body.baseline_metrics, observed, body.thresholds)
    reconciliation_checks = _stage_reconciliation_checks(
        binding_id=body.binding_id,
        runtime_id=body.runtime_id,
        deployment_stage="live",
        deployment_plan_id=body.deployment_plan_id,
        artifact_id=body.artifact_id,
        capital_pool_id=body.capital_pool_id,
        telemetry_events=body.telemetry_events,
    )
    severity_signal = _worst_status([check["status"] for check in drift_checks + reconciliation_checks])
    severity = _record_severity(severity_signal)
    status = "open" if severity_signal in {"degraded", "warning", "critical"} else "resolved"
    telemetry_event_ids = [
        str(event.get("event_id") or event.get("id"))
        for event in body.telemetry_events
        if event.get("event_id") or event.get("id")
    ]
    evidence_refs = [
        *body.evidence_refs,
        {"type": "runtime_binding", "id": body.binding_id},
        {"type": "artifact", "id": body.artifact_id, "version": body.artifact_version},
        {"type": "capital_pool", "id": body.capital_pool_id},
    ]
    record = {
        "id": record_id,
        "record_id": record_id,
        "tenant_id": tenant_id,
        "recon_type": "live_run",
        "scope_ref": body.binding_id,
        "runtime_binding_id": body.binding_id,
        "binding_id": body.binding_id,
        "runtime_id": body.runtime_id,
        "deployment_stage": "live",
        "deployment_plan_id": body.deployment_plan_id,
        "artifact_id": body.artifact_id,
        "artifact_version": body.artifact_version,
        "capital_pool_id": body.capital_pool_id,
        "persona_capital_binding_id": body.persona_capital_binding_id,
        "trace_id": body.trace_id,
        "live_run_id": body.live_run_id,
        "expected_ref": body.baseline_ref,
        "actual_ref": body.actual_ref,
        "delta_summary": {
            "baseline_metrics": body.baseline_metrics,
            "observed_metrics": observed,
            "drift_checks": drift_checks,
            "reconciliation_checks": reconciliation_checks,
            "telemetry_event_count": len(body.telemetry_events),
        },
        "severity": severity,
        "status": status,
        "evidence_refs": evidence_refs,
        "generated_at": timestamp,
        "source_contract": {
            "telemetry_truth_owner": "telemetry-ingest",
            "incident_truth_owner": "incidents",
            "evolution_truth_owner": "evolution",
            "derived_only": False,
            "emergency_control_chain_affected": False,
        },
    }
    stored = store.put_reconciliation_record(record)

    incident_case = None
    incident_request = None
    evolution_proposal = None
    if status == "open" and body.create_incident_on_breach:
        incident_id = f"{record_id}-incident-001"
        incident_request = {
            "incident_id": incident_id,
            "title": f"Live reconciliation threshold breach for {body.binding_id}",
            "status": "open",
            "severity": _incident_severity(severity),
            "binding_id": body.binding_id,
            "deployment_stage": "live",
            "deployment_plan_id": body.deployment_plan_id,
            "capital_pool_id": body.capital_pool_id,
            "persona_capital_binding_id": body.persona_capital_binding_id,
            "artifact_id": body.artifact_id,
            "artifact_version": body.artifact_version,
            "runtime_id": body.runtime_id,
            "trace_id": body.trace_id,
            "telemetry_event_ids": telemetry_event_ids,
            "evidence_summary": f"ReconciliationRecord {record_id} severity={severity}",
            "lineage_ref": f"{body.artifact_id}@{body.artifact_version}",
        }
        incidents_api_url = os.getenv("PANTHEON_INCIDENTS_API_URL", "").rstrip("/")
        if incidents_api_url:
            incident_case = _post_json(f"{incidents_api_url}/api/incidents", incident_request)

    if status == "open" and body.propose_evolution_on_breach:
        evolution_proposal = {
            "decision_id": f"{record_id}-evo-001",
            "decision_state": "proposed",
            "target_type": "candidate_artifact",
            "target_id": body.artifact_id,
            "target_version": body.artifact_version,
            "target_stage": "live",
            "action_type": "flag_for_review",
            "rationale": f"Live reconciliation threshold breach from {record_id}; no automatic evolution execution.",
            "capital_pool_id": body.capital_pool_id,
            "linked_incident_id": (incident_case or incident_request or {}).get("incident_id"),
            "evidence_refs": [
                {"ref_type": "telemetry_event", "ref_id": event_id}
                for event_id in telemetry_event_ids
            ]
            + [{"ref_type": "runtime_binding", "ref_id": body.binding_id}],
            "metadata": {
                "source_reconciliation_record_id": record_id,
                "proposed_only": True,
                "automatic_execution_allowed": False,
            },
        }

    return {
        "record": stored,
        "incident_request": incident_request,
        "incident_case": incident_case,
        "evolution_proposal": evolution_proposal,
    }


@app.get("/api/reconciliation-drift/evaluations")
def list_evaluations(binding_id: Optional[str] = Query(default=None)) -> List[Dict[str, Any]]:
    evaluations = _tenant_scoped(store.list_evaluations())
    if binding_id:
        evaluations = [item for item in evaluations if item.get("binding_id") == binding_id]
    return evaluations


@app.get("/api/reconciliation-drift/evaluations/{evaluation_id}")
def get_evaluation(evaluation_id: str) -> Dict[str, Any]:
    evaluation = store.get_evaluation(evaluation_id, tenant_id=_current_tenant_id())
    if not evaluation or not _tenant_visible(evaluation):
        raise HTTPException(status_code=404, detail="drift evaluation not found")
    return evaluation


@app.get("/api/reconciliation-drift/reconciliation-records")
def list_reconciliation_records(
    binding_id: Optional[str] = Query(default=None),
    runtime_id: Optional[str] = Query(default=None),
) -> List[Dict[str, Any]]:
    records = _tenant_scoped(store.list_reconciliation_records())
    if binding_id:
        records = [
            item for item in records
            if binding_id in (item.get("binding_id"), item.get("runtime_binding_id"), item.get("scope_ref"))
        ]
    if runtime_id:
        records = [item for item in records if item.get("runtime_id") == runtime_id]
    return records


@app.get("/api/reconciliation-drift/reconciliation-records/{record_id}")
def get_reconciliation_record(record_id: str) -> Dict[str, Any]:
    record = store.get_reconciliation_record(
        record_id,
        tenant_id=_current_tenant_id(),
    )
    if not record or not _tenant_visible(record):
        raise HTTPException(status_code=404, detail="reconciliation record not found")
    return record


@app.get("/api/reconciliation-drift/drift-reports")
def list_drift_reports(
    scope_ref: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
) -> List[Dict[str, Any]]:
    reports = _tenant_scoped(store.list_drift_reports())
    if scope_ref:
        reports = [item for item in reports if item.get("scope_ref") == scope_ref]
    if status:
        reports = [item for item in reports if item.get("status") == status]
    return reports


@app.get("/api/reconciliation-drift/drift-reports/{report_id}")
def get_drift_report(report_id: str) -> Dict[str, Any]:
    report = store.get_drift_report(report_id, tenant_id=_current_tenant_id())
    if not report or not _tenant_visible(report):
        raise HTTPException(status_code=404, detail="drift report not found")
    return report


@app.get("/api/reconciliation-drift/summary")
def summary(binding_id: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    evaluations = list_evaluations(binding_id=binding_id)
    alerts = list_alert_handoffs(binding_id=binding_id, handoff_state=None)
    drift_reports = _tenant_scoped(store.list_drift_reports())
    if binding_id:
        drift_reports = [
            item for item in drift_reports if item.get("binding_id") == binding_id or item.get("scope_ref") == binding_id
        ]
    latest = sorted(evaluations, key=lambda item: str(item.get("evaluated_at") or ""))[-1] if evaluations else None
    return {
        "status": latest.get("status") if latest else "degraded",
        "binding_id": binding_id,
        "latest_evaluation_id": latest.get("evaluation_id") if latest else None,
        "evaluation_count": len(evaluations),
        "alert_handoff_count": len(alerts),
        "drift_report_count": len(drift_reports),
        "derived_only": True,
        "emergency_control_chain_affected": False,
        "projection_updated_at": latest.get("evaluated_at") if latest else None,
    }


@app.get("/api/reconciliation-drift/reconciliation-status/{binding_id}")
def reconciliation_status(binding_id: str) -> Dict[str, Any]:
    evaluations = list_evaluations(binding_id=binding_id)
    if not evaluations:
        raise HTTPException(status_code=404, detail="no reconciliation status for binding")
    latest = sorted(evaluations, key=lambda item: str(item.get("evaluated_at") or ""))[-1]
    return {
        "binding_id": binding_id,
        "status": latest["status"],
        "evaluation_id": latest["evaluation_id"],
        "checks": latest["reconciliation_checks"],
        "projection_updated_at": latest["evaluated_at"],
        "derived_only": True,
    }


@app.get("/api/reconciliation-drift/alerts")
def list_alert_handoffs(
    binding_id: Optional[str] = Query(default=None),
    handoff_state: Optional[str] = Query(default="ready"),
) -> List[Dict[str, Any]]:
    alerts = _tenant_scoped(store.list_alert_handoffs())
    if binding_id:
        alerts = [item for item in alerts if item.get("binding_id") == binding_id]
    if handoff_state:
        alerts = [item for item in alerts if item.get("handoff_state") == handoff_state]
    return alerts


@app.post("/api/reconciliation-drift/alerts/{alert_id}/handoff")
def mark_alert_handoff(alert_id: str, body: HandoffBody) -> Dict[str, Any]:
    alert = store.get_alert_handoff(alert_id, tenant_id=_current_tenant_id())
    if not alert or not _tenant_visible(alert):
        raise HTTPException(status_code=404, detail="alert handoff not found")
    updated = dict(alert)
    updated["handoff_state"] = body.handoff_state
    updated["target_service"] = body.target_service or updated.get("target_service")
    updated["handoff_actor"] = body.actor_id
    updated["handoff_note"] = body.note
    updated["handed_off_at"] = utc_now()
    return store.put_alert_handoff(updated)


class ScheduledReconcileBody(BaseModel):
    tenant_id: Optional[str] = None
    tick_id: Optional[str] = None
    window_id: Optional[str] = None
    worker_id: Optional[str] = None
    sla_seconds: Optional[float] = Field(default=None, gt=0)
    binding_id: Optional[str] = None
    dispatch_incidents: bool = True
    lifecycle_only: bool = False


class IncidentTriggerBody(BaseModel):
    tenant_id: Optional[str] = None
    incident: Optional[Dict[str, Any]] = None
    anomaly_event: Optional[Dict[str, Any]] = None
    event: Optional[Dict[str, Any]] = None
    incident_id: Optional[str] = None
    source_event_id: Optional[str] = None
    reason: Optional[str] = None
    binding_id: Optional[str] = None
    runtime_id: Optional[str] = None
    deployment_stage: Optional[str] = None
    telemetry_event_ids: List[str] = Field(default_factory=list)
    observed_metrics: Dict[str, Any] = Field(default_factory=dict)
    baseline_metrics: Dict[str, Any] = Field(default_factory=dict)
    thresholds: Dict[str, Any] = Field(default_factory=dict)
    generated_at: Optional[str] = None


def _tick_evaluation_id(tick_id: str, binding_id: str) -> str:
    safe_tick = _safe_id_component(tick_id, fallback="tick", limit=32)
    safe_binding = _safe_id_component(binding_id, fallback="binding", limit=24)
    # Keep readable prefixes while binding uniqueness to the complete inputs;
    # hosted tick IDs commonly share more than the first 32 characters.
    identity_digest = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"pantheon:scheduled-reconciliation-evaluation:{tick_id}:{binding_id}",
    ).hex[:16]
    return f"rdeval-sched-{safe_tick}-{safe_binding}-{identity_digest}"


def _trigger_evaluation_id(trigger_id: str, binding_id: str) -> str:
    safe_trigger = _safe_id_component(trigger_id, fallback="trigger", limit=48)
    safe_binding = _safe_id_component(binding_id, fallback="binding", limit=24)
    return f"rdeval-incident-{safe_trigger}-{safe_binding}"


def _summary_telemetry_event_ids(summary: Dict[str, Any]) -> List[str]:
    values: List[Any] = list(summary.get("telemetry_event_ids") or [])
    values.extend((summary.get("last_event_id"), summary.get("last_heartbeat_event_id")))
    return list(dict.fromkeys(str(value) for value in values if value))


def _summary_observed_metrics(summary: Dict[str, Any]) -> Dict[str, float]:
    observed: Dict[str, float] = {}
    nested = summary.get("observed_metrics") or summary.get("metrics") or {}
    if isinstance(nested, dict):
        for key, raw_value in nested.items():
            number = _numeric(raw_value)
            if number is not None:
                observed[str(key)] = number
    for key in (
        "pnl",
        "drawdown",
        "sharpe_ratio",
        "fill_rate",
        "avg_slippage_bps",
        "total_trades",
        "queue_lag_ms",
        "event_delivery_lag_ms",
    ):
        number = _numeric(summary.get(key))
        if number is not None:
            observed[key] = number
    return observed


def _summary_risk_metrics(summary: Dict[str, Any]) -> Dict[str, float]:
    """Risk telemetry aggregates (drawdown) read with runtime-summary provenance.

    Kept apart from the snapshot-grain observed metrics: a committed position
    snapshot only carries qty/price/cash, so drawdown must not be dropped, nor
    transplanted onto the snapshot.
    """
    nested = summary.get("observed_metrics") or summary.get("metrics") or {}
    risk: Dict[str, float] = {}
    for key in ONE_SIDED_HIGHER_IS_WORSE_METRICS:
        number = _numeric(summary.get(key))
        if number is None and isinstance(nested, dict):
            number = _numeric(nested.get(key))
        if number is not None and math.isfinite(number):
            risk[key] = number
    return risk


def _resolve_scheduled_metrics(summary: Dict[str, Any]) -> Dict[str, float]:
    committed_snapshot = summary.get("committed_position_snapshot")
    if isinstance(committed_snapshot, dict):
        observed: Dict[str, float] = {}
        # Retain operational lag and baseline slippage from summary with own provenance
        for key in ("queue_lag_ms", "event_delivery_lag_ms", "avg_slippage_bps"):
            num = _numeric(summary.get(key))
            if num is not None and math.isfinite(num):
                observed[key] = num

        # Resolve snapshot financial metrics from that event only
        nested = committed_snapshot.get("metrics") or {}
        if isinstance(nested, dict):
            for key, raw_value in nested.items():
                num = _numeric(raw_value)
                if num is not None and math.isfinite(num):
                    observed[str(key)] = num
        for key in (
            "position_qty",
            "price",
            "cash",
            "pnl",
            "drawdown",
            "sharpe_ratio",
            "fill_rate",
            "total_trades",
        ):
            num = _numeric(committed_snapshot.get(key))
            if num is not None and math.isfinite(num):
                observed[key] = num
        return observed
    return _summary_observed_metrics(summary)


def _paper_lifecycle_identity(
    summary: Dict[str, Any],
    *,
    binding_id: str,
    runtime_id: str,
) -> tuple[Dict[str, Any] | None, str | None]:
    """Return a complete live paper lifecycle identity or a fail-closed reason."""
    committed_snapshot = summary.get("committed_position_snapshot")
    last = summary.get("last_lifecycle_identity")

    if isinstance(committed_snapshot, dict):
        raw_identity = committed_snapshot
    elif isinstance(last, dict):
        evt_type = str(last.get("event_type") or "").strip()
        if evt_type == "position_snapshot":
            # Admitted-but-not-committed position snapshot cannot qualify:
            return None, "missing_lifecycle_identity"
        raw_identity = last
    else:
        return None, "missing_lifecycle_identity"

    identity = json.loads(json.dumps(raw_identity))
    missing = [
        field
        for field in _REQUIRED_LIFECYCLE_IDENTITY_FIELDS
        if identity.get(field) in (None, "", [], {})
    ]
    if missing:
        return None, "incomplete_lifecycle_identity:" + ",".join(missing)

    sequence_no = identity.get("sequence_no")
    if isinstance(sequence_no, bool) or not isinstance(sequence_no, int) or sequence_no < 1:
        return None, "invalid_lifecycle_sequence"

    raw_envelope = identity.get("correlation_envelope")
    try:
        envelope = validate_envelope(raw_envelope)
    except (CorrelationEnvelopeError, TypeError, ValueError):
        return None, "invalid_correlation_envelope"

    paper_scopes = {
        str(summary.get("deployment_stage") or "").strip().lower(),
        str(identity.get("environment") or "").strip().lower(),
        str(identity.get("execution_mode") or "").strip().lower(),
        str(identity.get("deployment_stage") or "").strip().lower(),
        str(envelope.get("environment") or "").strip().lower(),
    }
    if paper_scopes != {"paper"}:
        return None, "non_paper_lifecycle"
    if str(identity.get("source_mode") or "").strip().lower() != "live":
        return None, "non_live_lifecycle_source"
    if str(identity.get("binding_id")) != binding_id:
        return None, "lifecycle_binding_mismatch"
    if str(identity.get("runtime_id")) != runtime_id:
        return None, "lifecycle_runtime_mismatch"
    if str(identity.get("event_id")) != str(envelope.get("event_id")):
        return None, "lifecycle_event_envelope_mismatch"
    if str(identity.get("tenant_id")) != str(envelope.get("tenant_id")):
        return None, "lifecycle_tenant_mismatch"
    if str(identity.get("trace_id")) != str(envelope.get("trace_id")):
        return None, "lifecycle_trace_mismatch"
    if str(identity.get("aggregate_type")) != "trade_journey":
        return None, "invalid_lifecycle_aggregate_type"
    if str(identity.get("aggregate_id")) != str(envelope.get("journey_id")):
        return None, "lifecycle_aggregate_mismatch"

    authority_refs = identity.get("authority_refs")
    if not isinstance(authority_refs, dict):
        authority_refs = {}
    metadata = identity.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    authority_persona_id = authority_refs.get("persona_id")
    metadata_persona_id = metadata.get("persona_id")
    authority_persona_id = (
        authority_persona_id.strip()
        if isinstance(authority_persona_id, str)
        else ""
    )
    metadata_persona_id = (
        metadata_persona_id.strip()
        if isinstance(metadata_persona_id, str)
        else ""
    )
    if not authority_persona_id and not metadata_persona_id:
        return None, "missing_lifecycle_persona_id"
    if (
        authority_persona_id
        and metadata_persona_id
        and authority_persona_id != metadata_persona_id
    ):
        return None, "lifecycle_persona_id_mismatch"
    persona_id = authority_persona_id or metadata_persona_id
    # Normalize the projector-complete persona identity into both locations so
    # every appended lifecycle event preserves the authority reference and the
    # metadata projection used by downstream readers.
    authority_refs["persona_id"] = persona_id
    metadata["persona_id"] = persona_id
    identity["authority_refs"] = authority_refs
    identity["metadata"] = metadata

    target = identity.get("target")
    if not isinstance(target, dict) or not str(target.get("strategy_id") or "").strip():
        return None, "incomplete_lifecycle_target"

    for summary_field, identity_field in (
        ("capital_pool_id", "capital_pool_id"),
        ("artifact_id", "artifact_id"),
        ("artifact_version", "artifact_version"),
        ("persona_capital_binding_id", "persona_capital_binding_id"),
    ):
        summary_value = summary.get(summary_field)
        if summary_value not in (None, "") and str(summary_value) != str(identity.get(identity_field)):
            return None, f"lifecycle_{identity_field}_mismatch"
    summary_plan_id = summary.get("deployment_plan_id") or summary.get("plan_id")
    if summary_plan_id not in (None, "") and str(summary_plan_id) != str(identity.get("plan_id")):
        return None, "lifecycle_plan_id_mismatch"
    return identity, None


def _scheduled_lifecycle_event(
    *,
    summary: Dict[str, Any],
    evaluation: Dict[str, Any],
    timestamp: str,
) -> tuple[Dict[str, Any] | None, str | None]:
    binding_id = str(evaluation.get("binding_id") or "").strip()
    runtime_id = str(evaluation.get("runtime_id") or "").strip()
    identity, reason = _paper_lifecycle_identity(
        summary,
        binding_id=binding_id,
        runtime_id=runtime_id,
    )
    if identity is None:
        return None, reason
    if identity["event_type"] in {
        "reconciliation_completed",
        "reconciliation_failed",
    }:
        return None, "lifecycle_already_reconciled"
    if is_noop_order(identity, identity.get("metadata") or {}):
        return None, "lifecycle_noop_order_closed"
    if identity["event_type"] != "position_snapshot":
        return None, "lifecycle_in_flight"

    evaluation_id = str(evaluation["evaluation_id"])
    event_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"pantheon:scheduled-reconciliation:{evaluation_id}",
        )
    )
    event_type = (
        "reconciliation_completed"
        if str(evaluation.get("status") or "").lower() == "ok"
        else "reconciliation_failed"
    )
    sequence_no = int(identity["sequence_no"]) + 1
    envelope = propagate_envelope(
        identity["correlation_envelope"],
        producer=_LIFECYCLE_APPEND_PRODUCER,
        event_id=event_id,
        event_time=timestamp,
        received_at=timestamp,
        producer_revision=1,
    )
    metadata = json.loads(json.dumps(identity.get("metadata") or {}))
    metadata.update(
        {
            "reconciliation_evaluation_id": evaluation_id,
            "reconciliation_tick_id": evaluation.get("tick_id"),
            "reconciliation_status": evaluation.get("status"),
            "signal_id": identity["signal_id"],
            "run_id": identity["run_id"],
            "loop_run_id": identity["loop_run_id"],
            "journey_id": envelope["journey_id"],
            "correlation_envelope": envelope,
            "sequence_no": sequence_no,
            "causal_parent_id": identity["event_id"],
            "source_mode": "live",
        }
    )
    event: Dict[str, Any] = {
        "event_id": event_id,
        "event_type": event_type,
        "created_at": timestamp,
        "tenant_id": identity["tenant_id"],
        "environment": "paper",
        "execution_mode": "paper",
        "deployment_stage": "paper",
        "binding_id": identity["binding_id"],
        "runtime_id": identity["runtime_id"],
        "capital_pool_id": identity["capital_pool_id"],
        "artifact_id": identity["artifact_id"],
        "artifact_version": identity["artifact_version"],
        "plan_id": identity["plan_id"],
        "persona_capital_binding_id": identity["persona_capital_binding_id"],
        "trace_id": envelope["trace_id"],
        "signal_id": identity["signal_id"],
        "run_id": identity["run_id"],
        "loop_run_id": identity["loop_run_id"],
        "journey_id": envelope["journey_id"],
        "aggregate_type": identity["aggregate_type"],
        "aggregate_id": identity["aggregate_id"],
        "sequence_no": sequence_no,
        "causal_parent_id": identity["event_id"],
        "source_mode": "live",
        "reconciliation_id": event_id,
        "target": json.loads(json.dumps(identity["target"])),
        "metrics": {
            "action": event_type,
            "reconciliation_status": evaluation.get("status"),
            "drift_check_count": len(evaluation.get("drift_checks") or []),
            "reconciliation_check_count": len(evaluation.get("reconciliation_checks") or []),
        },
        "metadata": metadata,
        "correlation_envelope": envelope,
    }
    if isinstance(identity.get("authority_refs"), dict):
        event["authority_refs"] = json.loads(json.dumps(identity["authority_refs"]))
    return event, None


def _latest_accepted_lifecycle_append(
    binding_id: str,
    *,
    evaluations: Optional[List[Dict[str, Any]]] = None,
) -> tuple[Dict[str, Any], Dict[str, Any]] | None:
    """Return the latest accepted scheduled append for a runtime binding."""
    candidates: List[
        tuple[datetime, str, str, Dict[str, Any], Dict[str, Any]]
    ] = []
    source = (
        evaluations
        if evaluations is not None
        else _tenant_scoped(store.list_evaluations_for_binding(binding_id))
    )
    for evaluation in source:
        if str(evaluation.get("binding_id") or "") != binding_id:
            continue
        raw_state = evaluation.get("lifecycle_append")
        if not isinstance(raw_state, dict) or raw_state.get("status") != "accepted":
            continue
        state = dict(raw_state)
        accepted_at_text = str(
            state.get("accepted_at")
            or state.get("attempted_at")
            or evaluation.get("evaluated_at")
            or ""
        )
        accepted_at = _strict_lifecycle_datetime(accepted_at_text) or datetime.min.replace(
            tzinfo=timezone.utc
        )
        candidates.append(
            (
                accepted_at,
                accepted_at_text,
                str(evaluation.get("evaluation_id") or evaluation.get("id") or ""),
                evaluation,
                state,
            )
        )
    if not candidates:
        return None
    _, _, _, evaluation, state = max(candidates, key=lambda candidate: candidate[:3])
    return evaluation, state


def _strict_lifecycle_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


_MISSING_APPEND_DEADLINE_SECONDS = float(
    os.getenv("PANTHEON_RECON_MISSING_APPEND_DEADLINE_SECONDS", "1800")
)


def _committed_append_evidence(
    *,
    summary: Dict[str, Any],
    identity: Dict[str, Any],
    binding_id: str,
    accepted_event_id: str,
    observed_event_id: str,
    telemetry_url: Optional[str],
    tenant_id: Optional[str],
    service_token: Optional[str],
    timeout_seconds: float,
) -> str:
    """Classify an accepted append against committed telemetry.

    Returns "superseded" (event committed and a later same-binding event is
    ordered after it), "missing" (no committed event) or "unknown".
    """
    if not telemetry_url or timeout_seconds <= 0:
        return "unknown"
    tid = str(summary.get("tenant_id") or identity.get("tenant_id") or tenant_id or "").strip()
    if not tid:
        return "unknown"
    try:
        from telemetry_client import fetch_accepted_event, verify_durable_event_order

        if fetch_accepted_event(
            telemetry_url,
            accepted_event_id,
            tenant_id=tid,
            service_token=service_token,
            timeout_seconds=timeout_seconds,
        ) is None:
            return "missing"
        if not observed_event_id or observed_event_id == accepted_event_id:
            return "unknown"
        fields = {}
        for key in ("runtime_id", "artifact_id", "artifact_version"):
            value = summary.get(key) or identity.get(key)
            if not isinstance(value, str) or not value.strip():
                return "unknown"
            fields[key] = value.strip()
        ok, _, pair = verify_durable_event_order(
            telemetry_url,
            accepted_event_id=accepted_event_id,
            observed_event_id=observed_event_id,
            tenant_id=tid,
            service_token=service_token,
            timeout_seconds=timeout_seconds,
            expected_binding_id=binding_id,
            expected_runtime_id=fields["runtime_id"],
            expected_artifact_id=fields["artifact_id"],
            expected_artifact_version=fields["artifact_version"],
        )
        if (
            ok
            and pair.get("accepted_event_id") == accepted_event_id
            and pair.get("observed_event_id") == observed_event_id
            and pair.get("binding_id") == binding_id
        ):
            return "superseded"
    except Exception:  # noqa: BLE001 - uncertain evidence stays deferred.
        return "unknown"
    return "unknown"


def _accepted_append_visibility_reason(
    *,
    summary: Dict[str, Any],
    binding_id: str,
    timestamp: str,
    evaluations: Optional[List[Dict[str, Any]]] = None,
    telemetry_url: Optional[str] = None,
    tenant_id: Optional[str] = None,
    service_token: Optional[str] = None,
    timeout_seconds: float = 5.0,
) -> tuple[str | None, Dict[str, Any]]:
    """Fail closed until a prior accepted append is visible in the projector.

    Once the projector has returned the accepted event itself, the persisted
    visibility acknowledgement permits a later, strictly newer lifecycle event
    to be reconciled.  This avoids both stale sequence reuse and a permanent
    deadlock after a subsequent non-reconciliation lifecycle stage arrives.
    """
    latest = _latest_accepted_lifecycle_append(binding_id, evaluations=evaluations)
    if latest is None:
        return None, {}

    accepted_evaluation, accepted_state = latest
    accepted_event = (
        accepted_state.get("event")
        if isinstance(accepted_state.get("event"), dict)
        else {}
    )
    accepted_event_id = str(
        accepted_state.get("event_id") or accepted_event.get("event_id") or ""
    ).strip()
    raw_identity = summary.get("last_lifecycle_identity")
    identity = raw_identity if isinstance(raw_identity, dict) else {}
    observed_event_id = identity["event_id"].strip() if isinstance(identity.get("event_id"), str) else ""
    raw_recent_event_ids = summary.get("recent_lifecycle_event_ids")
    recent_event_ids = (
        [str(value or "").strip() for value in raw_recent_event_ids]
        if isinstance(raw_recent_event_ids, list)
        else []
    )
    recent_event_ids = [value for value in recent_event_ids if value]

    visibility = {
        "waiting_for_event_id": accepted_event_id or None,
        "observed_event_id": observed_event_id or None,
    }
    if not accepted_event_id:
        return "accepted_lifecycle_append_state_incomplete", visibility

    ordered_after_accepted = (
        accepted_event_id in recent_event_ids
        and observed_event_id in recent_event_ids
        and recent_event_ids[-1] == observed_event_id
        and recent_event_ids.index(accepted_event_id)
        < recent_event_ids.index(observed_event_id)
    )
    visibility_source = None
    if observed_event_id == accepted_event_id:
        visibility_source = "last_lifecycle_identity"
    elif ordered_after_accepted:
        visibility_source = "recent_lifecycle_event_ids"

    if visibility_source is not None:
        if not accepted_state.get("summary_visibility_confirmed_at"):
            accepted_state["summary_visibility_confirmed_at"] = timestamp
            accepted_state["summary_visibility_event_id"] = accepted_event_id
            accepted_state["summary_visibility_observed_event_id"] = observed_event_id or None
            accepted_state["summary_visibility_source"] = visibility_source
            accepted_evaluation["lifecycle_append"] = accepted_state
            store.put_evaluation(accepted_evaluation)

    if observed_event_id == accepted_event_id:
        return None, visibility

    if accepted_state.get("visibility_terminal_failure_at"):
        return "accepted_lifecycle_append_missing_terminal", visibility

    if not accepted_state.get("summary_visibility_confirmed_at"):
        # The accepted event may have fallen out of the bounded recent window
        # after a later lifecycle event superseded it. Committed telemetry is
        # the authority; the window and ordering checks stay unchanged.
        committed = _committed_append_evidence(
            summary=summary,
            identity=identity,
            binding_id=binding_id,
            accepted_event_id=accepted_event_id,
            observed_event_id=observed_event_id,
            telemetry_url=telemetry_url,
            tenant_id=tenant_id,
            service_token=service_token,
            timeout_seconds=timeout_seconds,
        )
        if committed == "superseded":
            accepted_state["summary_visibility_confirmed_at"] = timestamp
            accepted_state["summary_visibility_event_id"] = accepted_event_id
            accepted_state["summary_visibility_observed_event_id"] = observed_event_id or None
            accepted_state["summary_visibility_source"] = "committed_telemetry_superseded"
            accepted_evaluation["lifecycle_append"] = accepted_state
            store.put_evaluation(accepted_evaluation)
        elif committed == "missing":
            missing_since = str(accepted_state.get("committed_missing_since") or "")
            if not missing_since:
                accepted_state["committed_missing_since"] = timestamp
                missing_since = timestamp
            started = _strict_lifecycle_datetime(missing_since)
            now = _strict_lifecycle_datetime(timestamp)
            if (
                started is not None
                and now is not None
                and (now - started).total_seconds() >= _MISSING_APPEND_DEADLINE_SECONDS
            ):
                accepted_state["visibility_terminal_failure_at"] = timestamp
                accepted_evaluation["lifecycle_append"] = accepted_state
                store.put_evaluation(accepted_evaluation)
                return "accepted_lifecycle_append_missing_terminal", visibility
            accepted_evaluation["lifecycle_append"] = accepted_state
            store.put_evaluation(accepted_evaluation)
            return "accepted_lifecycle_append_not_visible", visibility
        else:
            return "accepted_lifecycle_append_not_visible", visibility

    raw_observed_sequence = identity.get("sequence_no")
    raw_accepted_sequence = accepted_event.get("sequence_no")
    observed_sequence = (
        raw_observed_sequence
        if isinstance(raw_observed_sequence, int)
        and not isinstance(raw_observed_sequence, bool)
        else -1
    )
    accepted_sequence = (
        raw_accepted_sequence
        if isinstance(raw_accepted_sequence, int)
        and not isinstance(raw_accepted_sequence, bool)
        else -1
    )
    same_aggregate = (
        str(identity.get("aggregate_type") or "")
        == str(accepted_event.get("aggregate_type") or "")
        and str(identity.get("aggregate_id") or "")
        == str(accepted_event.get("aggregate_id") or "")
    )
    def _durable_order_verified() -> bool:
        if not (accepted_state.get("summary_visibility_confirmed_at") and telemetry_url and accepted_event_id and observed_event_id):
            return False
        try:
            from telemetry_client import verify_durable_event_order
            if "last_lifecycle_identity" in summary and not isinstance(summary["last_lifecycle_identity"], dict): raise ValueError
            def _field(k: str) -> str:
                raws = [s[k] for s in (summary, identity) if k in s]
                if not raws or any(not isinstance(x, str) or isinstance(x, bool) or not x.strip() for x in raws) or len(set(x.strip() for x in raws)) > 1: raise ValueError
                return raws[0].strip()
            tid = _field("tenant_id")
            if tenant_id is not None and (not isinstance(tenant_id, str) or isinstance(tenant_id, bool) or tenant_id.strip() != tid): raise ValueError
            if not isinstance(binding_id, str) or isinstance(binding_id, bool) or not binding_id.strip(): raise ValueError
            bid = binding_id.strip()
            b_owner = [s[k] for s in (summary, identity) for k in ("binding_id", "runtime_binding_id") if k in s]
            if not b_owner or any(not isinstance(b, str) or isinstance(b, bool) or b.strip() != bid for b in b_owner): raise ValueError
            rid, aid, av = _field("runtime_id"), _field("artifact_id"), _field("artifact_version")
            ok, _, p = verify_durable_event_order(
                telemetry_url, accepted_event_id=accepted_event_id, observed_event_id=observed_event_id,
                tenant_id=tid, service_token=service_token, timeout_seconds=timeout_seconds,
                expected_binding_id=bid, expected_runtime_id=rid.strip(), expected_artifact_id=aid.strip(), expected_artifact_version=av.strip(),
            )
            if ok and p and p.get("accepted_event_id") == accepted_event_id and p.get("observed_event_id") == observed_event_id and p.get("binding_id") == bid:
                return True
        except Exception: pass
        return False

    if same_aggregate:
        if observed_sequence < accepted_sequence:
            return "accepted_lifecycle_append_not_visible", visibility
        if observed_sequence == accepted_sequence:
            # Same-sequence reuse by a different event: only committed
            # telemetry order can prove the observed event is later.
            if observed_event_id != accepted_event_id and _durable_order_verified():
                return None, visibility
            return "accepted_lifecycle_append_not_visible", visibility
        return None, visibility

    # Aggregate sequence numbers restart for a new journey. Ordered projector
    # receipts are the only authoritative proof that the new aggregate was
    # observed after the accepted reconciliation; producer timestamps can be
    # skewed or backfilled and therefore cannot replace projector order.
    if ordered_after_accepted:
        return None, visibility

    if _durable_order_verified():
        return None, visibility

    return "accepted_lifecycle_append_not_visible", visibility


def _append_telemetry_lifecycle_event(
    telemetry_url: str,
    event: Dict[str, Any],
    *,
    tenant_id: str | None = None,
    service_token: str | None = None,
    timeout_seconds: float = 5.0,
) -> Dict[str, Any]:
    """Append once and distinguish terminal acceptance from retryable ambiguity."""
    return append_lifecycle_event(
        telemetry_url,
        event,
        tenant_id=tenant_id,
        service_token=service_token,
        timeout_seconds=timeout_seconds,
        urlopen=urllib.request.urlopen,
    )


def _ensure_scheduled_lifecycle_append(
    *,
    summary: Dict[str, Any],
    evaluation: Dict[str, Any],
    telemetry_url: str,
    timestamp: str,
    timeout_seconds: float = 5.0,
    evaluations: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Persist-before-send and retry the same event for an idempotent evaluation."""
    raw_state = evaluation.get("lifecycle_append")
    state: Dict[str, Any] = dict(raw_state) if isinstance(raw_state, dict) else {}
    if state.get("status") in {
        "accepted",
        "terminal_rejected",
        "not_eligible",
        "terminal_failed",
    }:
        return state

    event = state.get("event") if isinstance(state.get("event"), dict) else None
    if event is None:
        visibility_reason, visibility = _accepted_append_visibility_reason(
            summary=summary, binding_id=evaluation.get("binding_id"),
            timestamp=timestamp, evaluations=evaluations, telemetry_url=telemetry_url,
            tenant_id=evaluation.get("tenant_id"), timeout_seconds=timeout_seconds,
        )
        if visibility_reason == "accepted_lifecycle_append_missing_terminal":
            state.update(
                {
                    "status": "terminal_failed",
                    "terminal": True,
                    "retryable": False,
                    "outcome": "terminal_failed",
                    "reason": visibility_reason,
                    "attempt_count": int(state.get("attempt_count") or 0),
                    "event_id": None,
                    "updated_at": timestamp,
                    **visibility,
                }
            )
            evaluation["lifecycle_append"] = state
            store.put_evaluation(evaluation)
            return state
        if visibility_reason is not None:
            state.update(
                {
                    "status": "deferred",
                    "terminal": False,
                    "retryable": True,
                    "outcome": "deferred",
                    "reason": visibility_reason,
                    "attempt_count": int(state.get("attempt_count") or 0),
                    "deferred_count": int(state.get("deferred_count") or 0) + 1,
                    "event_id": None,
                    "updated_at": timestamp,
                    **visibility,
                }
            )
            evaluation["lifecycle_append"] = state
            store.put_evaluation(evaluation)
            return state
        event, reason = _scheduled_lifecycle_event(
            summary=summary,
            evaluation=evaluation,
            timestamp=timestamp,
        )
        if event is None:
            inflight = reason == "lifecycle_in_flight"
            state = {
                "status": "pending" if inflight else "not_eligible",
                "terminal": not inflight,
                "retryable": inflight,
                "outcome": "pending" if inflight else "not_eligible",
                "reason": reason,
                "attempt_count": 0,
                "deferred_count": int(state.get("deferred_count") or 0),
                "event_id": None,
                "updated_at": timestamp,
            }
            evaluation["lifecycle_append"] = state
            store.put_evaluation(evaluation)
            return state
        state = {
            "status": "pending",
            "terminal": False,
            "retryable": True,
            "reason": None,
            "attempt_count": 0,
            "deferred_count": int(state.get("deferred_count") or 0),
            "event_id": event["event_id"],
            "upstream_event_id": event["causal_parent_id"],
            "event": event,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        evaluation["lifecycle_append"] = state
        # Persist the exact deterministic payload before any ambiguous network
        # boundary so a retry can only produce an exact duplicate.
        store.put_evaluation(evaluation)

    if timeout_seconds <= 0:
        delivery = {
            "status": "retryable_error",
            "terminal": False,
            "retryable": True,
            "outcome": "ambiguous",
            "http_status": 504,
            "response": None,
            "error": "scheduled reconciliation SLA budget exhausted",
        }
    else:
        try:
            delivery = _append_telemetry_lifecycle_event(
                telemetry_url,
                event,
                tenant_id=str(event.get("tenant_id") or "").strip() or None,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - delivery ambiguity must remain retryable.
            delivery = {
                "status": "retryable_error",
                "terminal": False,
                "retryable": True,
                "outcome": "ambiguous",
                "http_status": None,
                "response": None,
                "error": str(exc),
            }
    state.update(delivery)
    state["attempt_count"] = int(state.get("attempt_count") or 0) + 1
    state["attempted_at"] = timestamp
    state["updated_at"] = timestamp
    if state.get("status") == "accepted":
        state["accepted_at"] = timestamp
    evaluation["lifecycle_append"] = state
    store.put_evaluation(evaluation)
    return state


def _lifecycle_append_receipt(binding_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "binding_id": binding_id,
        "event_id": state.get("event_id"),
        "status": state.get("status"),
        "terminal": bool(state.get("terminal")),
        "retryable": bool(state.get("retryable")),
        "outcome": state.get("outcome"),
        "attempt_count": int(state.get("attempt_count") or 0),
        "reason": state.get("reason"),
        "http_status": state.get("http_status"),
        "error": state.get("error"),
    }


def _summary_binding_id(summary: Dict[str, Any]) -> str:
    return str(
        summary.get("binding_id") or summary.get("runtime_binding_id") or summary.get("id") or ""
    ).strip()


def _lag_check(metric: str, value: float | None, *, warning: float, critical: float) -> Dict[str, Any]:
    if value is None:
        return {
            "check": metric,
            "status": "degraded",
            "observed": None,
            "warning_threshold": warning,
            "critical_threshold": critical,
            "detail": f"{metric} is absent from authoritative runtime telemetry",
        }
    if value >= critical:
        status = "critical"
    elif value >= warning:
        status = "warning"
    else:
        status = "ok"
    return {
        "check": metric,
        "status": status,
        "observed": value,
        "warning_threshold": warning,
        "critical_threshold": critical,
        "detail": f"{metric}={value:g}ms",
    }


def _summary_actual_state_checks(
    summary: Dict[str, Any],
    *,
    binding_id: str,
    runtime_id: str,
    telemetry_event_ids: List[str],
    observed_metrics: Dict[str, float],
) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    missing_identity = [
        name
        for name, value in (
            ("binding_id", binding_id),
            ("runtime_id", runtime_id),
            ("telemetry_event_ids", telemetry_event_ids),
        )
        if not value
    ]
    checks.append(
        {
            "check": "authoritative_actual_identity",
            "status": "degraded" if missing_identity else "ok",
            "detail": "authoritative runtime identity and event evidence linked"
            if not missing_identity
            else "runtime summary missing " + ", ".join(missing_identity),
            "missing_fields": missing_identity,
            "telemetry_event_ids": telemetry_event_ids,
        }
    )

    state = str(summary.get("state") or "").strip().lower()
    if not state:
        state_status = "degraded"
    elif state in {"failed", "error", "dead", "stopped", "retired"}:
        state_status = "critical"
    elif state in {"degraded", "paused", "stale", "disconnected"}:
        state_status = "warning"
    else:
        state_status = "ok"
    checks.append(
        {
            "check": "runtime_state",
            "status": state_status,
            "observed": state or None,
            "detail": f"authoritative runtime state is {state or 'missing'}",
        }
    )

    health_summary = summary.get("health_summary")
    health_values = health_summary if isinstance(health_summary, dict) else {}
    unhealthy = {
        str(key): str(value)
        for key, value in health_values.items()
        if str(value).strip().lower()
        not in {"ok", "healthy", "active", "connected", "available", "not_applicable"}
    }
    if not health_values:
        health_status = "degraded"
    elif unhealthy:
        health_status = "warning"
    else:
        health_status = "ok"
    checks.append(
        {
            "check": "runtime_health_summary",
            "status": health_status,
            "observed": health_values,
            "unhealthy": unhealthy,
            "detail": "runtime health summary is authoritative and healthy"
            if health_status == "ok"
            else "runtime health summary is missing or unhealthy",
        }
    )

    if not observed_metrics:
        checks.append(
            {
                "check": "actual_metrics_presence",
                "status": "degraded",
                "detail": "runtime summary has no authoritative numeric actual-state metrics",
            }
        )
    else:
        checks.append(
            {
                "check": "actual_metrics_presence",
                "status": "ok",
                "detail": "runtime summary exposes authoritative numeric actual-state metrics",
                "metric_names": sorted(observed_metrics),
            }
        )

    checks.append(
        _lag_check(
            "queue_lag_ms",
            observed_metrics.get("queue_lag_ms"),
            warning=float(os.getenv("RECONCILIATION_DRIFT_QUEUE_LAG_WARNING_MS", "5000")),
            critical=float(os.getenv("RECONCILIATION_DRIFT_QUEUE_LAG_CRITICAL_MS", "15000")),
        )
    )
    checks.append(
        _lag_check(
            "event_delivery_lag_ms",
            observed_metrics.get("event_delivery_lag_ms"),
            warning=float(os.getenv("RECONCILIATION_DRIFT_EVENT_LAG_WARNING_MS", "10000")),
            critical=float(os.getenv("RECONCILIATION_DRIFT_EVENT_LAG_CRITICAL_MS", "30000")),
        )
    )
    return checks


def _scheduled_drift_report(
    *,
    summary: Dict[str, Any],
    evaluation: Dict[str, Any],
    telemetry_event_ids: List[str],
    timestamp: str,
) -> Dict[str, Any] | None:
    failing_checks = [
        check
        for check in [*evaluation.get("drift_checks", []), *evaluation.get("reconciliation_checks", [])]
        if check.get("status") in {"warning", "critical"}
        # Governed approved-baseline drift is observe-only at warning level.
        and not (check.get("status") == "warning" and check.get("baseline_source"))
    ]
    if not failing_checks:
        return None
    required = {
        "binding_id": evaluation.get("binding_id"),
        "runtime_id": evaluation.get("runtime_id"),
        "deployment_stage": summary.get("deployment_stage"),
        "deployment_plan_id": summary.get("deployment_plan_id") or summary.get("plan_id"),
        "capital_pool_id": summary.get("capital_pool_id"),
        "persona_capital_binding_id": summary.get("persona_capital_binding_id"),
        "artifact_id": summary.get("artifact_id"),
        "artifact_version": summary.get("artifact_version"),
        "trace_id": summary.get("trace_id"),
    }
    if any(value in (None, "") for value in required.values()) or not telemetry_event_ids:
        return None
    worst = max(failing_checks, key=lambda item: _status_rank(str(item.get("status") or "ok")))
    metric = str(worst.get("metric") or worst.get("check") or "runtime_health")
    worst_source = worst.get("baseline_source")
    if not isinstance(worst_source, dict):
        worst_source = None
    is_health = "lag" in metric or "runtime" in metric
    causal_anchor = (
        summary.get("last_heartbeat_event_id")
        if is_health
        else summary.get("last_event_id")
    )
    if causal_anchor:
        if causal_anchor not in telemetry_event_ids:
            return None
        causal_event_id = causal_anchor
    elif len(telemetry_event_ids) == 1:
        causal_event_id = telemetry_event_ids[0]
    else:
        return None
    report_id = f"drift-{_safe_id_component(causal_event_id)}-{_safe_id_component(metric)}"
    cluster_id = f"drift:{_safe_id_component(metric)}"
    severity = _incident_severity(str(worst.get("status") or "warning"))
    return {
        "id": report_id,
        "drift_report_id": report_id,
        "recon_run_id": evaluation["evaluation_id"],
        "evaluation_id": evaluation["evaluation_id"],
        "tenant_id": evaluation.get("tenant_id"),
        "drift_type": "runtime_health" if is_health else "execution",
        "incident_cluster_id": cluster_id,
        "scope_ref": required["binding_id"],
        **required,
        "telemetry_event_ids": [causal_event_id],
        "baseline_ref": (
            f"approved-artifact-baseline:{worst_source['artifact_id']}:"
            f"{worst_source['policy_source']}"
            if worst_source
            else str(summary.get("baseline_ref") or "governed-runtime-health-policy")
        ),
        "current_ref": f"telemetry-runtime-summary:{required['runtime_id']}:{causal_event_id}",
        "severity": severity,
        "status": "open",
        "recommended_action": "open_incident",
        "metrics": {
            "baseline_metrics": evaluation.get("baseline_metrics", {}),
            "current_metrics": evaluation.get("observed_metrics", {}),
            "drift_checks": failing_checks,
            "worst_metric": metric,
            "breached_metric_ids": [
                str(item.get("metric") or item.get("check")) for item in failing_checks
            ],
        },
        "evidence_refs": [
            f"telemetry_event:{causal_event_id}",
            f"runtime_binding:{required['binding_id']}",
            f"drift_evaluation:{evaluation['evaluation_id']}",
            f"drift_report:{report_id}",
        ],
        "generated_at": timestamp,
        "source_contract": {
            "telemetry_truth_owner": "telemetry-ingest",
            "incident_truth_owner": "incidents",
            "runtime_truth_owner": "runtime-manager",
            "derived_only": True,
            "emergency_control_chain_affected": False,
        },
    }


def _authoritative_runtime_binding(
    binding_id: str, *, timeout_seconds: float = 5.0
) -> Dict[str, Any] | None:
    """Read one RuntimeBinding from runtime-manager, the source incident validation uses.

    Returns None when runtime-manager is not configured or unreachable so callers
    keep their pre-existing behaviour instead of guessing at lifecycle state.
    Raises RuntimeError on lookup failure so callers can stop further lookups.
    """
    if not os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "").strip():
        return None
    if RuntimeManagerClient is None:
        raise RuntimeError("runtime_manager_client is not importable")
    try:
        binding = RuntimeManagerClient(
            require_remote=True,
            timeout_seconds=max(1, math.ceil(timeout_seconds)),
        ).get(binding_id)
    except Exception as exc:  # noqa: BLE001 - lifecycle lookup is best-effort
        logging.getLogger(__name__).warning(
            "runtime-manager binding lookup failed for %s", binding_id, exc_info=True
        )
        raise RuntimeError(f"runtime-manager lookup failed: {exc}") from exc
    return dict(binding) if isinstance(binding, dict) else None


def _binding_retired_before(binding: Dict[str, Any] | None, observed_at: str) -> bool:
    """True when the authoritative binding is retired and observed_at is not before retired_at."""
    if not binding:
        return False
    retired_at = _strict_lifecycle_datetime(binding.get("retired_at"))
    if retired_at is None:
        return str(binding.get("status") or "").strip().lower() == "retired"
    observed = _strict_lifecycle_datetime(observed_at)
    return observed is None or observed > retired_at


def _binding_paused(binding: Dict[str, Any] | None) -> bool:
    """True when runtime-manager reports the binding paused (an expected stop)."""
    if not binding:
        return False
    return str(binding.get("status") or "").strip().lower() == "paused"


def _is_retired_binding_rejection(detail: Any) -> bool:
    text = str(detail)
    return "is after RuntimeBinding" in text and "retired_at" in text


def _dispatch_scheduled_drift_report(report: Dict[str, Any], *, timeout_seconds: float = 5.0) -> Dict[str, Any]:
    stored = store.put_drift_report(report)
    result: Dict[str, Any] = {
        "status": "not_configured",
        "drift_report_id": stored["drift_report_id"],
        "incident_id": None,
        "error": None,
    }
    if timeout_seconds <= 0:
        result["status"] = "retryable_error"
        result["error"] = {"status_code": 504, "detail": "scheduled reconciliation SLA budget exhausted"}
        return result
    try:
        incident = _classify_drift_report_incident(stored, timeout_seconds=timeout_seconds)
    except HTTPException as exc:
        if _is_retired_binding_rejection(exc.detail):
            # Retirement is governance, not a fault: terminal, never retried.
            result["status"] = "terminal_rejected"
            result["reason"] = "runtime_binding_retired"
            result["detail"] = exc.detail
            return result
        result["status"] = "retryable_error"
        result["error"] = {"status_code": exc.status_code, "detail": exc.detail}
        return result
    if incident is None:
        return result
    result["status"] = "delivered"
    result["incident_id"] = str(
        incident.get("incident_id") or incident.get("id") or ""
    ).strip() or None
    return result


def _first_trigger_value(*payloads: Optional[Dict[str, Any]], keys: tuple[str, ...]) -> Any:
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in keys:
            value = payload.get(key)
            if value not in (None, ""):
                return value
    return None


def _trigger_telemetry_event_ids(body: IncidentTriggerBody) -> List[str]:
    seen: dict[str, None] = {}

    def add(value: Any) -> None:
        if value in (None, ""):
            return
        if isinstance(value, str):
            item = value.strip()
            if item:
                seen[item] = None
            return
        if isinstance(value, list):
            for part in value:
                add(part)

    incident = body.incident or {}
    anomaly_event = body.anomaly_event or body.event or {}
    add(body.telemetry_event_ids)
    add(body.source_event_id)
    add(incident.get("telemetry_event_ids"))
    add(incident.get("telemetry_event_id"))
    add(anomaly_event.get("event_id"))
    add(anomaly_event.get("telemetry_event_id"))
    add(anomaly_event.get("telemetry_event_ids"))
    return list(seen)


def _trigger_reason(body: IncidentTriggerBody) -> str:
    incident = body.incident or {}
    anomaly_event = body.anomaly_event or body.event or {}
    reason = _first_trigger_value(
        {"reason": body.reason},
        anomaly_event,
        incident,
        keys=("reason", "anomaly_type", "event_type", "title", "evidence_summary"),
    )
    return str(reason or "incident_or_anomaly_trigger").strip()


def _trigger_status(severity: Any, reason: str) -> str:
    normalized_severity = str(severity or "").strip().lower()
    normalized_reason = reason.lower()
    if normalized_severity in {"critical", "high"}:
        return "critical"
    if normalized_severity == "medium":
        return "warning"
    if any(token in normalized_reason for token in ("heartbeat", "rejection", "reject", "anomaly", "loss")):
        return "warning"
    return "degraded"


def _execute_scheduled_reconcile(
    body: ScheduledReconcileBody,
    *,
    tenant_id: str,
    timestamp: str,
    tick_id: str,
    started: Optional[float] = None,
) -> Dict[str, Any]:
    """Run a scheduled reconciliation pass over all active bindings visible in telemetry.

    Idempotent: a second call with the same tick_id skips bindings that already
    have an evaluation record for that tick, so duplicate scheduler ticks do not
    create duplicate ReconciliationRecords.
    """
    tick_start = started if started is not None else time.monotonic()
    tick_deadline = tick_start + max(1.0, float(body.sla_seconds or 60.0) - 5.0)

    def dispatch_timeout() -> float:
        return max(0.0, min(5.0, tick_deadline - time.monotonic()))

    telemetry_url = os.getenv("PANTHEON_TELEMETRY_API_URL", "").rstrip("/")
    try:
        summaries = fetch_runtime_summaries(
            telemetry_url,
            tenant_id=tenant_id,
            timeout_seconds=min(10.0, max(1.0, tick_deadline - time.monotonic())),
        )
    except TelemetryError as exc:
        err_result = {
            "status": "failure",
            "failure_code": (
                "telemetry_auth_failed"
                if isinstance(exc, TelemetryAuthError)
                else "telemetry_unavailable"
            ),
            "tick_id": tick_id,
            "trigger": "scheduled",
            "evaluated_binding_count": 0,
            "skipped_binding_count": 0,
            "evaluation_ids": [],
            "skipped_binding_ids": [],
            "telemetry_summaries_fetched": 0,
            "triggered_at": timestamp,
            "detail": str(exc),
        }
        if body.lifecycle_only:
            err_result["lifecycle_only"] = True
        return err_result

    scoped_summaries: List[Dict[str, Any]] = []
    for summary in summaries:
        summary_tenant = _mapping_tenant_id(summary)
        if summary_tenant:
            if summary_tenant == tenant_id:
                scoped_summaries.append(summary)
        elif _auth_mode() == "disabled":
            scoped_summaries.append(summary)
    summaries = scoped_summaries

    requested_binding_id = str(body.binding_id or "").strip()
    if body.lifecycle_only and not requested_binding_id:
        raise HTTPException(
            status_code=400,
            detail="lifecycle_only scheduled reconciliation requires binding_id",
        )
    if requested_binding_id:
        summaries = [
            summary
            for summary in summaries
            if _summary_binding_id(summary) == requested_binding_id
        ]

    if not summaries:
        empty_result = {
            "status": "degraded",
            "tick_id": tick_id,
            "trigger": "scheduled",
            "evaluated_binding_count": 0,
            "skipped_binding_count": 0,
            "evaluation_ids": [],
            "skipped_binding_ids": [],
            "telemetry_summaries_fetched": 0,
            "triggered_at": timestamp,
            "detail": "telemetry summaries empty",
        }
        if body.lifecycle_only:
            empty_result["lifecycle_only"] = True
        return empty_result

    existing_evaluation_ids = _evaluation_ids()

    created_evaluation_ids: List[str] = []
    skipped_binding_ids: List[str] = []
    evaluation_statuses: List[str] = []
    drift_report_ids: List[str] = []
    incident_ids: List[str] = []
    incident_delivery_errors: List[Dict[str, Any]] = []
    lifecycle_append_results: List[Dict[str, Any]] = []
    lifecycle_accepted_event_ids: List[str] = []
    lifecycle_retryable_errors: List[Dict[str, Any]] = []
    lifecycle_terminal_rejections: List[Dict[str, Any]] = []
    lifecycle_terminal_failures: List[Dict[str, Any]] = []
    lifecycle_ineligible_binding_ids: List[str] = []
    retired_binding_ids: List[str] = []
    paused_binding_ids: List[str] = []
    binding_lookup_failed = False
    dispatch_incidents = False if body.lifecycle_only else bool(body.dispatch_incidents)
    incident_candidates: List[tuple[Dict[str, Any], Dict[str, Any], List[str]]] = []

    def record_lifecycle_append(binding_id: str, state: Dict[str, Any]) -> None:
        receipt = _lifecycle_append_receipt(binding_id, state)
        lifecycle_append_results.append(receipt)
        if receipt["status"] == "accepted" and receipt["event_id"]:
            lifecycle_accepted_event_ids.append(str(receipt["event_id"]))
        elif receipt["status"] in {"retryable_error", "deferred", "pending"}:
            lifecycle_retryable_errors.append(receipt)
        elif receipt["status"] == "terminal_rejected":
            lifecycle_terminal_rejections.append(receipt)
        elif receipt["status"] == "terminal_failed":
            lifecycle_terminal_failures.append(receipt)
        elif receipt["status"] == "not_eligible":
            lifecycle_ineligible_binding_ids.append(binding_id)

    approved_baselines = _load_approved_baselines()
    for summary in summaries:
        binding_id = _summary_binding_id(summary)
        runtime_id = str(summary.get("runtime_id") or "").strip()
        if not binding_id:
            continue
        evaluation_id = _tick_evaluation_id(tick_id, binding_id)
        telemetry_event_ids = _summary_telemetry_event_ids(summary)
        observed_metrics = _resolve_scheduled_metrics(summary)
        committed_snapshot = summary.get("committed_position_snapshot")
        snap_event_id = str((committed_snapshot or {}).get("event_id") or "").strip() if isinstance(committed_snapshot, dict) else ""
        snap_run_id = str((committed_snapshot or {}).get("run_id") or ((committed_snapshot or {}).get("metadata") or {}).get("run_id") or "").strip() if isinstance(committed_snapshot, dict) else ""
        if snap_event_id and snap_event_id not in telemetry_event_ids:
            telemetry_event_ids = [snap_event_id, *telemetry_event_ids]
        raw_baseline_metrics = summary.get("baseline_metrics") or {}
        baseline_metrics: Dict[str, Any] = (
            raw_baseline_metrics if isinstance(raw_baseline_metrics, dict) else {}
        )
        approved_metrics, baseline_sources = _approved_artifact_baselines(
            summary.get("artifact_id"), approved_baselines
        )
        # Governed metrics are judged only against the approved artifact baseline;
        # a raw summary baseline never substitutes for a missing approval.
        baseline_metrics = {
            metric: value
            for metric, value in baseline_metrics.items()
            if metric not in APPROVED_BASELINE_KEYS
        }
        baseline_metrics.update(approved_metrics)
        raw_thresholds = summary.get("thresholds") or summary.get("drift_thresholds") or {}
        thresholds: Dict[str, Any] = raw_thresholds if isinstance(raw_thresholds, dict) else {}
        drift_checks = _drift_checks(
            baseline_metrics,
            {**_summary_risk_metrics(summary), **observed_metrics}
            if "drawdown" not in observed_metrics
            else observed_metrics,
            thresholds,
            baseline_sources,
        )
        reconciliation_checks = _summary_actual_state_checks(
            summary,
            binding_id=binding_id,
            runtime_id=runtime_id,
            telemetry_event_ids=telemetry_event_ids,
            observed_metrics=observed_metrics,
        )
        status = _worst_status(
            [
                str(check.get("status") or "degraded")
                for check in [*drift_checks, *reconciliation_checks]
            ]
        )

        # Only consult runtime-manager for bindings that could raise an incident or
        # whose telemetry state is not active; stop after the first failure/budget miss.
        summary_state = str(summary.get("state") or "").strip().lower()
        if (
            not binding_lookup_failed
            and (status != "ok" or summary_state not in {"active", "running", "healthy"})
        ):
            lookup_budget = dispatch_timeout()
            if lookup_budget > 0:
                try:
                    authoritative = _authoritative_runtime_binding(
                        binding_id, timeout_seconds=lookup_budget
                    )
                except RuntimeError:
                    binding_lookup_failed = True
                else:
                    if _binding_retired_before(authoritative, timestamp):
                        retired_binding_ids.append(binding_id)
                        lifecycle_ineligible_binding_ids.append(binding_id)
                        continue
                    if _binding_paused(authoritative):
                        paused_binding_ids.append(binding_id)
                        lifecycle_ineligible_binding_ids.append(binding_id)
                        continue
            else:
                binding_lookup_failed = True

        existing_evaluation = None
        if evaluation_id in existing_evaluation_ids:
            cached_eval = store.get_evaluation(
                evaluation_id,
                tenant_id=tenant_id,
            )
            if cached_eval is not None:
                cached_append = cached_eval.get("lifecycle_append")
                has_created_event = isinstance(cached_append, dict) and bool(
                    cached_append.get("event") or cached_append.get("event_id")
                )
                is_terminal_or_accepted = isinstance(cached_append, dict) and cached_append.get("status") in {
                    "accepted",
                    "terminal_rejected",
                }

                # A pending evaluation without any created lifecycle event may still be
                # paired to a newly eligible snapshot. But once an evaluation has an
                # accepted/terminal state or an originally persisted lifecycle event /
                # pending retry payload, preserve it; do not retarget its event ID to B.
                can_pair_pending_to_snapshot = (
                    snap_event_id
                    and not has_created_event
                    and not is_terminal_or_accepted
                )

                if not can_pair_pending_to_snapshot:
                    existing_evaluation = cached_eval
                    skipped_binding_ids.append(binding_id)
                    evaluation_statuses.append(str(existing_evaluation.get("status") or status))
                    target_evaluation = existing_evaluation
        if existing_evaluation is None:
            evaluation = {
                "id": evaluation_id,
                "evaluation_id": evaluation_id,
                "tenant_id": tenant_id,
                "binding_id": binding_id,
                "runtime_id": runtime_id or None,
                "run_id": snap_run_id or None,
                "snapshot_event_id": snap_event_id or None,
                "status": status,
                "summary": {
                    "status": status,
                    "telemetry_event_count": len(telemetry_event_ids),
                    "baseline_metric_count": len(baseline_metrics),
                    "observed_metric_count": len(observed_metrics),
                    "drift_check_count": len(drift_checks),
                    "reconciliation_check_count": len(reconciliation_checks),
                },
                "baseline_metrics": baseline_metrics,
                "observed_metrics": observed_metrics,
                "drift_checks": drift_checks,
                "reconciliation_checks": reconciliation_checks,
                "evidence_refs": [
                    {"type": "tick", "id": tick_id},
                    {"type": "runtime_binding", "id": binding_id},
                    *([{"type": "runtime_session", "id": runtime_id}] if runtime_id else []),
                    *[
                        {"type": "telemetry_event", "id": event_id}
                        for event_id in telemetry_event_ids
                    ],
                ],
                "tick_id": tick_id,
                "trigger": "scheduled",
                "source_contract": {
                    "telemetry_truth_owner": "telemetry-ingest",
                    "lineage_truth_owner": "lineage-read",
                    "runtime_truth_owner": "runtime-manager",
                    "derived_only": True,
                    "emergency_control_chain_affected": False,
                },
                "evaluated_at": timestamp,
            }
            stored = store.put_evaluation(evaluation)
            existing_evaluation_ids.add(evaluation_id)
            created_evaluation_ids.append(stored["evaluation_id"])
            evaluation_statuses.append(status)
            target_evaluation = stored

        lifecycle_state = _ensure_scheduled_lifecycle_append(
            summary=summary,
            evaluation=target_evaluation,
            telemetry_url=telemetry_url,
            timestamp=timestamp,
            timeout_seconds=dispatch_timeout(),
        )
        record_lifecycle_append(binding_id, lifecycle_state)
        if dispatch_incidents:
            incident_candidates.append((summary, target_evaluation, telemetry_event_ids))

    for summary, evaluation, telemetry_event_ids in incident_candidates:
        report = _scheduled_drift_report(
            summary=summary,
            evaluation=evaluation,
            telemetry_event_ids=telemetry_event_ids,
            timestamp=timestamp,
        )
        if report is None:
            continue
        existing_report = store.get_drift_report(
            str(report["drift_report_id"]),
            tenant_id=tenant_id,
        )
        report_to_dispatch = existing_report or report
        drift_report_ids.append(str(report_to_dispatch["drift_report_id"]))
        delivery = evaluation.get("incident_delivery")
        if isinstance(delivery, dict) and delivery.get("status") == "delivered":
            incident_id = str(delivery.get("incident_id") or "").strip()
            if incident_id:
                incident_ids.append(incident_id)
            continue
        if isinstance(delivery, dict) and delivery.get("status") == "terminal_rejected":
            continue
        delivery_result = _dispatch_scheduled_drift_report(
            report_to_dispatch,
            timeout_seconds=dispatch_timeout(),
        )
        evaluation["incident_delivery"] = {
            **delivery_result,
            "attempted_at": timestamp,
        }
        store.put_evaluation(evaluation)
        incident_id = str(delivery_result.get("incident_id") or "").strip()
        if incident_id:
            incident_ids.append(incident_id)
        if delivery_result.get("error"):
            incident_delivery_errors.append(
                {
                    "drift_report_id": delivery_result["drift_report_id"],
                    **delivery_result["error"],
                }
            )

    tick_status = _worst_status(evaluation_statuses)
    if incident_delivery_errors or lifecycle_retryable_errors or lifecycle_terminal_rejections:
        tick_status = "failure"
    elif not created_evaluation_ids and skipped_binding_ids:
        tick_status = _worst_status(evaluation_statuses)
    elif not created_evaluation_ids and (retired_binding_ids or paused_binding_ids):
        tick_status = "ok"
    elif not created_evaluation_ids:
        tick_status = "degraded"

    response = {
        "status": tick_status,
        "tick_id": tick_id,
        "trigger": "scheduled",
        "evaluated_binding_count": len(created_evaluation_ids),
        "skipped_binding_count": len(skipped_binding_ids),
        "evaluation_ids": created_evaluation_ids,
        "skipped_binding_ids": skipped_binding_ids,
        "drift_report_ids": drift_report_ids,
        "incident_ids": list(dict.fromkeys(incident_ids)),
        "incident_delivery_errors": incident_delivery_errors,
        "lifecycle_append_results": lifecycle_append_results,
        "lifecycle_accepted_event_ids": list(dict.fromkeys(lifecycle_accepted_event_ids)),
        "lifecycle_retryable_errors": lifecycle_retryable_errors,
        "lifecycle_terminal_rejections": lifecycle_terminal_rejections,
        "lifecycle_terminal_failures": lifecycle_terminal_failures,
        "lifecycle_ineligible_binding_ids": list(dict.fromkeys(lifecycle_ineligible_binding_ids)),
        "retired_binding_ids": retired_binding_ids,
        "paused_binding_ids": paused_binding_ids,
        "incident_dispatch_enabled": dispatch_incidents,
        "telemetry_summaries_fetched": len(summaries),
        "triggered_at": timestamp,
    }
    if body.lifecycle_only:
        response["lifecycle_only"] = True
    return response


def _completed_window_response(claim: Dict[str, Any]) -> Dict[str, Any]:
    cached = dict(claim.get("result") or {})
    evaluation_ids = [
        str(value) for value in cached.get("evaluation_ids") or [] if value
    ]
    skipped_binding_ids: List[str] = []
    for evaluation_id in evaluation_ids:
        evaluation = store.get_evaluation(
            evaluation_id,
            tenant_id=_current_tenant_id(),
        )
        binding_id = str((evaluation or {}).get("binding_id") or "").strip()
        if binding_id:
            skipped_binding_ids.append(binding_id)
    if not skipped_binding_ids:
        for item in cached.get("lifecycle_append_results") or []:
            if isinstance(item, dict) and item.get("binding_id"):
                bid = str(item["binding_id"]).strip()
                if bid and bid not in skipped_binding_ids:
                    skipped_binding_ids.append(bid)
    cached.update(
        {
            "evaluated_binding_count": 0,
            "skipped_binding_count": len(skipped_binding_ids),
            "evaluation_ids": [],
            "skipped_binding_ids": skipped_binding_ids,
            "duplicate_window": True,
            "lease_status": "completed",
        }
    )
    return cached


@app.post("/api/reconciliation-drift/scheduled-reconcile", status_code=201)
def scheduled_reconcile(body: ScheduledReconcileBody) -> Dict[str, Any]:
    """Claim and execute one deterministic tenant-scoped observation window."""

    tenant_id = _authorized_body_tenant(body.tenant_id)
    timestamp = utc_now()
    try:
        window_seconds = int(
            os.getenv("RECONCILIATION_DRIFT_SCHEDULER_WINDOW_SECONDS", "300")
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="RECONCILIATION_DRIFT_SCHEDULER_WINDOW_SECONDS is invalid",
        ) from exc
    derived_window_id = _scheduled_window_id(
        tenant_id=tenant_id,
        timestamp=timestamp,
        window_seconds=window_seconds,
    )
    tick_id = str(body.tick_id or body.window_id or derived_window_id).strip()
    window_id = str(body.window_id or tick_id).strip()
    worker_id = str(body.worker_id or "reconciliation-scheduler").strip()
    if not tick_id or not window_id or not worker_id:
        raise HTTPException(
            status_code=422,
            detail="tick_id, window_id, and worker_id must not be empty",
        )
    try:
        default_sla_seconds = float(
            os.getenv("RECONCILIATION_DRIFT_SCHEDULED_SLA_SECONDS", "60")
        )
        configured_lease_seconds = float(
            os.getenv("RECONCILIATION_DRIFT_SCHEDULER_LEASE_SECONDS", "180")
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="scheduled reconciliation SLA/lease configuration is invalid",
        ) from exc
    sla_seconds = float(body.sla_seconds or default_sla_seconds)
    lease_seconds = max(configured_lease_seconds, sla_seconds * 2)
    if (
        not math.isfinite(sla_seconds)
        or sla_seconds <= 0
        or not math.isfinite(lease_seconds)
        or lease_seconds <= 0
    ):
        raise HTTPException(
            status_code=503,
            detail="scheduled reconciliation SLA/lease must be finite and > 0",
        )

    claim_result = store.claim_work(
        tenant_id=tenant_id,
        work_type="scheduled_reconcile",
        window_id=window_id,
        owner_id=worker_id,
        lease_seconds=lease_seconds,
    )
    claim = claim_result.get("claim") or {}
    if not claim_result["acquired"]:
        if claim_result["reason"] == "completed":
            return _completed_window_response(claim)
        return {
            "status": "deferred",
            "controller_status": "degraded",
            "tick_id": tick_id,
            "window_id": window_id,
            "tenant_id": tenant_id,
            "worker_id": worker_id,
            "duplicate_window": True,
            "lease_status": claim_result["reason"],
            "lease_owner": claim.get("owner_id"),
            "lease_expires_at": claim.get("lease_expires_at"),
            "evaluated_binding_count": 0,
            "skipped_binding_count": 0,
            "evaluation_ids": [],
            "skipped_binding_ids": [],
            "triggered_at": timestamp,
        }

    started = _monotonic()
    claim_finalized = False
    try:
        result = _execute_scheduled_reconcile(
            body,
            tenant_id=tenant_id,
            timestamp=timestamp,
            tick_id=tick_id,
            started=started,
        )
        duration_seconds = max(0.0, _monotonic() - started)
        result.update(
            {
                "window_id": window_id,
                "tenant_id": tenant_id,
                "worker_id": worker_id,
                "duration_seconds": duration_seconds,
                "sla_seconds": sla_seconds,
                "within_sla": duration_seconds <= sla_seconds,
                "sla_status": "met"
                if duration_seconds <= sla_seconds
                else "breached",
                "duplicate_window": False,
                "lease_status": "acquired",
            }
        )
        terminal_failure = (
            result.get("status") in {"failure", "error", "unavailable"}
            or not result["within_sla"]
        )
        if terminal_failure:
            store.fail_work(
                claim_id=str(claim["claim_id"]),
                lease_token=str(claim["lease_token"]),
                error=(
                    "scheduled reconciliation SLA breached"
                    if not result["within_sla"]
                    else str(result.get("detail") or result.get("status"))
                ),
                result=result,
            )
        else:
            store.complete_work(
                claim_id=str(claim["claim_id"]),
                lease_token=str(claim["lease_token"]),
                result=result,
            )
        claim_finalized = True
        _record_worker_state(
            tenant_id=tenant_id,
            worker_kind="scheduler",
            worker_id=worker_id,
            window_id=window_id,
            result=result,
            updated_at=utc_now(),
        )
        return result
    except Exception as exc:
        if not claim_finalized:
            try:
                store.fail_work(
                    claim_id=str(claim["claim_id"]),
                    lease_token=str(claim["lease_token"]),
                    error=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                pass
        raise


_EXECUTION_EPSILON = 1e-9
_REVERSAL_REF_KEYS = (
    "corrects_event_id",
    "corrected_event_id",
    "reverses_event_id",
    "reversed_event_id",
    "reversal_of",
    "correction_of",
)
_REVERSAL_STATUSES = {"reversed", "corrected", "cancelled", "canceled", "rejected", "voided"}


def _event_dto(body: Any) -> Dict[str, Any] | None:
    if not isinstance(body, dict):
        return None
    nested = body.get("event")
    return nested if isinstance(nested, dict) else body


def _duplicate_execution_analysis(
    *,
    tenant_id: str,
    event_ids: List[str],
    discovered_at: str,
    deployment_stage: str = "",
) -> Dict[str, Any] | None:
    """Same-decision cross-event duplicate full-execution analysis.

    Resolves tenant-scoped accepted telemetry events and checks that one
    decision (signal/run/binding/runtime/artifact/bar) did not commit two
    distinct broker orders beyond the canonical order-intent requested quantity.
    Returns None when fewer than two events were referenced (nothing to compare).
    Unresolvable or incomparable evidence is reported as measurement
    "unmeasured", never as duplicate detected or measured healthy.
    """
    if len(event_ids) < 2:
        return None
    base: Dict[str, Any] = {
        "check": "duplicate_execution_invariant",
        "telemetry_event_ids": list(event_ids),
        "discovered_at": discovered_at,
    }

    def unmeasured(reason: str, **extra: Any) -> Dict[str, Any]:
        return {**base, "status": "degraded", "measurement": "unmeasured", "reason": reason,
                "detail": f"duplicate execution invariant not measured: {reason}", **extra}

    telemetry_url = os.getenv("PANTHEON_TELEMETRY_API_URL", "").rstrip("/")
    if not telemetry_url:
        return unmeasured("telemetry_api_not_configured")
    from telemetry_client import fetch_accepted_event

    events: Dict[str, Dict[str, Any]] = {}
    unresolved: List[str] = []
    for event_id in event_ids:
        try:
            dto = _event_dto(
                fetch_accepted_event(telemetry_url, event_id, tenant_id=tenant_id, timeout_seconds=5.0)
            )
        except Exception:  # noqa: BLE001 - uncertain evidence is unmeasured
            # Transient acquisition failure: retryable, never persisted as complete.
            return unmeasured("telemetry_fetch_failed", failed_event_id=event_id, retryable=True)
        if dto is None or str(dto.get("tenant_id") or "").strip() != tenant_id:
            unresolved.append(event_id)
            continue
        events[event_id] = dto
    if unresolved:
        return unmeasured("accepted_event_unresolved", unresolved_event_ids=unresolved)

    def meta(dto: Dict[str, Any]) -> Dict[str, Any]:
        value = dto.get("metadata")
        return value if isinstance(value, dict) else {}

    def metrics(dto: Dict[str, Any]) -> Dict[str, Any]:
        value = dto.get("metrics")
        return value if isinstance(value, dict) else {}

    reversed_ids: set[str] = set()
    for dto in events.values():
        for key in _REVERSAL_REF_KEYS:
            ref = meta(dto).get(key) or dto.get(key)
            if isinstance(ref, str) and ref.strip():
                reversed_ids.add(ref.strip())

    groups: Dict[tuple, Dict[str, Any]] = {}
    for event_id, dto in events.items():
        m, mt = meta(dto), metrics(dto)
        broker_order_id = str(m.get("broker_order_id") or dto.get("order_id") or "").strip()
        status = str(m.get("broker_submission_status") or "").strip().lower()
        is_reversal = (
            event_id in reversed_ids
            or any(meta(dto).get(k) or dto.get(k) for k in _REVERSAL_REF_KEYS)
            or status in _REVERSAL_STATUSES
        )
        if not is_reversal and mt.get("fill_quantity") is not None and (not broker_order_id or not status):
            # Fill evidence without order identity or status is ambiguous, not "no duplicate".
            return unmeasured("ambiguous_fill_evidence", event_id=event_id)
        if (
            not broker_order_id
            or event_id in reversed_ids
            or any(meta(dto).get(k) or dto.get(k) for k in _REVERSAL_REF_KEYS)
            or status in _REVERSAL_STATUSES
            or status not in {"filled", "partially_filled", "partial_fill", "partial"}
            or mt.get("submitted_to_broker") is False
            or m.get("ledger_committed") is False
        ):
            continue
        # Owner evidence for an accepted fill must be explicit; absence is not "no duplicate".
        if mt.get("submitted_to_broker") is not True or m.get("ledger_committed") is not True:
            return unmeasured("incomplete_owner_evidence", event_id=event_id)
        fill = _numeric(mt.get("fill_quantity"))
        requested = _numeric(m.get("requested_quantity"))
        identity = {
            "binding_id": dto.get("binding_id"),
            "runtime_id": dto.get("runtime_id"),
            "artifact_id": dto.get("artifact_id"),
            "artifact_version": dto.get("artifact_version"),
            "signal_id": dto.get("signal_id"),
            "run_id": dto.get("run_id"),
            "plan_id": dto.get("plan_id"),
            "capital_pool_id": dto.get("capital_pool_id"),
            "symbol": dto.get("symbol"),
            "action": mt.get("action"),
            "bar": m.get("bar_id") or m.get("bar_time") or m.get("market_price_as_of"),
        }
        missing = [k for k, v in identity.items() if not str(v or "").strip()]
        if missing:
            return unmeasured("incomplete_decision_identity", event_id=event_id, missing_fields=missing)
        checksum = str(
            m.get("plan_checksum") or dto.get("plan_checksum")
            or m.get("deployment_plan_checksum") or dto.get("deployment_plan_checksum") or ""
        ).strip()
        decision = (
            tenant_id,
            str(identity["binding_id"]).strip(),
            str(identity["runtime_id"]).strip(),
            str(identity["artifact_id"]).strip(),
            str(identity["artifact_version"]).strip(),
            str(identity["signal_id"]).strip(),
            str(identity["run_id"]).strip(),
            str(identity["symbol"]).strip(),
            str(identity["action"]).strip().upper(),
            str(identity["bar"]).strip(),
            str(identity["plan_id"]).strip(),
            checksum,
            str(identity["capital_pool_id"]).strip(),
        )
        if fill is None or requested is None or requested <= 0:
            return unmeasured("incomparable_execution_evidence", event_id=event_id)
        group = groups.setdefault(decision, {"orders": {}, "requested": set(), "events": []})
        group["requested"].add(requested)
        group["events"].append(event_id)
        fills = group["orders"].setdefault(broker_order_id, {})
        # Replay of one fill (same fill id or same event) is not a second fill.
        canonical_fill_id = str(dto.get("fill_id") or m.get("fill_id") or "").strip()
        if not canonical_fill_id and fills:
            # Several events for one order without a canonical fill id cannot be
            # told apart as replay versus incremental partial fill: unmeasured.
            return unmeasured("ambiguous_fill_evidence", event_id=event_id, broker_order_id=broker_order_id)
        if canonical_fill_id and any(k.startswith("event:") for k in fills):
            return unmeasured("ambiguous_fill_evidence", event_id=event_id, broker_order_id=broker_order_id)
        fills[canonical_fill_id or f"event:{event_id}"] = abs(fill)

    groups = {key: value for key, value in groups.items() if value["orders"]}
    if not groups:
        return unmeasured("fewer_than_two_comparable_execution_events")
    duplicates = []
    for decision, group in groups.items():
        if len(group["requested"]) != 1:
            return unmeasured("incomparable_requested_quantity", event_ids=group["events"])
        requested = next(iter(group["requested"]))
        orders = {oid: sum(fills.values()) for oid, fills in group["orders"].items()}
        total = sum(orders.values())
        if len(orders) >= 2 and total > requested + _EXECUTION_EPSILON:
            duplicates.append((decision, requested, orders, group["events"]))
    if not duplicates:
        return {**base, "status": "ok", "measurement": "measured", "duplicate_detected": False,
                "detail": "no distinct broker orders beyond canonical requested quantity"}
    decision, requested, orders, group_events = duplicates[0]
    created = sorted(str(events[e].get("created_at") or "") for e in group_events)
    ordered = sorted(group_events, key=lambda e: (str(events[e].get("created_at") or ""), e))
    linked = events[ordered[-1]]
    handoff = {
        "deployment_stage": str(linked.get("deployment_stage") or deployment_stage or "").strip(),
        "persona_capital_binding_id": str(
            linked.get("persona_capital_binding_id") or meta(linked).get("persona_capital_binding_id") or ""
        ).strip(),
        "trace_id": str(linked.get("trace_id") or meta(linked).get("trace_id") or "").strip(),
    }
    missing_handoff = [k for k, v in handoff.items() if not v]
    if missing_handoff:
        return unmeasured(
            "incomplete_handoff_identity",
            missing_fields=missing_handoff,
            duplicate_event_ids=[e for e in event_ids if e in group_events],
        )
    return {
        **base,
        "status": "critical",
        "measurement": "measured",
        "duplicate_detected": True,
        "detail": "distinct broker orders committed beyond canonical requested quantity for one decision",
        "decision": {
            "tenant_id": decision[0], "binding_id": decision[1], "runtime_id": decision[2],
            "artifact_id": decision[3], "artifact_version": decision[4], "signal_id": decision[5],
            "run_id": decision[6], "symbol": decision[7], "action": decision[8], "bar": decision[9],
            "plan_id": decision[10], "plan_checksum": decision[11], "capital_pool_id": decision[12],
        },
        "plan_id": decision[10],
        "capital_pool_id": decision[12],
        "requested_quantity": requested,
        "distinct_broker_order_ids": sorted(orders),
        "committed_quantity": sum(orders.values()),
        "duplicate_event_ids": [e for e in event_ids if e in group_events],
        "linked_telemetry_event_id": ordered[-1],
        "handoff": handoff,
        "occurrence_window": {"first_created_at": created[0], "last_created_at": created[-1]},
    }


def _duplicate_execution_report(
    *,
    analysis: Dict[str, Any],
    evaluation_id: str,
    incident_id: str,
) -> Dict[str, Any]:
    decision = analysis["decision"]
    ids = analysis["duplicate_event_ids"]
    digest = hashlib.sha256(
        json.dumps([decision, analysis["distinct_broker_order_ids"]], sort_keys=True).encode()
    ).hexdigest()[:16]
    report_id = f"drift-duplicate-execution-{digest}"
    return {
        "id": report_id,
        "drift_report_id": report_id,
        "recon_run_id": evaluation_id,
        "evaluation_id": evaluation_id,
        "tenant_id": decision["tenant_id"],
        "drift_type": "execution",
        "incident_cluster_id": "drift:duplicate_execution",
        "scope_ref": decision["binding_id"],
        "binding_id": decision["binding_id"],
        "runtime_id": decision["runtime_id"],
        "artifact_id": decision["artifact_id"],
        "artifact_version": decision["artifact_version"],
        "signal_id": decision["signal_id"],
        "run_id": decision["run_id"],
        "plan_id": analysis.get("plan_id"),
        "deployment_plan_id": analysis.get("plan_id"),
        "capital_pool_id": analysis.get("capital_pool_id"),
        **analysis["handoff"],
        # The existing incident consumer links exactly one telemetry event; the
        # other duplicate event stays preserved in the fields below.
        "telemetry_event_id": analysis["linked_telemetry_event_id"],
        "telemetry_event_ids": [analysis["linked_telemetry_event_id"]],
        "duplicate_event_ids": ids,
        "handoff_state": "pending",
        "predecessor_incident_id": incident_id or None,
        "severity": "critical",
        "status": "open",
        "recommended_action": "open_incident",
        "metrics": {"duplicate_execution": analysis},
        "evidence_refs": [
            f"telemetry_event:{analysis['linked_telemetry_event_id']}",
            *[f"duplicate_execution_event:{event_id}" for event_id in ids],
            *([f"incident:{incident_id}"] if incident_id else []),
            f"runtime_binding:{decision['binding_id']}",
            f"drift_evaluation:{evaluation_id}",
            f"drift_report:{report_id}",
        ],
        "occurrence_window": analysis["occurrence_window"],
        "generated_at": analysis["discovered_at"],
        "source_contract": {
            "telemetry_truth_owner": "telemetry-ingest",
            "incident_truth_owner": "incidents",
            "runtime_truth_owner": "runtime-manager",
            "derived_only": True,
            "emergency_control_chain_affected": False,
        },
    }


@app.post("/api/reconciliation-drift/incident-triggers/consume", status_code=201)
def consume_incident_trigger(body: IncidentTriggerBody) -> Dict[str, Any]:
    """Consume an incident/anomaly trigger and immediately create a reconciliation evaluation.

    Idempotent: duplicate incident or source event payloads produce the same
    evaluation_id and are returned as skipped instead of creating duplicates.
    """
    tenant_id = _authorized_body_tenant(body.tenant_id)
    timestamp = body.generated_at or utc_now()
    incident = body.incident or {}
    anomaly_event = body.anomaly_event or body.event or {}
    require_explicit_tenant = _auth_mode() == "token"
    if incident:
        _authorized_payload_tenant(
            incident,
            require_explicit=require_explicit_tenant,
        )
    if anomaly_event:
        _authorized_payload_tenant(
            anomaly_event,
            require_explicit=require_explicit_tenant,
        )
    binding_id = str(
        body.binding_id
        or _first_trigger_value(
            incident,
            anomaly_event,
            keys=("binding_id", "runtime_binding_id", "scope_ref"),
        )
        or ""
    ).strip()
    runtime_id = str(
        body.runtime_id
        or _first_trigger_value(incident, anomaly_event, keys=("runtime_id", "runtime_ref"))
        or ""
    ).strip()
    if not binding_id:
        raise HTTPException(status_code=422, detail="incident trigger requires binding_id or runtime_binding_id")

    incident_id = str(
        body.incident_id
        or _first_trigger_value(incident, anomaly_event, keys=("incident_id", "id"))
        or ""
    ).strip()
    telemetry_event_ids = _trigger_telemetry_event_ids(body)
    source_event_id = str(
        body.source_event_id
        or _first_trigger_value(anomaly_event, incident, keys=("event_id", "telemetry_event_id"))
        or (telemetry_event_ids[0] if telemetry_event_ids else "")
    ).strip()
    trigger_id = incident_id or source_event_id or f"{binding_id}:{timestamp}"
    evaluation_id = _trigger_evaluation_id(trigger_id, binding_id)

    evidence_summary = str(incident.get("evidence_summary") or anomaly_event.get("evidence_summary") or "")
    is_infra = binding_id.startswith("infra-subject-") or "non_trading_infrastructure_incident=true" in evidence_summary
    existing = None if is_infra else store.get_evaluation(evaluation_id, tenant_id=tenant_id)
    if existing is not None and any(
        isinstance(check, dict)
        and check.get("check") == "duplicate_execution_invariant"
        and check.get("reason") == "accepted_event_unresolved"
        for check in existing.get("reconciliation_checks") or []
    ):
        # Evidence was not yet accepted when first evaluated: replay re-measures.
        existing = None
    if existing is not None and any(
        (stored_report := store.get_drift_report(str(report_id), tenant_id=tenant_id)) is not None
        and stored_report.get("handoff_state") != "delivered"
        for report_id in existing.get("drift_report_ids") or []
    ):
        # Owner handoff never completed (e.g. Incidents not configured): replay retries it.
        existing = None
    if is_infra or existing is not None:
        return {
            "status": "ok",
            "trigger": "incident",
            "created": False,
            "skipped": True,
            "not_applicable": is_infra,
            "evaluation_id": evaluation_id,
            "tenant_id": tenant_id,
            "binding_id": binding_id,
            "runtime_id": runtime_id or None,
            "incident_id": incident_id or None,
            "source_event_id": source_event_id or None,
            "reason": "non_trading_infrastructure_incident" if is_infra else (existing.get("trigger_reason") or _trigger_reason(body)),
            "triggered_at": timestamp,
        }

    reason = _trigger_reason(body)
    trigger_status = _trigger_status(
        _first_trigger_value({"severity": anomaly_event.get("severity")}, incident, keys=("severity",)),
        reason,
    )
    raw_observed_metrics = body.observed_metrics or anomaly_event.get("observed_metrics") or anomaly_event.get("metrics") or {}
    raw_baseline_metrics = body.baseline_metrics or anomaly_event.get("baseline_metrics") or {}
    thresholds = body.thresholds or anomaly_event.get("thresholds") or {}
    if not isinstance(raw_observed_metrics, dict):
        raw_observed_metrics = {}
    if not isinstance(raw_baseline_metrics, dict):
        raw_baseline_metrics = {}
    if not isinstance(thresholds, dict):
        thresholds = {}
    observed_metrics = {
        str(key): number
        for key, value in raw_observed_metrics.items()
        if (number := _numeric(value)) is not None
    }
    baseline_metrics = raw_baseline_metrics
    drift_checks = _drift_checks(baseline_metrics, observed_metrics, thresholds)
    duplicate_analysis = _duplicate_execution_analysis(
        tenant_id=tenant_id,
        event_ids=telemetry_event_ids,
        discovered_at=timestamp,
        deployment_stage=str(
            body.deployment_stage
            or _first_trigger_value(incident, anomaly_event, keys=("deployment_stage", "deployment_mode"))
            or ""
        ).strip(),
    )
    if duplicate_analysis and duplicate_analysis.get("retryable"):
        # Nothing durable is written: a retry after recovery re-acquires evidence.
        raise HTTPException(
            status_code=503,
            detail=f"duplicate execution evidence not yet available: {duplicate_analysis['reason']}",
        )
    status = _worst_status(
        [
            trigger_status,
            *[check["status"] for check in drift_checks],
            *([duplicate_analysis["status"]] if duplicate_analysis else []),
        ]
    )
    evidence_refs = [
        {"type": "runtime_binding", "id": binding_id},
        *([{"type": "runtime_session", "id": runtime_id}] if runtime_id else []),
        *([{"type": "incident", "id": incident_id}] if incident_id else []),
        *[{"type": "telemetry_event", "id": event_id} for event_id in telemetry_event_ids],
    ]
    evaluation = {
        "id": evaluation_id,
        "evaluation_id": evaluation_id,
        "tenant_id": tenant_id,
        "binding_id": binding_id,
        "runtime_id": runtime_id or None,
        "status": status,
        "summary": {
            "status": status,
            "telemetry_event_count": len(telemetry_event_ids),
            "baseline_metric_count": len(baseline_metrics),
            "observed_metric_count": len(observed_metrics),
            "drift_check_count": len(drift_checks),
            "reconciliation_check_count": 1,
        },
        "baseline_metrics": baseline_metrics,
        "observed_metrics": observed_metrics,
        "drift_checks": drift_checks,
        "reconciliation_checks": [
            {
                "check": "incident_trigger_received",
                "status": trigger_status,
                "detail": "incident/anomaly listener triggered reconciliation",
                "binding_id": binding_id,
                "runtime_id": runtime_id or None,
                "incident_id": incident_id or None,
                "source_event_id": source_event_id or None,
                "reason": reason,
                "telemetry_event_ids": telemetry_event_ids,
            },
            *([duplicate_analysis] if duplicate_analysis else []),
        ],
        "evidence_refs": evidence_refs,
        "trigger": "incident",
        "trigger_id": trigger_id,
        "trigger_reason": reason,
        "incident_id": incident_id or None,
        "source_event_id": source_event_id or None,
        "telemetry_event_ids": telemetry_event_ids,
        "deployment_stage": body.deployment_stage
        or _first_trigger_value(incident, anomaly_event, keys=("deployment_stage", "deployment_mode")),
        "source_contract": {
            "incident_truth_owner": "incidents",
            "telemetry_truth_owner": "telemetry-ingest",
            "lineage_truth_owner": "lineage-read",
            "runtime_truth_owner": "runtime-manager",
            "derived_only": True,
            "emergency_control_chain_affected": False,
        },
        "evaluated_at": timestamp,
    }
    if duplicate_analysis and duplicate_analysis.get("duplicate_detected"):
        # Idempotent derived owner output keyed by the decision; written before
        # the evaluation so a retry after a partial failure converges.
        report = _duplicate_execution_report(
            analysis=duplicate_analysis,
            evaluation_id=evaluation_id,
            incident_id=incident_id,
        )
        existing_report = store.get_drift_report(report["drift_report_id"], tenant_id=tenant_id)
        stored_report = existing_report or store.put_drift_report(report)
        # Deliver through the existing incidents owner path. A failure raises
        # before the evaluation is stored, so the retry re-enters here, finds
        # the stored report, and redelivers (incident ids are deterministic).
        delivered = _classify_drift_report_incident(stored_report)
        stored_report["handoff_state"] = "delivered" if delivered is not None else "not_configured"
        if delivered is not None:
            stored_report["incident_id"] = delivered.get("incident_id") or delivered.get("id")
        store.put_drift_report(stored_report)
        evaluation["drift_report_ids"] = [stored_report["drift_report_id"]]
        evaluation["evidence_refs"].append(
            {"type": "drift_report", "id": stored_report["drift_report_id"]}
        )
    stored = store.put_evaluation(evaluation)
    return {
        "status": "ok",
        "trigger": "incident",
        "created": True,
        "skipped": False,
        "evaluation_id": stored["evaluation_id"],
        "tenant_id": tenant_id,
        "binding_id": binding_id,
        "runtime_id": runtime_id or None,
        "incident_id": incident_id or None,
        "source_event_id": source_event_id or None,
        "reason": reason,
        "triggered_at": timestamp,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8102")))
