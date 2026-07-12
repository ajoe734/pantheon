"""TJ-E2E-011 SLO and data-quality incident evaluator.

Pure functions that turn a ``JourneyMaterializer`` read model
(``services/trade_journey/materializer.py``) into the metrics required by
the Trade Journey E2E gap spec section 13 ("SLO、data quality 與告警") and
into alertable ``DataQualityIncident`` records that link back to the exact
journey and its evidence endpoint. This module performs no I/O beyond
reading the bundled SLO target config; it does not call the BFF, a metrics
backend, or an alert transport, so it is safe to run in tests, drills, and
failure-injection harnesses (``services/trade_journey/failure_injection.py``).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.trade_journey.alert_rules import evaluate_aggregate_rules, load_alert_rules
from services.trade_journey.materializer import IDENTIFIER_FIELDS, STAGES, JourneyProjection, TERMINAL_STATUSES


SCHEMA_VERSION = "trade_journey_slo_data_quality.v1"
RUNBOOK_REF = "docs/operations/trade_journey_slo_runbook.md"
DEFAULT_SLO_TARGETS_PATH = Path(__file__).with_name("trade_journey_slo_targets.json")
DEFAULT_STALLED_AFTER_SECONDS = 900

# Canonical identifier a journey is expected to carry once it has reached a
# given stage. Only stages with a dedicated field in ``IDENTIFIER_FIELDS``
# are covered; stages without a canonical correlation id (e.g. governance
# gates) are intentionally excluded.
STAGE_IDENTIFIER_EXPECTATIONS: Mapping[str, str] = {
    "research_rationale": "research_journey_id",
    "strategy_candidate": "strategy_lifecycle_id",
    "signal_generation": "signal_id",
    "trade_decision": "decision_id",
    "risk_evaluation": "risk_decision_id",
    "order_submission": "client_order_id",
    "broker_acknowledgement": "broker_order_id",
    "fill_management": "broker_trade_id",
    "ledger_booking": "ledger_entry_id",
    "reconciliation": "reconciliation_id",
}
assert set(STAGE_IDENTIFIER_EXPECTATIONS.values()) <= set(IDENTIFIER_FIELDS)


@dataclass(frozen=True)
class AlertPath:
    event_type: str
    severity_channel: str = "telemetry.alerts"
    operator_surface: str = "/bff/management/trade-journeys/attention"
    escalation_target: str = "trade-journey-owner"
    runbook: str = RUNBOOK_REF


@dataclass(frozen=True)
class SLOTargets:
    environment: str
    producer_to_read_model_p95_seconds: float
    detail_api_p95_seconds: float
    resolve_p95_seconds: float
    correlation_completeness_min: float
    reconciliation_completeness_min: float
    stalled_after_seconds: int
    schema_version: str = SCHEMA_VERSION
    source: str = DEFAULT_SLO_TARGETS_PATH.as_posix()


@dataclass(frozen=True)
class DataQualityMetrics:
    schema_version: str
    environment: str
    measured_at: str
    total_journeys: int
    materializer_lag_seconds: float | None
    stalled_count: int
    orphan_event_rate: float | None
    missing_identifier_rate: float | None
    identifier_conflict_rate: float | None
    conflicting_terminal_rate: float | None
    correlation_completeness_rate: float | None
    reconciliation_completeness_rate: float | None
    broker_reject_count: int
    broker_reject_rate: float | None
    partial_fill_max_age_seconds: float | None
    reconciliation_mismatch_max_age_seconds: float | None
    late_event_lag_p95_ms: float | None
    sse_disconnect_count: int
    stage_latency_ms: Mapping[str, Mapping[str, Any]]
    detail_api_p95_ms: float | None
    resolve_api_p95_ms: float | None


@dataclass(frozen=True)
class DataQualityIncident:
    code: str
    severity: str
    message: str
    metric_name: str
    observed_value: Any
    target_value: Any
    alert_path: AlertPath
    journey_id: str | None = None
    tenant_id: str | None = None
    environment: str | None = None
    evidence_ref: str | None = None


def journey_evidence_ref(journey_id: str) -> str:
    return f"/bff/management/trade-journeys/{journey_id}/evidence"


def load_slo_targets(
    environment: str,
    *,
    path: Path = DEFAULT_SLO_TARGETS_PATH,
) -> SLOTargets:
    """Load paper/canary SLO targets. ``live`` shares the canary targets."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    targets = payload.get("targets")
    if not isinstance(targets, Mapping):
        raise ValueError("trade_journey_slo_targets.json must contain targets")
    lookup_key = "canary" if environment == "live" else environment
    target = targets.get(lookup_key)
    if not isinstance(target, Mapping):
        raise ValueError(f"unknown SLO target environment: {environment}")
    return SLOTargets(
        environment=environment,
        producer_to_read_model_p95_seconds=float(target["producer_to_read_model_p95_seconds"]),
        detail_api_p95_seconds=float(target["detail_api_p95_seconds"]),
        resolve_p95_seconds=float(target["resolve_p95_seconds"]),
        correlation_completeness_min=float(target["correlation_completeness_min"]),
        reconciliation_completeness_min=float(target["reconciliation_completeness_min"]),
        stalled_after_seconds=int(target["stalled_after_seconds"]),
        source=f"{payload.get('source', path.as_posix())}#targets.{lookup_key}",
    )


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(pct * (len(ordered) - 1))))
    return ordered[idx]


