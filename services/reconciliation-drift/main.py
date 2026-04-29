from __future__ import annotations

import os
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from services.foundation.health import register_fastapi_health_routes
from store import ReconciliationDriftStore


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


def _status_rank(status: str) -> int:
    return {"ok": 0, "degraded": 1, "warning": 2, "critical": 3}.get(status, 0)


def _worst_status(statuses: List[str]) -> str:
    if not statuses:
        return "ok"
    return max(statuses, key=_status_rank)


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


class HandoffBody(BaseModel):
    actor_id: str = "reconciliation-drift-service"
    handoff_state: str = "sent"
    target_service: Optional[str] = None
    note: Optional[str] = None


DATA_DIR = _data_dir()
store = ReconciliationDriftStore(DATA_DIR)
app = FastAPI(title="Pantheon Reconciliation Drift Service", version="0.1.0")
register_fastapi_health_routes(
    app,
    "reconciliation-drift",
    dependencies=lambda: {
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
    },
    details=lambda: {"data_dir": DATA_DIR},
)


@app.get("/health")
def health() -> Dict[str, Any]:
    evaluations = store.list_evaluations()
    alerts = store.list_alert_handoffs()
    return {
        "status": "ok",
        "service": "reconciliation-drift",
        "data_dir": DATA_DIR,
        "evaluation_count": len(evaluations),
        "alert_handoff_count": len(alerts),
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


@app.get("/api/reconciliation-drift/summary")
def summary(binding_id: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    evaluations = list_evaluations(binding_id=binding_id)
    alerts = list_alert_handoffs(binding_id=binding_id, handoff_state=None)
    latest = sorted(evaluations, key=lambda item: str(item.get("evaluated_at") or ""))[-1] if evaluations else None
    return {
        "status": latest.get("status") if latest else "degraded",
        "binding_id": binding_id,
        "latest_evaluation_id": latest.get("evaluation_id") if latest else None,
        "evaluation_count": len(evaluations),
        "alert_handoff_count": len(alerts),
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8102")))
