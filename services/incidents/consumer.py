"""Threshold telemetry consumer for the Incident Evidence Service.

This module is intentionally small and service-local: it adapts telemetry
threshold-breach payloads into the canonical IncidentCase domain object, then
writes through the IncidentStore owned by the incident service.
"""
from __future__ import annotations

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
from services.incident.incident import IncidentCase, IncidentError, IncidentStore


class IncidentConsumerError(ValueError):
    """Raised when a telemetry payload cannot be consumed into an IncidentCase."""


@dataclass(frozen=True)
class ThresholdIncidentResult:
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
            return ThresholdIncidentResult(incident=existing, created=False)

        try:
            created = self._store.create_incident(incident)
        except IncidentError as exc:
            existing = self._store.get_incident(incident.incident_id)
            if existing is not None:
                return ThresholdIncidentResult(incident=existing, created=False)
            raise IncidentConsumerError(str(exc)) from exc

        return ThresholdIncidentResult(incident=created, created=True)


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
    )


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
    return "; ".join(parts)


def _is_breached(threshold: Mapping[str, Any]) -> bool:
    value = threshold.get("breached", True)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no"}
    return bool(value)


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