def _is_stalled(snapshot: Mapping[str, Any], *, now: datetime, stalled_after_seconds: int) -> bool:
    if snapshot.get("status") in TERMINAL_STATUSES:
        return False
    updated = _parse_iso(snapshot.get("updated_at"))
    if updated is None:
        return False
    return (now - updated).total_seconds() > stalled_after_seconds


def _missing_identifier(projection: JourneyProjection) -> bool:
    identifiers = projection.snapshot.get("identifiers") or {}
    stages = projection.snapshot.get("stages") or {}
    for stage_name, expected_identifier in STAGE_IDENTIFIER_EXPECTATIONS.items():
        if stage_name in stages and not identifiers.get(expected_identifier):
            return True
    return False


def compute_data_quality_metrics(
    projections: Sequence[JourneyProjection],
    *,
    environment: str,
    source_watermarks: Mapping[str, str],
    now: datetime,
    stalled_after_seconds: int = DEFAULT_STALLED_AFTER_SECONDS,
    sse_disconnect_count: int = 0,
    detail_api_latencies_ms: Sequence[float] = (),
    resolve_api_latencies_ms: Sequence[float] = (),
) -> DataQualityMetrics:
    """Derive TJ-E2E-011 data-quality metrics from a materializer snapshot.

    ``source_watermarks`` and ``now`` are supplied by the caller (rather than
    read from the wall clock) so the evaluation is deterministic in tests and
    failure-injection drills. ``detail_api_latencies_ms``/
    ``resolve_api_latencies_ms`` are likewise caller-supplied (the BFF's
    ``ApiLatencyRecorder`` in production; a fixed list in tests) rather than
    measured here, so this function stays a pure, deterministic evaluator —
    gap-spec section 13 requires the ``detail_api_p95_seconds``/
    ``resolve_p95_seconds`` targets to be measured against real latency, not
    just loaded.
    """
    total = len(projections)
    orphan = 0
    identifier_conflict = 0
    missing_identifier = 0
    conflicting_terminal = 0
    reconciled_complete = 0
    stalled_count = 0
    broker_reject = 0
    reached_ack = 0
    partial_fill_ages: list[float] = []
    mismatch_ages: list[float] = []
    late_event_lags_ms: list[float] = []
    stage_latencies_ms: dict[str, list[float]] = {stage: [] for stage in STAGES}

    for projection in projections:
        snapshot = projection.snapshot
        codes = {item.get("code") for item in projection.diagnostics}
        if "orphan_identifier" in codes:
            orphan += 1
        if "identifier_conflict" in codes:
            identifier_conflict += 1
        if "conflicting_terminal_states" in codes:
            conflicting_terminal += 1
        if _missing_identifier(projection):
            missing_identifier += 1
        if _is_stalled(snapshot, now=now, stalled_after_seconds=stalled_after_seconds):
            stalled_count += 1

        stages = snapshot.get("stages") or {}
        if "order_submission" in stages:
            if "reconciliation" in stages:
                reconciled_complete += 1
            if "broker_acknowledgement" in stages:
                reached_ack += 1
                if stages["broker_acknowledgement"].get("status") == "rejected":
                    broker_reject += 1

        updated = _parse_iso(snapshot.get("updated_at"))
        age_seconds = (now - updated).total_seconds() if updated else None
        if snapshot.get("status") == "partially_filled" and age_seconds is not None:
            partial_fill_ages.append(age_seconds)
        if snapshot.get("status") == "completed_with_variance" and age_seconds is not None:
            mismatch_ages.append(age_seconds)

        for event in projection.timeline:
            occurred = _parse_iso(event.get("occurred_at"))
            recorded = _parse_iso(event.get("recorded_at"))
            if occurred and recorded:
                lag_ms = (recorded - occurred).total_seconds() * 1000.0
                late_event_lags_ms.append(lag_ms)
                stage_name = event.get("stage")
                if stage_name in stage_latencies_ms:
                    stage_latencies_ms[stage_name].append(lag_ms)

    executed = sum(1 for p in projections if "order_submission" in (p.snapshot.get("stages") or {}))

    materializer_lag_seconds: float | None = None
    parsed_watermarks = [dt for dt in (_parse_iso(value) for value in source_watermarks.values()) if dt is not None]
    if parsed_watermarks:
        oldest = min(parsed_watermarks)
        materializer_lag_seconds = max(0.0, (now - oldest).total_seconds())

    stage_latency_ms = {
        stage: {"p50_ms": _percentile(values, 0.5), "p95_ms": _percentile(values, 0.95), "sample_count": len(values)}
        for stage, values in stage_latencies_ms.items()
        if values
    }

    return DataQualityMetrics(
        schema_version=SCHEMA_VERSION,
        environment=environment,
        measured_at=now.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        total_journeys=total,
        materializer_lag_seconds=materializer_lag_seconds,
        stalled_count=stalled_count,
        orphan_event_rate=(orphan / total) if total else None,
        missing_identifier_rate=(missing_identifier / total) if total else None,
        identifier_conflict_rate=(identifier_conflict / total) if total else None,
        conflicting_terminal_rate=(conflicting_terminal / total) if total else None,
        correlation_completeness_rate=(1 - missing_identifier / total) if total else None,
        reconciliation_completeness_rate=(reconciled_complete / executed) if executed else None,
        broker_reject_count=broker_reject,
        broker_reject_rate=(broker_reject / reached_ack) if reached_ack else None,
        partial_fill_max_age_seconds=max(partial_fill_ages) if partial_fill_ages else None,
        reconciliation_mismatch_max_age_seconds=max(mismatch_ages) if mismatch_ages else None,
        late_event_lag_p95_ms=_percentile(late_event_lags_ms, 0.95),
        sse_disconnect_count=sse_disconnect_count,
        stage_latency_ms=stage_latency_ms,
        detail_api_p95_ms=_percentile(list(detail_api_latencies_ms), 0.95),
        resolve_api_p95_ms=_percentile(list(resolve_api_latencies_ms), 0.95),
    )


