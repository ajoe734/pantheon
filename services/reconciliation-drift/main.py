from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from consumer import build_drift_report_from_event
from services.foundation.health import register_fastapi_health_routes
from services.foundation.persistence_posture import require_persistence_posture
from store import ReconciliationDriftStore, build_reconciliation_drift_store


DEFAULT_WARNING_RELATIVE_DELTA = 0.2
DEFAULT_CRITICAL_RELATIVE_DELTA = 0.5
RECONCILIATION_ERROR_SEVERITIES = {"critical", "error"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _data_dir() -> str:
    return os.getenv("RECONCILIATION_DRIFT_DATA_DIR", "/tmp/pantheon/reconciliation-drift")


def _next_id(prefix: str, timestamp: str, existing: set[str]) -> str:
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


def _drift_checks(
    baseline_metrics: Dict[str, Any],
    observed_metrics: Dict[str, float],
    thresholds: Dict[str, Any],
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
        relative_delta = abs(observed - baseline) / denominator
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


def _post_json(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - service URL is operator configured.
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"incident service rejected request: {detail}") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"incident service unavailable: {exc.reason}") from exc


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
STORE_BACKEND = os.getenv("RECONCILIATION_DRIFT_STORE_BACKEND", "json").strip().lower() or "json"
PERSISTENCE_POSTURE = require_persistence_posture("reconciliation-drift")
store = build_reconciliation_drift_store(DATA_DIR)
app = FastAPI(title="Pantheon Reconciliation Drift Service", version="0.1.0")
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
        "evaluation_count": len(store.list_evaluations()),
        "alert_count": len(store.list_alert_handoffs()),
        "reconciliation_record_count": len(store.list_reconciliation_records()),
        "drift_report_count": len(store.list_drift_reports()),
    },
    details=lambda: {
        "data_dir": DATA_DIR,
        "store_backend": STORE_BACKEND,
        "persistence_posture": PERSISTENCE_POSTURE.to_dict(),
    },
)


@app.get("/health")
def health() -> Dict[str, Any]:
    evaluations = store.list_evaluations()
    alerts = store.list_alert_handoffs()
    records = store.list_reconciliation_records()
    return {
        "status": "ok",
        "service": "reconciliation-drift",
        "data_dir": DATA_DIR,
        "evaluation_count": len(evaluations),
        "alert_handoff_count": len(alerts),
        "reconciliation_record_count": len(records),
        "drift_report_count": len(store.list_drift_reports()),
        "telemetry_api_url": os.getenv("PANTHEON_TELEMETRY_API_URL", ""),
        "lineage_read_url": os.getenv("PANTHEON_LINEAGE_READ_URL", ""),
        "runtime_manager_url": os.getenv("PANTHEON_RUNTIME_MANAGER_URL", ""),
        "emergency_control_chain_member": False,
    }


