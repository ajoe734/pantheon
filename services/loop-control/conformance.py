from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


CANONICAL_LOOP_IDS = (
    "source_ingestion",
    "strategy_distillation",
    "alpha_replication",
    "persona_teaching",
    "agora_interaction_evidence",
    "human_imitation_shadow_evaluation",
    "consultation",
    "promotion_deployment",
    "capital_pool_execution",
    "telemetry_reconciliation",
    "evolution",
    "bff_health_monitoring",
)

TRUTH_LEVELS = (
    "seed_fixture",
    "snapshot_fallback",
    "registry_metadata",
    "scheduled_tick",
    "reconciled_live_proof",
    "proven_live_evidence",
)
TRUTH_LEVEL_RANK = {level: rank for rank, level in enumerate(TRUTH_LEVELS)}

CONTROLLER_RECORD_FIELDS = (
    "loop_id",
    "tenant_id",
    "environment",
    "controller_id",
    "controller_name",
    "deployment_sha",
    "desired_state_query",
    "actual_state_query",
    "desired_state",
    "downstream_actual_state",
    "last_heartbeat_at",
    "last_tick_at",
    "last_success_at",
    "last_failure_at",
    "last_failure_reason",
    "last_repair_at",
    "last_repair_reason",
    "backlog",
    "lag",
    "dlq_count",
    "evidence_refs",
    "truth_level",
    "lease_token",
    "lease_expires_at",
    "payload",
)

_IDENTITY_FIELDS = (
    "loop_id",
    "tenant_id",
    "environment",
    "controller_id",
    "controller_name",
    "deployment_sha",
)
_TIMESTAMP_FIELDS = (
    "last_heartbeat_at",
    "last_tick_at",
    "last_success_at",
    "last_failure_at",
    "last_repair_at",
    "lease_expires_at",
)
_COUNTER_FIELDS = ("backlog", "lag", "dlq_count")


class ControllerRecordConformanceError(ValueError):
    """Raised when a controller row cannot satisfy the shared loop contract."""


def _is_timestamp(value: Any) -> bool:
    if isinstance(value, datetime):
        return value.tzinfo is not None
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def assert_controller_record_conforms(record: Mapping[str, Any]) -> None:
    """Validate semantic fields shared by every canonical loop controller.

    JSON Schema remains the wire-format authority.  This helper adds checks
    that are awkward to express in draft-07 and is intentionally reusable by
    each loop's own controller tests.
    """

    missing = [field for field in CONTROLLER_RECORD_FIELDS if field not in record]
    if missing:
        raise ControllerRecordConformanceError(
            f"controller record is missing required fields: {', '.join(missing)}"
        )

    for field in _IDENTITY_FIELDS:
        if not isinstance(record[field], str) or not record[field].strip():
            raise ControllerRecordConformanceError(
                f"controller record field {field!r} must be a non-empty string"
            )

    truth_level = record["truth_level"]
    if truth_level not in TRUTH_LEVEL_RANK:
        raise ControllerRecordConformanceError(
            f"unsupported controller truth_level {truth_level!r}"
        )

    for field in _TIMESTAMP_FIELDS:
        value = record[field]
        if value is not None and not _is_timestamp(value):
            raise ControllerRecordConformanceError(
                f"controller record field {field!r} must be a timezone-aware timestamp"
            )

    for field in _COUNTER_FIELDS:
        value = record[field]
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise ControllerRecordConformanceError(
                f"controller record field {field!r} must be a non-negative integer"
            )

    if not isinstance(record["evidence_refs"], list) or not all(
        isinstance(ref, str) and ref.strip() for ref in record["evidence_refs"]
    ):
        raise ControllerRecordConformanceError(
            "controller record evidence_refs must contain non-empty strings"
        )
    if not isinstance(record["payload"], Mapping):
        raise ControllerRecordConformanceError(
            "controller record payload must be an object"
        )

    lease_token = record["lease_token"]
    if record["lease_expires_at"] is not None and (
        not isinstance(lease_token, str) or not lease_token.strip()
    ):
        raise ControllerRecordConformanceError(
            "an expiring controller lease requires a non-empty fencing token"
        )

    desired_state = record["desired_state"]
    if desired_state is not None:
        if not isinstance(desired_state, Mapping):
            raise ControllerRecordConformanceError(
                "desired_state must be null or an object"
            )
        if not isinstance(desired_state.get("present"), bool):
            raise ControllerRecordConformanceError(
                "desired_state.present must be a boolean observation"
            )
        if not str(desired_state.get("source") or "").strip():
            raise ControllerRecordConformanceError(
                "desired_state.source must identify the domain authority"
            )
        if not _is_timestamp(desired_state.get("checked_at")):
            raise ControllerRecordConformanceError(
                "desired_state.checked_at must be a timezone-aware timestamp"
            )

    actual_state = record["downstream_actual_state"]
    if actual_state is not None:
        if not isinstance(actual_state, Mapping):
            raise ControllerRecordConformanceError(
                "downstream_actual_state must be null or an object"
            )
        if not str(actual_state.get("status") or "").strip():
            raise ControllerRecordConformanceError(
                "downstream_actual_state.status must be non-empty"
            )
        if not str(actual_state.get("source") or "").strip():
            raise ControllerRecordConformanceError(
                "downstream_actual_state.source must identify the domain authority"
            )
        if not _is_timestamp(actual_state.get("checked_at")):
            raise ControllerRecordConformanceError(
                "downstream_actual_state.checked_at must be a timezone-aware timestamp"
            )