def evaluate_data_quality(
    metrics: DataQualityMetrics,
    targets: SLOTargets,
    projections: Sequence[JourneyProjection],
    *,
    now: datetime,
    alert_rules: tuple[Any, ...] | None = None,
) -> tuple[DataQualityIncident, ...]:
    """Compare metrics against SLO targets and emit journey-linked incidents.

    Journey-scoped incidents (stalled, orphan, conflict, mismatch, broker
    reject) always carry ``journey_id``/``evidence_ref`` so an operator can
    jump straight to the offending journey. Aggregate SLO-breach incidents
    (materializer lag, correlation/reconciliation completeness, SSE
    disconnects, API p95 latency) are environment-scoped and driven by the
    declarative rules in ``alert_rules.py``/``trade_journey_alert_rules.json``
    (pass ``alert_rules`` to override the loaded default, e.g. in tests).
    """
    incidents: list[DataQualityIncident] = []

    for projection in projections:
        snapshot = projection.snapshot
        journey_id = projection.journey_id
        evidence_ref = journey_evidence_ref(journey_id)
        codes = {item.get("code") for item in projection.diagnostics}

        if _is_stalled(snapshot, now=now, stalled_after_seconds=targets.stalled_after_seconds):
            updated = _parse_iso(snapshot.get("updated_at"))
            age = (now - updated).total_seconds() if updated else 0.0
            severity = "critical" if age >= targets.stalled_after_seconds * 3 else "high"
            incidents.append(DataQualityIncident(
                code="journey_stalled", severity=severity,
                message=f"journey {journey_id} has not advanced in {int(age)}s",
                metric_name="stalled_after_seconds", observed_value=age,
                target_value=targets.stalled_after_seconds,
                alert_path=AlertPath(event_type="trade_journey.slo.journey_stalled"),
                journey_id=journey_id, tenant_id=projection.tenant_id,
                environment=projection.environment, evidence_ref=evidence_ref,
            ))

        if "orphan_identifier" in codes:
            incidents.append(DataQualityIncident(
                code="orphan_identifier", severity="warning",
                message=f"journey {journey_id} has an order_id with no client_order_id or signal_id",
                metric_name="orphan_event_rate", observed_value=True, target_value=False,
                alert_path=AlertPath(event_type="trade_journey.slo.orphan_identifier"),
                journey_id=journey_id, tenant_id=projection.tenant_id,
                environment=projection.environment, evidence_ref=evidence_ref,
            ))

        if "identifier_conflict" in codes:
            conflict = next(item for item in projection.diagnostics if item.get("code") == "identifier_conflict")
            incidents.append(DataQualityIncident(
                code="identifier_conflict", severity="warning",
                message=(
                    f"journey {journey_id} has conflicting {conflict['identifier_type']} values: "
                    f"{conflict['values']}"
                ),
                metric_name="identifier_conflict_rate", observed_value=True, target_value=False,
                alert_path=AlertPath(event_type="trade_journey.slo.identifier_conflict"),
                journey_id=journey_id, tenant_id=projection.tenant_id,
                environment=projection.environment, evidence_ref=evidence_ref,
            ))

        if "conflicting_terminal_states" in codes:
            incidents.append(DataQualityIncident(
                code="conflicting_terminal_states", severity="critical",
                message=f"journey {journey_id} reports more than one terminal state",
                metric_name="conflicting_terminal_rate", observed_value=True, target_value=False,
                alert_path=AlertPath(event_type="trade_journey.slo.conflicting_terminal_states"),
                journey_id=journey_id, tenant_id=projection.tenant_id,
                environment=projection.environment, evidence_ref=evidence_ref,
            ))

        if snapshot.get("status") == "completed_with_variance":
            updated = _parse_iso(snapshot.get("updated_at"))
            age = (now - updated).total_seconds() if updated else 0.0
            incidents.append(DataQualityIncident(
                code="reconciliation_mismatch", severity="critical",
                message=f"journey {journey_id} has an unresolved reconciliation mismatch aged {int(age)}s",
                metric_name="reconciliation_mismatch_max_age_seconds", observed_value=age,
                target_value=0,
                alert_path=AlertPath(event_type="trade_journey.slo.reconciliation_mismatch"),
                journey_id=journey_id, tenant_id=projection.tenant_id,
                environment=projection.environment, evidence_ref=evidence_ref,
            ))

        stages = snapshot.get("stages") or {}
        if stages.get("broker_acknowledgement", {}).get("status") == "rejected":
            incidents.append(DataQualityIncident(
                code="broker_reject", severity="warning",
                message=f"journey {journey_id} was rejected at broker acknowledgement",
                metric_name="broker_reject_rate", observed_value=True, target_value=False,
                alert_path=AlertPath(event_type="trade_journey.slo.broker_reject"),
                journey_id=journey_id, tenant_id=projection.tenant_id,
                environment=projection.environment, evidence_ref=evidence_ref,
            ))

    for firing in evaluate_aggregate_rules(metrics, targets, alert_rules or load_alert_rules()):
        rule = firing.rule
        message = _AGGREGATE_ALERT_MESSAGES[rule.code](firing.observed_value, firing.target_value, targets.environment)
        incidents.append(DataQualityIncident(
            code=rule.code, severity=rule.severity, message=message,
            metric_name=rule.metric_name, observed_value=firing.observed_value,
            target_value=firing.target_value,
            alert_path=AlertPath(event_type=rule.event_type),
        ))

    return tuple(incidents)


