"""RuntimeHeartbeat adapter for canonical telemetry ingest."""

from __future__ import annotations

import uuid
from typing import Any


DEPLOYMENT_MODES = {"paper", "canary", "live"}
CONNECTIVITY_STATUSES = {"connected", "degraded", "disconnected"}
BROKER_STATUSES = {"ok", "degraded", "unavailable"}


class RuntimeHeartbeatValidationError(ValueError):
    """Validation error with a stable API code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _string_field(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeHeartbeatValidationError(
            "INVALID_RUNTIME_HEARTBEAT",
            f"RuntimeHeartbeat.{field} must be a non-empty string",
        )
    return value.strip()


def _enum_field(payload: dict[str, Any], field: str, allowed: set[str]) -> str:
    value = _string_field(payload, field).lower()
    if value not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise RuntimeHeartbeatValidationError(
            "INVALID_RUNTIME_HEARTBEAT",
            f"RuntimeHeartbeat.{field} must be one of: {allowed_values}",
        )
    return value


def _optional_number(payload: dict[str, Any], field: str) -> int | float | None:
    value = payload.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeHeartbeatValidationError(
            "INVALID_RUNTIME_HEARTBEAT",
            f"RuntimeHeartbeat.{field} must be a number or null",
        )
    return value


def _binding_value(binding: Any | None, field: str) -> Any:
    if binding is None:
        return None
    if isinstance(binding, dict):
        return binding.get(field)
    return getattr(binding, field, None)


def _evidence_field(payload: dict[str, Any], binding: Any | None, field: str) -> str:
    value = _binding_value(binding, field)
    if value in (None, ""):
        value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeHeartbeatValidationError(
            "MISSING_BINDING_EVIDENCE",
            f"RuntimeHeartbeat requires {field}; provide it directly or configure RuntimeBinding lookup",
        )
    return value.strip()


def _assert_matches_binding(
    payload_value: str,
    binding: Any | None,
    binding_field: str,
    *,
    payload_field: str,
) -> None:
    binding_value = _binding_value(binding, binding_field)
    if binding_value in (None, ""):
        return
    if payload_value != str(binding_value):
        raise RuntimeHeartbeatValidationError(
            "BINDING_MISMATCH",
            f"RuntimeHeartbeat.{payload_field}={payload_value!r} does not match "
            f"RuntimeBinding.{binding_field}={binding_value!r}",
        )


def _execution_mode_for_stage(deployment_stage: str) -> str:
    return "live" if deployment_stage in {"canary", "live"} else "paper"


def _target(payload: dict[str, Any], artifact_id: str, artifact_version: str) -> dict[str, Any]:
    target_payload = payload.get("target")
    target = dict(target_payload) if isinstance(target_payload, dict) else {}
    strategy_id = payload.get("strategy_id") or target.get("strategy_id") or artifact_id
    if not isinstance(strategy_id, str) or not strategy_id.strip():
        raise RuntimeHeartbeatValidationError(
            "INVALID_RUNTIME_HEARTBEAT",
            "RuntimeHeartbeat target strategy_id could not be derived",
        )
    target["strategy_id"] = strategy_id.strip()
    target.setdefault("artifact_version", artifact_version)
    target.setdefault("artifact_type", "execution_bundle")
    return target


def build_telemetry_event_from_runtime_heartbeat(
    payload: dict[str, Any],
    *,
    binding: Any | None = None,
) -> dict[str, Any]:
    """Convert a RuntimeHeartbeat payload into the canonical TelemetryEvent envelope."""
    if not isinstance(payload, dict):
        raise RuntimeHeartbeatValidationError(
            "INVALID_BODY",
            "Request body must be a RuntimeHeartbeat JSON object",
        )

    runtime_id = _string_field(payload, "runtime_id")
    runtime_binding_id = _string_field(payload, "runtime_binding_id")
    capital_pool_id = _string_field(payload, "capital_pool_id")
    artifact_id = _string_field(payload, "artifact_id")
    deployment_mode = _enum_field(payload, "deployment_mode", DEPLOYMENT_MODES)
    heartbeat_time = _string_field(payload, "heartbeat_time")
    connectivity_status = _enum_field(payload, "connectivity_status", CONNECTIVITY_STATUSES)
    broker_status = _enum_field(payload, "broker_status", BROKER_STATUSES)
    queue_lag_ms = _optional_number(payload, "queue_lag_ms")
    event_delivery_lag_ms = _optional_number(payload, "event_delivery_lag_ms")
    health_summary = payload.get("health_summary")
    if health_summary is not None and not isinstance(health_summary, dict):
        raise RuntimeHeartbeatValidationError(
            "INVALID_RUNTIME_HEARTBEAT",
            "RuntimeHeartbeat.health_summary must be an object or null",
        )

    _assert_matches_binding(runtime_id, binding, "runtime_id", payload_field="runtime_id")
    _assert_matches_binding(capital_pool_id, binding, "capital_pool_id", payload_field="capital_pool_id")
    _assert_matches_binding(artifact_id, binding, "artifact_id", payload_field="artifact_id")
    _assert_matches_binding(runtime_binding_id, binding, "binding_id", payload_field="runtime_binding_id")
    _assert_matches_binding(deployment_mode, binding, "deployment_mode", payload_field="deployment_mode")

    artifact_version = _evidence_field(payload, binding, "artifact_version")
    plan_id = _evidence_field(payload, binding, "plan_id")
    persona_capital_binding_id = _evidence_field(payload, binding, "persona_capital_binding_id")

    event_id = payload.get("event_id")
    if not isinstance(event_id, str) or not event_id.strip():
        event_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"pantheon:runtime-heartbeat:{runtime_binding_id}:{runtime_id}:{heartbeat_time}",
            )
        )

    metrics: dict[str, Any] = {"heartbeat": 1}
    if queue_lag_ms is not None:
        metrics["queue_lag_ms"] = queue_lag_ms
    if event_delivery_lag_ms is not None:
        metrics["event_delivery_lag_ms"] = event_delivery_lag_ms

    metadata = dict(payload.get("metadata")) if isinstance(payload.get("metadata"), dict) else {}
    runtime_heartbeat = {
        "connectivity_status": connectivity_status,
        "broker_status": broker_status,
        "queue_lag_ms": queue_lag_ms,
        "event_delivery_lag_ms": event_delivery_lag_ms,
        "health_summary": health_summary or {},
    }
    metadata.update(
        {
            "source_type": "runtime_heartbeat",
            "runtime_heartbeat": runtime_heartbeat,
            "connectivity_status": connectivity_status,
            "broker_status": broker_status,
        }
    )
    if queue_lag_ms is not None:
        metadata["queue_lag_ms"] = queue_lag_ms
    if event_delivery_lag_ms is not None:
        metadata["event_delivery_lag_ms"] = event_delivery_lag_ms
    if health_summary is not None:
        metadata["reported_health_summary"] = health_summary

    event = {
        "event_id": event_id.strip(),
        "event_type": "heartbeat",
        "created_at": heartbeat_time,
        "execution_mode": _execution_mode_for_stage(deployment_mode),
        "environment": deployment_mode,
        "deployment_stage": deployment_mode,
        "binding_id": runtime_binding_id,
        "runtime_id": runtime_id,
        "capital_pool_id": capital_pool_id,
        "artifact_id": artifact_id,
        "artifact_version": artifact_version,
        "plan_id": plan_id,
        "persona_capital_binding_id": persona_capital_binding_id,
        "target": _target(payload, artifact_id, artifact_version),
        "metrics": metrics,
        "metadata": metadata,
    }
    trace_id = payload.get("trace_id")
    if isinstance(trace_id, str) and trace_id.strip():
        event["trace_id"] = trace_id.strip()
    return event


def heartbeat_status_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Build the RuntimeHeartbeatStatus response from a runtime summary record."""
    last_heartbeat_at = summary.get("last_heartbeat_at")
    connectivity_status = summary.get("connectivity_status")
    if not last_heartbeat_at:
        connectivity_status = "disconnected"
    elif summary.get("state") == "degraded" and connectivity_status != "disconnected":
        connectivity_status = "degraded"
    elif connectivity_status not in CONNECTIVITY_STATUSES:
        connectivity_status = "connected"

    response = {
        "runtime_id": summary.get("runtime_id") or summary.get("id"),
        "runtime_binding_id": summary.get("runtime_binding_id") or summary.get("binding_id"),
        "capital_pool_id": summary.get("capital_pool_id"),
        "artifact_id": summary.get("artifact_id"),
        "artifact_version": summary.get("artifact_version"),
        "deployment_mode": summary.get("deployment_stage"),
        "deployment_stage": summary.get("deployment_stage"),
        "last_heartbeat_at": last_heartbeat_at,
        "status": connectivity_status,
        "connectivity_status": connectivity_status,
        "broker_status": summary.get("broker_status", "unavailable"),
        "queue_lag_ms": summary.get("queue_lag_ms"),
        "event_delivery_lag_ms": summary.get("event_delivery_lag_ms"),
        "health_summary": summary.get("health_summary") or {},
        "reported_health_summary": summary.get("reported_health_summary") or {},
    }
    if summary.get("staleness") is not None:
        response["staleness"] = summary["staleness"]
    return response