@app.post("/api/reconciliation-drift/evaluations", status_code=201)
def create_evaluation(body: EvaluationBody) -> Dict[str, Any]:
    timestamp = body.evaluated_at or utc_now()
    evaluation_id = body.evaluation_id or _next_id(
        "rdeval",
        timestamp,
        {str(item.get("evaluation_id") or "") for item in store.list_evaluations()},
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
    events = list(body.events)
    if body.event:
        events.insert(0, body.event)

    existing_report_ids = {str(item.get("drift_report_id") or "") for item in store.list_drift_reports()}
    drift_reports: List[Dict[str, Any]] = []
    ignored_event_ids: List[str] = []
    for event in events:
        report = build_drift_report_from_event(
            event,
            baseline_metrics=body.baseline_metrics,
            thresholds=body.thresholds,
            generated_at=body.generated_at,
            existing_report_ids=existing_report_ids,
        )
        if report is None:
            ignored_event_ids.append(str(event.get("event_id") or event.get("id") or "<unknown>"))
            continue
        stored = store.put_drift_report(report)
        existing_report_ids.add(str(stored.get("drift_report_id") or ""))
        drift_reports.append(stored)

    return {
        "status": "ok",
        "consumed_event_count": len(events),
        "drift_report_count": len(drift_reports),
        "drift_reports": drift_reports,
        "ignored_event_ids": ignored_event_ids,
        "source_contract": {
            "telemetry_truth_owner": "telemetry-ingest",
            "derived_only": True,
            "emergency_control_chain_affected": False,
        },
    }


@app.post("/api/reconciliation-drift/paper-runs/reconcile", status_code=201)
def create_paper_reconciliation_record(body: PaperRunReconciliationBody) -> Dict[str, Any]:
    timestamp = body.generated_at or utc_now()
    record_id = body.record_id or _next_id(
        "recon",
        timestamp,
        {str(item.get("record_id") or "") for item in store.list_reconciliation_records()},
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
    timestamp = body.generated_at or utc_now()
    record_id = body.record_id or _next_id(
        "recon",
        timestamp,
        {str(item.get("record_id") or "") for item in store.list_reconciliation_records()},
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
    timestamp = body.generated_at or utc_now()
    record_id = body.record_id or _next_id(
        "recon",
        timestamp,
        {str(item.get("record_id") or "") for item in store.list_reconciliation_records()},
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
    evaluations = store.list_evaluations()
    if binding_id:
        evaluations = [item for item in evaluations if item.get("binding_id") == binding_id]
    return evaluations


@app.get("/api/reconciliation-drift/evaluations/{evaluation_id}")
def get_evaluation(evaluation_id: str) -> Dict[str, Any]:
    evaluation = store.get_evaluation(evaluation_id)
    if not evaluation:
        raise HTTPException(status_code=404, detail="drift evaluation not found")
    return evaluation


@app.get("/api/reconciliation-drift/reconciliation-records")
def list_reconciliation_records(binding_id: Optional[str] = Query(default=None)) -> List[Dict[str, Any]]:
    records = store.list_reconciliation_records()
    if binding_id:
        records = [item for item in records if item.get("binding_id") == binding_id]
    return records


@app.get("/api/reconciliation-drift/reconciliation-records/{record_id}")
def get_reconciliation_record(record_id: str) -> Dict[str, Any]:
    record = store.get_reconciliation_record(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="reconciliation record not found")
    return record


@app.get("/api/reconciliation-drift/drift-reports")
def list_drift_reports(
    scope_ref: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
) -> List[Dict[str, Any]]:
    reports = store.list_drift_reports()
    if scope_ref:
        reports = [item for item in reports if item.get("scope_ref") == scope_ref]
    if status:
        reports = [item for item in reports if item.get("status") == status]
    return reports


@app.get("/api/reconciliation-drift/drift-reports/{report_id}")
def get_drift_report(report_id: str) -> Dict[str, Any]:
    report = store.get_drift_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="drift report not found")
    return report


@app.get("/api/reconciliation-drift/summary")
def summary(binding_id: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    evaluations = list_evaluations(binding_id=binding_id)
    alerts = list_alert_handoffs(binding_id=binding_id, handoff_state=None)
    drift_reports = store.list_drift_reports()
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
    alerts = store.list_alert_handoffs()
    if binding_id:
        alerts = [item for item in alerts if item.get("binding_id") == binding_id]
    if handoff_state:
        alerts = [item for item in alerts if item.get("handoff_state") == handoff_state]
    return alerts


@app.post("/api/reconciliation-drift/alerts/{alert_id}/handoff")
def mark_alert_handoff(alert_id: str, body: HandoffBody) -> Dict[str, Any]:
    alert = store.get_alert_handoff(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="alert handoff not found")
    updated = dict(alert)
    updated["handoff_state"] = body.handoff_state
    updated["target_service"] = body.target_service or updated.get("target_service")
    updated["handoff_actor"] = body.actor_id
    updated["handoff_note"] = body.note
    updated["handed_off_at"] = utc_now()
    return store.put_alert_handoff(updated)


class ScheduledReconcileBody(BaseModel):
    tick_id: Optional[str] = None


class IncidentTriggerBody(BaseModel):
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


def _fetch_telemetry_runtime_summaries(telemetry_url: str) -> List[Dict[str, Any]]:
    if not telemetry_url:
        return []
    url = telemetry_url.rstrip("/") + "/api/telemetry/runtime-summaries"
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            body = response.read().decode("utf-8")
        payload = json.loads(body) if body else []
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            return payload.get("summaries") or payload.get("items") or []
        return []
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return []


def _tick_evaluation_id(tick_id: str, binding_id: str) -> str:
    safe_tick = tick_id.replace(":", "-").replace("+", "-").replace(" ", "-")[:32]
    safe_binding = binding_id.replace("/", "-").replace(":", "-")[:24]
    return f"rdeval-sched-{safe_tick}-{safe_binding}"


def _trigger_evaluation_id(trigger_id: str, binding_id: str) -> str:
    safe_trigger = _safe_id_component(trigger_id, fallback="trigger", limit=48)
    safe_binding = _safe_id_component(binding_id, fallback="binding", limit=24)
    return f"rdeval-incident-{safe_trigger}-{safe_binding}"


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


@app.post("/api/reconciliation-drift/scheduled-reconcile", status_code=201)
def scheduled_reconcile(body: ScheduledReconcileBody) -> Dict[str, Any]:
    """Run a scheduled reconciliation pass over all active bindings visible in telemetry.

    Idempotent: a second call with the same tick_id skips bindings that already
    have an evaluation record for that tick, so duplicate scheduler ticks do not
    create duplicate ReconciliationRecords.
    """
    timestamp = utc_now()
    tick_id = body.tick_id or timestamp

    telemetry_url = os.getenv("PANTHEON_TELEMETRY_API_URL", "").rstrip("/")
    summaries: List[Dict[str, Any]] = _fetch_telemetry_runtime_summaries(telemetry_url)

    existing_evaluation_ids = {str(item.get("evaluation_id") or "") for item in store.list_evaluations()}

    created_evaluation_ids: List[str] = []
    skipped_binding_ids: List[str] = []

    for summary in summaries:
        binding_id = str(
            summary.get("binding_id") or summary.get("runtime_binding_id") or summary.get("id") or ""
        ).strip()
        runtime_id = str(summary.get("runtime_id") or "").strip()
        if not binding_id:
            continue

        evaluation_id = _tick_evaluation_id(tick_id, binding_id)
        if evaluation_id in existing_evaluation_ids:
            skipped_binding_ids.append(binding_id)
            continue

        # Normalize telemetry event IDs from the real telemetry runtime-summary
        # contract.  The projection exposes last_event_id and last_heartbeat_event_id
        # rather than a telemetry_event_ids list; accept both forms so the scheduled
        # reconciler links real event evidence regardless of the source shape.
        raw_event_ids: list[Any] = list(summary.get("telemetry_event_ids") or [])
        if not raw_event_ids:
            seen_eids: set[str] = set()
            for _eid in (summary.get("last_event_id"), summary.get("last_heartbeat_event_id")):
                if _eid:
                    _eid_str = str(_eid)
                    if _eid_str not in seen_eids:
                        raw_event_ids.append(_eid_str)
                        seen_eids.add(_eid_str)
        telemetry_event_ids = [str(eid) for eid in raw_event_ids if eid]
        observed_metrics: Dict[str, Any] = summary.get("observed_metrics") or summary.get("metrics") or {}
        baseline_metrics: Dict[str, Any] = summary.get("baseline_metrics") or {}

        evaluation = {
            "id": evaluation_id,
            "evaluation_id": evaluation_id,
            "binding_id": binding_id,
            "runtime_id": runtime_id or None,
            "status": "ok",
            "summary": {
                "status": "ok",
                "telemetry_event_count": len(telemetry_event_ids),
                "baseline_metric_count": len(baseline_metrics),
                "observed_metric_count": len(observed_metrics),
                "drift_check_count": 0,
                "reconciliation_check_count": 1,
            },
            "baseline_metrics": baseline_metrics,
            "observed_metrics": observed_metrics,
            "drift_checks": [],
            "reconciliation_checks": [
                {
                    "check": "telemetry_runtime_alignment",
                    "status": "ok",
                    "detail": "scheduled reconciliation pass; binding and runtime identifiers linked",
                    "binding_id": binding_id,
                    "runtime_id": runtime_id or None,
                    "telemetry_event_ids": telemetry_event_ids,
                }
            ],
            "evidence_refs": [
                {"type": "tick", "id": tick_id},
                {"type": "runtime_binding", "id": binding_id},
                *([{"type": "runtime_session", "id": runtime_id}] if runtime_id else []),
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

    return {
        "status": "ok",
        "tick_id": tick_id,
        "trigger": "scheduled",
        "evaluated_binding_count": len(created_evaluation_ids),
        "skipped_binding_count": len(skipped_binding_ids),
        "evaluation_ids": created_evaluation_ids,
        "skipped_binding_ids": skipped_binding_ids,
        "telemetry_summaries_fetched": len(summaries),
        "triggered_at": timestamp,
    }


@app.post("/api/reconciliation-drift/incident-triggers/consume", status_code=201)
def consume_incident_trigger(body: IncidentTriggerBody) -> Dict[str, Any]:
    """Consume an incident/anomaly trigger and immediately create a reconciliation evaluation.

    Idempotent: duplicate incident or source event payloads produce the same
    evaluation_id and are returned as skipped instead of creating duplicates.
    """
    timestamp = body.generated_at or utc_now()
    incident = body.incident or {}
    anomaly_event = body.anomaly_event or body.event or {}
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

    existing = store.get_evaluation(evaluation_id)
    if existing is not None:
        return {
            "status": "ok",
            "trigger": "incident",
            "created": False,
            "skipped": True,
            "evaluation_id": evaluation_id,
            "binding_id": binding_id,
            "runtime_id": runtime_id or None,
            "incident_id": incident_id or None,
            "source_event_id": source_event_id or None,
            "reason": existing.get("trigger_reason") or _trigger_reason(body),
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
    status = _worst_status([trigger_status, *[check["status"] for check in drift_checks]])
    evidence_refs = [
        {"type": "runtime_binding", "id": binding_id},
        *([{"type": "runtime_session", "id": runtime_id}] if runtime_id else []),
        *([{"type": "incident", "id": incident_id}] if incident_id else []),
        *[{"type": "telemetry_event", "id": event_id} for event_id in telemetry_event_ids],
    ]
    evaluation = {
        "id": evaluation_id,
        "evaluation_id": evaluation_id,
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
            }
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
    stored = store.put_evaluation(evaluation)
    return {
        "status": "ok",
        "trigger": "incident",
        "created": True,
        "skipped": False,
        "evaluation_id": stored["evaluation_id"],
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
