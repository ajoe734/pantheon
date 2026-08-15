"""Threshold telemetry consumer for the Incident Evidence Service.

This module is intentionally small and service-local: it adapts telemetry
threshold-breach payloads into the canonical IncidentCase domain object, then
writes through the IncidentStore owned by the incident service.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from services.incident.evidence_collector import (
    EvidenceCollectionError,
    PostmortemEvidenceCollector,
    RuntimeBindingEvidence,
    TelemetryEvidence,
)
from services.incident.incident import (
    IncidentCase,
    IncidentConcurrencyError,
    IncidentError,
    IncidentStore,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class IncidentConsumerError(ValueError):
    """Raised when a telemetry payload cannot be consumed into an IncidentCase."""


# Mirrors the `threshold_snapshots[].signal_type`/`.comparator` enums in
# services/control-plane/governance/evolution_decision.schema.json (the
# canonical ThresholdSnapshot shape). A payload whose snapshot uses a value
# outside these enums is not canonical and must be rejected at this boundary,
# the same as a missing required field.
_CANONICAL_SIGNAL_TYPES = {
    "performance_degradation",
    "execution_drift",
    "feature_drift",
    "human_correction",
    "governance_incident",
    "manual_review",
}
_CANONICAL_COMPARATORS = {"gt", "gte", "lt", "lte", "eq", "neq"}


class IncidentConsumerRetryableError(IncidentConsumerError):
    """Raised when repeated owner-store CAS contention requires upstream retry."""


@dataclass(frozen=True)
class ThresholdIncidentResult:
    incident: IncidentCase
    created: bool


@dataclass(frozen=True)
class DriftReportIncidentResult:
    incident: IncidentCase
    created: bool
    incident_cluster_id: str


@dataclass(frozen=True)
class InfrastructureHealthIncidentResult:
    incident: IncidentCase
    created: bool


class ThresholdTelemetryIncidentConsumer:
    """Consume a threshold breach telemetry payload into IncidentStore."""

    def __init__(
        self,
        *,
        incident_store: IncidentStore,
        collector: Optional[PostmortemEvidenceCollector] = None,
        reference_validator: Any | None = None,
    ) -> None:
        self._store = incident_store
        self._collector = collector or PostmortemEvidenceCollector()
        self._reference_validator = reference_validator

    def consume(self, payload: Mapping[str, Any]) -> ThresholdIncidentResult:
        try:
            incident = build_incident_from_threshold_payload(
                payload,
                collector=self._collector,
            )
        except (EvidenceCollectionError, IncidentError, ValueError, TypeError) as exc:
            raise IncidentConsumerError(str(exc)) from exc

        if self._reference_validator is not None:
            self._reference_validator.validate_incident(incident)

        existing = self._store.get_incident(incident.incident_id)
        if existing is not None:
            _require_same_incident_identity(existing, incident)
            return ThresholdIncidentResult(incident=existing, created=False)

        try:
            created = self._store.create_incident(incident)
        except IncidentError as exc:
            existing = self._store.get_incident(incident.incident_id)
            if existing is not None:
                _require_same_incident_identity(existing, incident)
                return ThresholdIncidentResult(incident=existing, created=False)
            raise IncidentConsumerError(str(exc)) from exc

        return ThresholdIncidentResult(incident=created, created=True)


class DriftReportIncidentConsumer:
    """Consume DriftReports into deduped IncidentCases."""

    def __init__(
        self,
        *,
        incident_store: IncidentStore,
        reference_validator: Any | None = None,
    ) -> None:
        self._store = incident_store
        self._reference_validator = reference_validator

    def consume(self, payload: Mapping[str, Any]) -> DriftReportIncidentResult:
        try:
            report = _drift_report_payload(payload)
            incident = build_incident_from_drift_report(report)
        except (IncidentError, ValueError, TypeError) as exc:
            raise IncidentConsumerError(str(exc)) from exc

        if self._reference_validator is not None:
            self._reference_validator.validate_incident(incident)

        existing = self._find_existing_incident(incident)
        if existing is not None:
            updated = self._merge_incident_evidence(existing.incident_id, incident)
            return DriftReportIncidentResult(
                incident=updated,
                created=False,
                incident_cluster_id=incident.incident_cluster_id or "",
            )

        try:
            created = self._store.create_incident(incident)
        except IncidentError as exc:
            existing = self._store.get_incident(incident.incident_id)
            if existing is not None:
                updated = self._merge_incident_evidence(existing.incident_id, incident)
                return DriftReportIncidentResult(
                    incident=updated,
                    created=False,
                    incident_cluster_id=incident.incident_cluster_id or "",
                )
            raise IncidentConsumerError(str(exc)) from exc

        return DriftReportIncidentResult(
            incident=created,
            created=True,
            incident_cluster_id=incident.incident_cluster_id or "",
        )

    def _merge_incident_evidence(
        self,
        incident_id: str,
        incoming: IncidentCase,
        *,
        max_attempts: int = 3,
    ) -> IncidentCase:
        last_conflict: IncidentConcurrencyError | None = None
        for _ in range(max(1, int(max_attempts))):
            try:
                # The store refreshes durable state inside every guarded write,
                # so a retry merges incoming evidence onto the CAS winner.
                return self._store.merge_incident_evidence(incident_id, incoming)
            except IncidentConcurrencyError as exc:
                last_conflict = exc
            except IncidentError as exc:
                raise IncidentConsumerError(str(exc)) from exc
        raise IncidentConsumerRetryableError(
            "Incident evidence changed concurrently after retry budget: "
            f"{incident_id}: {last_conflict}"
        ) from last_conflict

    def _find_existing_incident(self, incident: IncidentCase) -> IncidentCase | None:
        direct = self._store.get_incident(incident.incident_id)
        if direct is not None:
            return direct

        cluster_id = incident.incident_cluster_id
        if not cluster_id:
            return None
        for existing in self._store.find_open_incidents():
            if (
                existing.binding_id == incident.binding_id
                and existing.runtime_id == incident.runtime_id
                and existing.incident_cluster_id == cluster_id
            ):
                return existing
        return None


class InfrastructureHealthIncidentConsumer:
    """Consume non-trading infrastructure health events into IncidentStore.

    This route-specific consumer is separate from generic incident creation.
    Infrastructure health is not trading telemetry and must not be forced
    through caller-supplied fake RuntimeBinding evidence.
    """

    def __init__(self, *, incident_store: IncidentStore) -> None:
        self._store = incident_store

    def consume(self, payload: Mapping[str, Any]) -> InfrastructureHealthIncidentResult:
        try:
            incident = build_incident_from_infrastructure_health_payload(payload)
        except (IncidentError, ValueError, TypeError) as exc:
            raise IncidentConsumerError(str(exc)) from exc

        existing = self._store.get_incident(incident.incident_id)
        if existing is not None:
            _require_same_infrastructure_incident_identity(existing, incident)
            return InfrastructureHealthIncidentResult(incident=existing, created=False)

        try:
            created = self._store.create_incident(incident)
        except IncidentError as exc:
            existing = self._store.get_incident(incident.incident_id)
            if existing is not None:
                _require_same_infrastructure_incident_identity(existing, incident)
                return InfrastructureHealthIncidentResult(incident=existing, created=False)
            raise IncidentConsumerError(str(exc)) from exc

        return InfrastructureHealthIncidentResult(incident=created, created=True)


def build_incident_from_threshold_payload(
    payload: Mapping[str, Any],
    *,
    collector: Optional[PostmortemEvidenceCollector] = None,
) -> IncidentCase:
    """Build a canonical IncidentCase from a telemetry threshold breach payload."""
    if not isinstance(payload, Mapping):
        raise IncidentConsumerError("threshold payload must be an object")

    threshold = _threshold_snapshot(payload)
    if not _is_breached(threshold):
        raise IncidentConsumerError("threshold snapshot is not breached")

    # Validate the full canonical ThresholdSnapshot required-field boundary
    # (evolution_decision.schema.json `threshold_snapshots[]`: policy_source,
    # signal_type, metric_name, comparator, observed_value, threshold_value),
    # not just metric_name/policy_source. This is the authoritative consumer
    # boundary every producer's payload passes through, so a payload that
    # dropped a required field (e.g. signal_type/comparator/observed_value/
    # threshold_value) must never reach IncidentCase creation.
    metric_name_val = threshold.get("metric_name")
    if not isinstance(metric_name_val, str) or not metric_name_val.strip():
        raise IncidentConsumerError("metric_name is required and cannot be empty")

    policy_source_val = threshold.get("policy_source")
    if not isinstance(policy_source_val, str) or not policy_source_val.strip():
        raise IncidentConsumerError("policy_source is required and cannot be empty")

    signal_type_val = threshold.get("signal_type")
    if not isinstance(signal_type_val, str) or signal_type_val not in _CANONICAL_SIGNAL_TYPES:
        raise IncidentConsumerError("signal_type is required and must be a canonical ThresholdSignalType")

    comparator_val = threshold.get("comparator")
    if not isinstance(comparator_val, str) or comparator_val not in _CANONICAL_COMPARATORS:
        raise IncidentConsumerError("comparator is required and must be a canonical ThresholdComparator")

    if threshold.get("observed_value") is None:
        raise IncidentConsumerError("observed_value is required")

    if threshold.get("threshold_value") is None:
        raise IncidentConsumerError("threshold_value is required")

    # evolution_decision.schema.json declares optional `window`/`note` as
    # `type: string`. Without this check a list/object value passes through
    # `_threshold_notes()`'s `if value not in (None, "")` guard unchanged and
    # gets silently stringified into the incident's evidence (round-9 review
    # point 5); reject anything but a string when the field is present.
    window_val = threshold.get("window")
    if window_val is not None and not isinstance(window_val, str):
        raise IncidentConsumerError("threshold_snapshot.window must be a string when present")

    note_val = threshold.get("note")
    if note_val is not None and not isinstance(note_val, str):
        raise IncidentConsumerError("threshold_snapshot.note must be a string when present")

    event = _telemetry_event(payload)
    runtime_summary = _mapping(payload.get("runtime_summary_projection")) or _mapping(
        payload.get("runtime_summary")
    )
    authority_refs = _mapping(event.get("authority_refs"))
    target = _mapping(event.get("target"))

    event_id = _required_str(
        "telemetry event_id",
        _first_value(event, payload, keys=("event_id", "telemetry_event_id", "id")),
    )
    metric_name = str(threshold.get("metric_name") or "threshold_breach")

    runtime_binding = RuntimeBindingEvidence(
        binding_id=_required_str(
            "runtime binding id",
            _first_value(
                event,
                payload,
                runtime_summary,
                keys=("runtime_binding_id", "binding_id"),
            ),
        ),
        runtime_id=_required_str(
            "runtime_id",
            _first_value(event, runtime_summary, payload, keys=("runtime_id", "runtime_ref")),
        ),
        deployment_stage=_required_str(
            "deployment_stage",
            _first_value(
                event,
                runtime_summary,
                payload,
                keys=("deployment_stage", "deployment_mode", "environment", "execution_mode"),
            ),
        ),
        deployment_plan_id=_required_str(
            "deployment_plan_id",
            _first_value(
                event,
                runtime_summary,
                payload,
                keys=("deployment_plan_id", "plan_id"),
            ),
        ),
        capital_pool_id=_required_str(
            "capital_pool_id",
            _first_value(event, runtime_summary, payload, keys=("capital_pool_id",)),
        ),
        persona_capital_binding_id=_required_str(
            "persona_capital_binding_id",
            _first_value(
                event,
                runtime_summary,
                payload,
                keys=("persona_capital_binding_id", "persona_binding_id"),
            ),
        ),
        artifact_id=_required_str(
            "artifact_id",
            _first_value(event, target, runtime_summary, payload, keys=("artifact_id", "registry_id")),
        ),
        artifact_version=_required_str(
            "artifact_version",
            _first_value(event, target, runtime_summary, payload, keys=("artifact_version", "version")),
        ),
        trace_id=_required_str(
            "trace_id",
            _first_value(event, authority_refs, runtime_summary, payload, keys=("trace_id",)),
        ),
    )
    telemetry = _telemetry_evidence(payload, event, event_id)
    notes = _threshold_notes(threshold)
    bundle = (collector or PostmortemEvidenceCollector()).assemble_bundle(
        runtime_binding=runtime_binding,
        telemetry=telemetry,
        extra_notes=notes,
    )

    return (collector or PostmortemEvidenceCollector()).create_incident(
        bundle=bundle,
        title=_title(payload, event, metric_name),
        severity=_severity(payload, event, threshold),
        incident_id=_incident_id(payload, event_id, metric_name),
        created_at=_created_at(payload, event),
        threshold_identity=_threshold_identity(threshold),
    )


def build_incident_from_infrastructure_health_payload(payload: Mapping[str, Any]) -> IncidentCase:
    """Build an IncidentCase from the BFF infrastructure incident contract.

    The current IncidentCase backbone is still runtime-oriented.  This adapter
    keeps the public infrastructure route honest by deriving stable
    ``infra-*`` subject evidence inside the incident authority instead of
    accepting producer-supplied RuntimeBinding fields.
    """
    if not isinstance(payload, Mapping):
        raise IncidentConsumerError("infrastructure incident payload must be an object")

    schema_version = _required_str(
        "schema_version",
        _first_value(payload, keys=("schema_version",)),
    )
    if schema_version != "pantheon.infrastructure-incident/1":
        raise IncidentConsumerError(
            "schema_version must be pantheon.infrastructure-incident/1"
        )

    incident_id = _required_str("incident_id", _first_value(payload, keys=("incident_id",)))
    source_event_id = _required_str(
        "source_event_id",
        _first_value(payload, keys=("source_event_id", "event_id", "telemetry_event_id")),
    )
    tenant_id = _required_str("tenant_id", _first_value(payload, keys=("tenant_id",)))
    producer = _required_str("producer", _first_value(payload, keys=("producer",)))
    component = _mapping(payload.get("component"))
    if component is None:
        raise IncidentConsumerError("component is required")
    service_name = _required_str(
        "component.service_name",
        _first_value(component, keys=("service_name",)),
    )
    component_kind = _required_str(
        "component.component_kind",
        _first_value(component, keys=("component_kind",)),
    )
    endpoint = _required_str("component.endpoint", _first_value(component, keys=("endpoint",)))

    status = str(payload.get("status") or "open").strip().lower()
    if status not in {"open", "investigating"}:
        raise IncidentConsumerError(
            "infrastructure incident consumer only opens active incidents"
        )

    namespace = _safe_component(f"{tenant_id}:{producer}:{service_name}")
    endpoint_digest = uuid.uuid5(uuid.NAMESPACE_URL, endpoint).hex[:12]
    artifact_id = f"infra-component-{_safe_component(service_name)}"
    artifact_version = f"endpoint-{endpoint_digest}"
    evidence_summary = _infrastructure_evidence_summary(
        payload=payload,
        tenant_id=tenant_id,
        producer=producer,
        service_name=service_name,
        component_kind=component_kind,
        endpoint=endpoint,
        source_event_id=source_event_id,
    )
    return IncidentCase(
        incident_id=incident_id,
        title=str(payload.get("title") or f"Infrastructure degradation: {service_name}").strip(),
        status=status,
        severity=_infrastructure_incident_severity(payload.get("severity")),
        created_at=str(payload.get("created_at") or payload.get("observed_at") or _utc_now()),
        binding_id=f"infra-subject-{namespace}",
        deployment_stage="paper",
        deployment_plan_id=f"infra-plan-{_safe_component(producer)}",
        capital_pool_id=f"infra-tenant-{_safe_component(tenant_id)}",
        persona_capital_binding_id=f"infra-authority-{_safe_component(producer)}",
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        runtime_id=f"infra-endpoint-{endpoint_digest}",
        trace_id=source_event_id,
        telemetry_event_ids=_infrastructure_telemetry_event_ids(payload, source_event_id),
        incident_cluster_id=_infrastructure_cluster_id(tenant_id, producer, service_name),
        evidence_summary=evidence_summary,
        lineage_ref=f"{artifact_id}@{artifact_version}",
    )


def build_incident_from_drift_report(payload: Mapping[str, Any]) -> IncidentCase:
    """Build a canonical IncidentCase from a reconciliation DriftReport."""
    if not isinstance(payload, Mapping):
        raise IncidentConsumerError("drift report payload must be an object")

    report_id = _required_str(
        "drift_report_id",
        _first_value(payload, keys=("drift_report_id", "id")),
    )
    if str(payload.get("status") or "open").lower() not in {"open", "investigating"}:
        raise IncidentConsumerError("drift report is not open")

    binding_id = _required_str(
        "binding_id",
        _first_value(payload, keys=("binding_id", "runtime_binding_id", "scope_ref")),
    )
    runtime_id = _required_str("runtime_id", _first_value(payload, keys=("runtime_id", "runtime_ref")))
    cluster_id = _drift_incident_cluster_id(payload)
    telemetry_event_ids = _drift_telemetry_event_ids(payload)
    if len(telemetry_event_ids) != 1:
        raise IncidentConsumerError(
            "drift report must link exactly one telemetry_event_id"
        )

    reconciliation_ids = _drift_reconciliation_ids(payload)
    if report_id not in reconciliation_ids:
        reconciliation_ids.insert(0, report_id)

    severity = _drift_incident_severity(payload.get("severity"))
    artifact_id = _required_str("artifact_id", _first_value(payload, keys=("artifact_id", "registry_id")))
    artifact_version = _required_str(
        "artifact_version",
        _first_value(payload, keys=("artifact_version", "version")),
    )
    worst_metric = _drift_metric(payload)
    evidence_summary = (
        f"DriftReport {report_id}; cluster={cluster_id}; "
        f"reconciliation_ids={','.join(reconciliation_ids)}; "
        f"telemetry_event_ids={','.join(telemetry_event_ids)}"
    )
    if worst_metric:
        evidence_summary = f"{evidence_summary}; worst_metric={worst_metric}"

    return IncidentCase(
        incident_id=_drift_incident_id(payload, binding_id, runtime_id, cluster_id),
        title=str(
            payload.get("title")
            or f"Drift threshold breach: {cluster_id} for {binding_id}"
        ),
        status="open",
        severity=severity,
        created_at=str(payload.get("generated_at") or payload.get("created_at") or _utc_now()),
        binding_id=binding_id,
        deployment_stage=_required_str(
            "deployment_stage",
            _first_value(payload, keys=("deployment_stage", "deployment_mode", "environment")),
        ),
        deployment_plan_id=_required_str(
            "deployment_plan_id",
            _first_value(payload, keys=("deployment_plan_id", "plan_id")),
        ),
        capital_pool_id=_required_str("capital_pool_id", _first_value(payload, keys=("capital_pool_id",))),
        persona_capital_binding_id=_required_str(
            "persona_capital_binding_id",
            _first_value(payload, keys=("persona_capital_binding_id", "persona_binding_id")),
        ),
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        runtime_id=runtime_id,
        trace_id=_required_str("trace_id", _first_value(payload, keys=("trace_id",))),
        telemetry_event_ids=telemetry_event_ids,
        reconciliation_ids=reconciliation_ids,
        incident_cluster_id=cluster_id,
        evidence_summary=evidence_summary,
        lineage_ref=f"{artifact_id}@{artifact_version}",
    )


def _infrastructure_telemetry_event_ids(
    payload: Mapping[str, Any],
    source_event_id: str,
) -> list[str]:
    seen: dict[str, None] = {}

    def add(value: Any) -> None:
        if value in (None, ""):
            return
        if isinstance(value, str):
            item = value.strip()
            if item:
                seen[item] = None
            return
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            for part in value:
                add(part)

    add(source_event_id)
    add(payload.get("telemetry_event_id"))
    add(payload.get("telemetry_event_ids"))
    return list(seen)


def _infrastructure_cluster_id(tenant_id: str, producer: str, service_name: str) -> str:
    return (
        "infrastructure:"
        f"{_safe_component(tenant_id)}:"
        f"{_safe_component(producer)}:"
        f"{_safe_component(service_name)}"
    )


def _infrastructure_evidence_summary(
    *,
    payload: Mapping[str, Any],
    tenant_id: str,
    producer: str,
    service_name: str,
    component_kind: str,
    endpoint: str,
    source_event_id: str,
) -> str:
    explicit = str(payload.get("evidence_summary") or "").strip()
    prefix = (
        "non_trading_infrastructure_incident=true; "
        f"tenant_id={tenant_id}; producer={producer}; "
        f"service_name={service_name}; component_kind={component_kind}; "
        f"endpoint={endpoint}; source_event_id={source_event_id}"
    )
    return f"{prefix}; {explicit}" if explicit else prefix


def _infrastructure_incident_severity(raw: Any) -> str:
    normalized = str(raw or "").strip().lower().replace("_", "-")
    mapping = {
        "critical": "critical",
        "high": "high",
        "error": "high",
        "medium": "medium",
        "warning": "medium",
        "warn": "medium",
        "low": "low",
        "degraded": "low",
    }
    return mapping.get(normalized, "high")


def _require_same_infrastructure_incident_identity(
    existing: IncidentCase,
    incident: IncidentCase,
) -> None:
    same_binding = existing.binding_id == incident.binding_id
    same_cluster = existing.incident_cluster_id == incident.incident_cluster_id
    existing_primary = existing.telemetry_event_ids[0] if existing.telemetry_event_ids else None
    incoming_primary = incident.telemetry_event_ids[0] if incident.telemetry_event_ids else None
    if not (same_binding and same_cluster and existing_primary == incoming_primary):
        raise IncidentConsumerError(
            f"incident_id {incident.incident_id!r} conflicts with an existing "
            "infrastructure incident for a different subject or source_event_id"
        )


def _drift_report_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("drift_report", "report"):
        nested = _mapping(payload.get(key))
        if nested is not None:
            return nested
    return payload


def _drift_incident_cluster_id(payload: Mapping[str, Any]) -> str:
    explicit = _first_value(payload, keys=("incident_cluster_id", "cluster_id"))
    if explicit:
        return explicit
    metric = _drift_metric(payload)
    drift_type = str(payload.get("drift_type") or metric or "threshold_breach")
    return f"drift:{_safe_component(drift_type)}"


def _drift_metric(payload: Mapping[str, Any]) -> str:
    metrics = _mapping(payload.get("metrics")) or {}
    value = metrics.get("worst_metric") or payload.get("worst_metric")
    if value not in (None, ""):
        return str(value)
    breached = metrics.get("breached_metric_ids") or payload.get("breached_metric_ids")
    if isinstance(breached, Sequence) and not isinstance(breached, (str, bytes, bytearray)):
        for item in breached:
            if item not in (None, ""):
                return str(item)
    return ""


def _drift_incident_id(
    payload: Mapping[str, Any],
    binding_id: str,
    runtime_id: str,
    cluster_id: str,
) -> str:
    explicit = _first_value(payload, keys=("incident_id",))
    if explicit:
        return explicit
    seed = f"{binding_id}:{runtime_id}:{cluster_id}"
    return f"inc-drift-{uuid.uuid5(uuid.NAMESPACE_URL, seed).hex[:12]}"


def _drift_telemetry_event_ids(payload: Mapping[str, Any]) -> list[str]:
    seen: dict[str, None] = {}

    def add(value: Any) -> None:
        if value in (None, ""):
            return
        if isinstance(value, str):
            item = value.strip()
            if item:
                seen[item] = None
            return
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            for part in value:
                add(part)

    add(payload.get("telemetry_event_ids"))
    add(payload.get("telemetry_event_id"))
    for ref in payload.get("evidence_refs") or []:
        ref_id = _evidence_ref_id(ref, expected_type="telemetry_event")
        if ref_id:
            add(ref_id)
    return list(seen)


def _drift_reconciliation_ids(payload: Mapping[str, Any]) -> list[str]:
    seen: dict[str, None] = {}

    def add(value: Any) -> None:
        if value in (None, ""):
            return
        if isinstance(value, str):
            item = value.strip()
            if item:
                seen[item] = None
            return
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            for part in value:
                add(part)

    for key in (
        "drift_report_id",
        "recon_run_id",
        "reconciliation_id",
        "reconciliation_record_id",
        "evaluation_id",
    ):
        add(payload.get(key))
    for ref in payload.get("evidence_refs") or []:
        ref_id = _evidence_ref_id(
            ref,
            expected_type={"drift_report", "reconciliation_record", "drift_evaluation"},
        )
        if ref_id:
            add(ref_id)
    return list(seen)


def _evidence_ref_id(ref: Any, *, expected_type: str | set[str]) -> str | None:
    expected = {expected_type} if isinstance(expected_type, str) else expected_type
    if isinstance(ref, str):
        if ":" not in ref:
            return None
        ref_type, ref_id = ref.split(":", 1)
        return ref_id.strip() if ref_type.strip() in expected and ref_id.strip() else None
    if isinstance(ref, Mapping):
        ref_type = str(ref.get("type") or ref.get("ref_type") or "").strip()
        ref_id = str(ref.get("id") or ref.get("ref_id") or "").strip()
        if ref_type in expected and ref_id:
            return ref_id
    return None


def _drift_incident_severity(raw: Any) -> str:
    normalized = str(raw or "").strip().lower().replace("_", "-")
    mapping = {
        "critical": "critical",
        "error": "high",
        "high": "high",
        "warning": "medium",
        "medium": "medium",
        "low": "low",
        "degraded": "low",
    }
    return mapping.get(normalized, "medium")


def _safe_component(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value.strip())
    safe = safe.strip("-._").lower()
    return safe or "threshold-breach"


def _threshold_snapshot(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("threshold_snapshot", "threshold", "threshold_breach"):
        value = _mapping(payload.get(key))
        if value is not None:
            return value
    snapshots = payload.get("threshold_snapshots")
    if isinstance(snapshots, Sequence) and not isinstance(snapshots, (str, bytes, bytearray)):
        for item in snapshots:
            value = _mapping(item)
            if value is not None and _is_breached(value):
                return value
    if _looks_like_threshold(payload):
        return payload
    raise IncidentConsumerError("threshold payload is missing threshold_snapshot")


def _telemetry_event(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("telemetry_event", "telemetry", "event"):
        value = _mapping(payload.get(key))
        if value is not None:
            return value

    packet = _mapping(payload.get("telemetry_packet"))
    if packet is not None:
        events = packet.get("events")
        if isinstance(events, Sequence) and not isinstance(events, (str, bytes, bytearray)):
            requested_id = _first_value(payload, keys=("telemetry_event_id", "event_id"))
            for item in events:
                event = _mapping(item)
                if event is None:
                    continue
                if requested_id and str(event.get("event_id")) == requested_id:
                    return event
            for item in reversed(events):
                event = _mapping(item)
                if event is not None:
                    return event

    if _first_value(payload, keys=("event_id", "telemetry_event_id")):
        return payload
    raise IncidentConsumerError("threshold payload is missing telemetry_event")


def _telemetry_evidence(
    payload: Mapping[str, Any],
    event: Mapping[str, Any],
    event_id: str,
) -> TelemetryEvidence:
    event_type = str(event.get("event_type") or "").lower()
    event_ids = _event_ids(payload, event, event_id)
    return TelemetryEvidence(
        telemetry_event_ids=event_ids,
        heartbeat_lag_ms=_optional_float(_first_value(event, payload, keys=("heartbeat_lag_ms",))),
        pnl_snapshot_refs=[event_id] if "pnl" in event_type else [],
        order_rejection_refs=[event_id] if "reject" in event_type else [],
        fill_observation_refs=[event_id] if "fill" in event_type else [],
    )


def _event_ids(payload: Mapping[str, Any], event: Mapping[str, Any], event_id: str) -> list[str]:
    seen: dict[str, None] = {}

    def add(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, str):
            item = value.strip()
            if item:
                seen[item] = None
            return
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            for part in value:
                add(part)

    add(event_id)
    add(event.get("event_id"))
    add(event.get("telemetry_event_id"))
    add(payload.get("telemetry_event_id"))
    add(payload.get("telemetry_event_ids"))
    packet = _mapping(payload.get("telemetry_packet"))
    if packet is not None:
        add(packet.get("event_ids"))
    return list(seen)


def _incident_id(payload: Mapping[str, Any], event_id: str, metric_name: str) -> str:
    explicit = _first_value(payload, keys=("incident_id",))
    if explicit:
        return explicit
    seed = f"{event_id}:{metric_name}"
    return f"inc-threshold-{uuid.uuid5(uuid.NAMESPACE_URL, seed).hex[:12]}"


def _threshold_identity(threshold: Mapping[str, Any]) -> str:
    """Canonical (metric_name, window, policy_source) identity for a breach.

    A JSON array of a fixed arity is an unambiguous encoding (unlike a
    delimiter-joined string, where distinct tuples can collide across the
    delimiter). Used to tell two breaches under the same explicit
    caller-supplied ``incident_id`` apart (round-9 review point 4).
    """
    return json.dumps(
        [threshold.get("metric_name"), threshold.get("window"), threshold.get("policy_source")],
        separators=(",", ":"),
    )


def _require_same_incident_identity(existing: IncidentCase, incident: IncidentCase) -> None:
    """Reject a caller-supplied ``incident_id`` that collides across identities.

    A producer may pass an explicit ``incident_id`` (``_incident_id`` above
    trusts it verbatim), so two payloads for entirely different events/
    bindings/metrics can hash to the same id. Looking that id up and treating
    whatever is stored under it as "the" duplicate — without checking it is
    actually the same breach — silently discards the new breach and reports
    a fabricated dedupe (round-8 review point 4). Same breach means: same
    binding, same runtime, the same *canonical primary* telemetry event id
    (``telemetry_event_ids[0]``, not an arbitrary intersection — a caller can
    inject an old id as a supplemental ``telemetry_event_ids`` entry while
    changing the real primary event, round-9 review point 4), and the same
    threshold identity (metric_name/window/policy_source). An incident with
    no recorded ``threshold_identity`` (e.g. created via a non-threshold
    path) can never be proven to be the same breach, so it fails closed as a
    conflict rather than matching by default.
    """
    same_binding = existing.binding_id == incident.binding_id
    same_runtime = existing.runtime_id == incident.runtime_id
    existing_primary = existing.telemetry_event_ids[0] if existing.telemetry_event_ids else None
    incident_primary = incident.telemetry_event_ids[0] if incident.telemetry_event_ids else None
    same_primary_event = existing_primary is not None and existing_primary == incident_primary
    same_threshold_identity = (
        existing.threshold_identity is not None
        and existing.threshold_identity == incident.threshold_identity
    )
    if not (same_binding and same_runtime and same_primary_event and same_threshold_identity):
        raise IncidentConsumerError(
            f"incident_id {incident.incident_id!r} conflicts with an existing incident "
            "for a different binding_id/runtime_id/primary telemetry_event_id/threshold "
            "identity; refusing to treat this as a duplicate of an unrelated incident"
        )


def _title(payload: Mapping[str, Any], event: Mapping[str, Any], metric_name: str) -> str:
    explicit = _first_value(payload, event, keys=("title", "headline"))
    if explicit:
        return explicit
    description = _first_value(payload, event, keys=("description", "note"))
    if description:
        return description
    return f"Threshold breach: {metric_name}"


def _created_at(payload: Mapping[str, Any], event: Mapping[str, Any]) -> str:
    value = _first_value(
        payload,
        event,
        keys=("created_at", "timestamp", "event_produced_at", "produced_at"),
    )
    if value:
        return value
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _severity(
    payload: Mapping[str, Any],
    event: Mapping[str, Any],
    threshold: Mapping[str, Any],
) -> str:
    raw = _first_value(payload, event, threshold, keys=("severity", "incident_severity"))
    if raw:
        normalized = str(raw).strip().lower().replace("_", "").replace("-", "")
        mapping = {
            "critical": "critical",
            "sev1": "critical",
            "severity1": "critical",
            "s1": "critical",
            "high": "high",
            "sev2": "high",
            "severity2": "high",
            "s2": "high",
            "medium": "medium",
            "sev3": "medium",
            "severity3": "medium",
            "s3": "medium",
            "low": "low",
            "sev4": "low",
            "severity4": "low",
            "s4": "low",
        }
        if normalized in mapping:
            return mapping[normalized]

    metric = str(threshold.get("metric_name") or "").lower()
    if "severity1" in metric or "sev1" in metric:
        return "critical"
    if "severity2" in metric or "sev2" in metric:
        return "high"
    signal = str(threshold.get("signal_type") or "").lower()
    if signal in {"governance_incident", "execution_drift", "performance_degradation"}:
        return "high"
    return "medium"


def _threshold_notes(threshold: Mapping[str, Any]) -> str:
    parts: list[str] = []
    fields = (
        ("threshold_metric", "metric_name"),
        ("observed", "observed_value"),
        ("comparator", "comparator"),
        ("threshold_value", "threshold_value"),
        ("threshold_window", "window"),
        ("policy_source", "policy_source"),
    )
    for label, key in fields:
        value = threshold.get(key)
        if value not in (None, ""):
            parts.append(f"{label}={value}")
    # Preserve the producer's own note verbatim (carries dedupe_key=... and
    # any baseline used) instead of dropping it: it is the audit trail that
    # proves why a rerun deduped instead of opening a second incident.
    note = threshold.get("note")
    if note not in (None, ""):
        parts.append(str(note))
    return "; ".join(parts)


def _is_breached(threshold: Mapping[str, Any]) -> bool:
    if "breached" not in threshold:
        return True
    value = threshold["breached"]
    # The governance schema (evolution_decision.schema.json threshold_snapshots[].
    # breached) declares this field `type: boolean`. Coercing arbitrary truthy
    # values (e.g. the string "yes") let a malformed producer payload open an
    # IncidentCase from a snapshot that was never actually validated as
    # breached (round-8 review point 3); reject anything but an actual bool.
    if not isinstance(value, bool):
        raise IncidentConsumerError(
            f"threshold_snapshot.breached must be a boolean per the governance schema, got {value!r}"
        )
    return value


def _looks_like_threshold(value: Mapping[str, Any]) -> bool:
    return all(key in value for key in ("metric_name", "observed_value", "threshold_value"))


def _mapping(value: Any) -> Optional[Mapping[str, Any]]:
    return value if isinstance(value, Mapping) else None


def _first_value(*sources: Mapping[str, Any] | None, keys: tuple[str, ...]) -> Optional[str]:
    for source in sources:
        if not source:
            continue
        for key in keys:
            value = source.get(key)
            if value is None or value == "":
                continue
            return str(value)
    return None


def _required_str(label: str, value: Optional[str]) -> str:
    if value is None or not str(value).strip():
        raise IncidentConsumerError(f"{label} is required")
    return str(value).strip()


def _optional_float(value: Optional[str]) -> Optional[float]:
    if value in (None, ""):
        return None
    return float(value)