_AGGREGATE_ALERT_MESSAGES: Mapping[str, Any] = {
    "materializer_lag_breach": lambda observed, target, environment: (
        f"materializer lag {observed:.1f}s exceeds the {target}s {environment} target"
    ),
    "correlation_completeness_breach": lambda observed, target, environment: (
        f"correlation completeness {observed:.4f} is below the {target} {environment} target"
    ),
    "reconciliation_completeness_breach": lambda observed, target, environment: (
        f"reconciliation completeness {observed:.4f} is below the {target} {environment} target"
    ),
    "sse_disconnect": lambda observed, target, environment: (
        f"{int(observed)} SSE disconnect(s) observed in {environment}"
    ),
    "detail_api_p95_breach": lambda observed, target, environment: (
        f"detail API p95 latency {observed:.1f}ms exceeds the {target:.0f}ms {environment} target"
    ),
    "resolve_api_p95_breach": lambda observed, target, environment: (
        f"resolve API p95 latency {observed:.1f}ms exceeds the {target:.0f}ms {environment} target"
    ),
}


def metrics_to_dict(metrics: DataQualityMetrics) -> dict[str, Any]:
    return {
        "schema_version": metrics.schema_version,
        "environment": metrics.environment,
        "measured_at": metrics.measured_at,
        "total_journeys": metrics.total_journeys,
        "materializer_lag_seconds": metrics.materializer_lag_seconds,
        "stalled_count": metrics.stalled_count,
        "orphan_event_rate": metrics.orphan_event_rate,
        "missing_identifier_rate": metrics.missing_identifier_rate,
        "identifier_conflict_rate": metrics.identifier_conflict_rate,
        "conflicting_terminal_rate": metrics.conflicting_terminal_rate,
        "correlation_completeness_rate": metrics.correlation_completeness_rate,
        "reconciliation_completeness_rate": metrics.reconciliation_completeness_rate,
        "broker_reject_count": metrics.broker_reject_count,
        "broker_reject_rate": metrics.broker_reject_rate,
        "partial_fill_max_age_seconds": metrics.partial_fill_max_age_seconds,
        "reconciliation_mismatch_max_age_seconds": metrics.reconciliation_mismatch_max_age_seconds,
        "late_event_lag_p95_ms": metrics.late_event_lag_p95_ms,
        "sse_disconnect_count": metrics.sse_disconnect_count,
        "stage_latency_ms": dict(metrics.stage_latency_ms),
        "detail_api_p95_ms": metrics.detail_api_p95_ms,
        "resolve_api_p95_ms": metrics.resolve_api_p95_ms,
    }


def incident_to_dict(incident: DataQualityIncident) -> dict[str, Any]:
    return {
        "code": incident.code,
        "severity": incident.severity,
        "message": incident.message,
        "metric_name": incident.metric_name,
        "observed_value": incident.observed_value,
        "target_value": incident.target_value,
        "journey_id": incident.journey_id,
        "tenant_id": incident.tenant_id,
        "environment": incident.environment,
        "evidence_ref": incident.evidence_ref,
        "alert_path": {
            "event_type": incident.alert_path.event_type,
            "severity_channel": incident.alert_path.severity_channel,
            "operator_surface": incident.alert_path.operator_surface,
            "escalation_target": incident.alert_path.escalation_target,
            "runbook": incident.alert_path.runbook,
        },
    }
