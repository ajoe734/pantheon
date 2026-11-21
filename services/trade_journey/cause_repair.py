"""Bounded historical cause recovery for paper Trade Journey projections.

Recovers rejection and reconciliation failure causes on existing
``trade_journey_projection.journey_stages`` rows from immutable source evidence:

* ``public.telemetry_events`` joined by ``(tenant_id, environment, journey_id)``
  and exact ``source_event_id``, ``source_fill_event_id`` / ``fill_id``, and
  ``causal_parent_id`` (``position_snapshot``).
* ``reconciliation_drift.drift_evaluations`` joined by ``(tenant_id,
  reconciliation_evaluation_id)`` using the owner store's deterministic key.
* Optional read-only paper broker order records joined by ``broker_order_id``.

Invariants:
* Never mutates ``public.telemetry_events``, ``public.trade_journey_action_ledger``,
  ``reconciliation_drift.*``, ledger tables, or paper broker orders.
* Never replays trading side effects.
* Protects concurrently updated stage/journey projection revisions via optimistic
  revision + fingerprint guards.
* Saves a bounded, scoped before-image file prior to applying batch updates so
  any applied batch can be rolled back without clobbering subsequent live
  projector updates.
* Repeat runs are idempotent and modify zero already-repaired rows.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Protocol, Sequence

from services.trade_journey.lifecycle_projector import (
    InvalidLifecycleEvent,
    LifecycleProjector,
)

REPAIR_SCHEMA_VERSION = "pantheon.trade-journey-historical-cause-repair.v1"
DEFAULT_TENANT_ID = "tenant-dev"
DEFAULT_ENVIRONMENT = "paper"
DEFAULT_PROJECTION_SCHEMA = "trade_journey_projection"
DEFAULT_SOURCE_SCHEMA = "public"
DEFAULT_SOURCE_TABLE = "telemetry_events"
DEFAULT_EVALUATION_SCHEMA = "reconciliation_drift"
DEFAULT_EVALUATION_TABLE = "drift_evaluations"

DEFAULT_BATCH_SIZE = 500
MAX_BATCH_SIZE = 2000
DEFAULT_MAX_BATCHES = 100
DEFAULT_TEMP_DIR = Path("/tmp/pantheon-journey-cause-repair")
BEFORE_IMAGE_FILENAME = "paper_journey_cause_repair_before_image.jsonl"
CURSOR_FILENAME = "paper_journey_cause_repair_cursor.json"
PREVIEW_CURSOR_FILENAME = "paper_journey_cause_repair_preview_cursor.json"
DEFAULT_MAX_BEFORE_IMAGE_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_BEFORE_IMAGE_RECORDS = 25000
DEFAULT_TEMP_RETENTION_SECONDS = 7 * 86400

_EXPRESSION_REASON_CODES = frozenset(
    {"long_cash", "long_lever", "short_via_future", "close_leg"}
)
_RUNTIME_HEALTH_CHECKS = frozenset(
    {
        "runtime_state",
        "runtime_health_summary",
        "queue_lag_ms",
        "event_delivery_lag_ms",
        "actual_metrics_presence",
    }
)
_TRADE_COMPARISON_CHECKS = frozenset(
    {
        "broker_record",
        "broker_fill_qty",
        "broker_fill_price",
        "broker_status",
        "broker_side",
        "broker_instrument_id",
        "position_change",
        "committed_snapshot_position",
        "cash_change",
        "committed_snapshot_cash",
    }
)
_NUMERIC_COMPARISON_CHECKS = frozenset(
    {
        "broker_fill_qty",
        "broker_fill_price",
        "position_change",
        "committed_snapshot_position",
        "cash_change",
        "committed_snapshot_cash",
    }
)
_ACCOUNTING_VARIANCE_REASONS = frozenset(
    {
        "cash_variance",
        "position_variance",
        "broker_fill_mismatch",
        "broker_record_missing",
    }
)
_BOOKING_TOLERANCE = 1e-6


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_str(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _first_str(*values: Any) -> str:
    for value in values:
        text = _clean_str(value)
        if text:
            return text
    return ""


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _first_num(*values: Any) -> float | None:
    for value in values:
        parsed = _num(value)
        if parsed is not None:
            return parsed
    return None


def evaluation_storage_record_id(tenant_id: str, evaluation_id: str) -> str:
    """Compute the deterministic key used by reconciliation_drift.drift_evaluations."""
    clean_tenant = _clean_str(tenant_id)
    clean_eval = _clean_str(evaluation_id)
    if not clean_tenant:
        return clean_eval
    digest = hashlib.sha256(
        f"{clean_tenant}\0{clean_eval}".encode("utf-8")
    ).hexdigest()
    return f"tenant-record-{digest}"


@dataclass(frozen=True)
class CandidateStageRow:
    tenant_id: str
    environment: str
    journey_id: str
    source_event_id: str
    stage_name: str
    stage_status: str
    stage_ordinal: int
    source_ingested_seq: int
    event_sequence: int
    occurred_at_iso: str
    recorded_at_iso: str
    contract_fields: dict[str, Any]
    evidence_references: list[Any]
    projection_revision: int
    fingerprint: str
    journey_status: str
    journey_projection_revision: int

    @property
    def cursor_key(self) -> tuple[str, str, str]:
        return (self.journey_id, self.source_event_id, self.stage_name)


@dataclass(frozen=True)
class StageRepairDecision:
    row: CandidateStageRow
    classification: str  # recoverable_refusals | health_only_evaluations | proven_variances | irrecoverable_records
    repaired_contract_fields: dict[str, Any]
    needs_update: bool
    reason_code: str
    summary_note: str = ""


@dataclass
class RepairRunReport:
    schema_version: str = REPAIR_SCHEMA_VERSION
    mode: str = "dry_run"
    tenant_id: str = DEFAULT_TENANT_ID
    environment: str = DEFAULT_ENVIRONMENT
    batch_size: int = DEFAULT_BATCH_SIZE
    max_batches: int = DEFAULT_MAX_BATCHES
    batches_processed: int = 0
    scanned_rows: int = 0
    recoverable_refusals: int = 0
    health_only_evaluations: int = 0
    proven_variances: int = 0
    irrecoverable_records: int = 0
    needs_update_rows: int = 0
    already_repaired_rows: int = 0
    updated_rows: int = 0
    rolled_back_rows: int = 0
    skipped_concurrent_revision: int = 0
    before_image_path: str | None = None
    before_image_records: int = 0
    before_image_bytes: int = 0
    cursor_path: str | None = None
    last_after_key: list[str] | None = None
    has_more: bool = False
    temp_files_cleaned: int = 0
    temp_bytes_freed: int = 0
    notable_journeys: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "tenant_id": self.tenant_id,
            "environment": self.environment,
            "batch_size": self.batch_size,
            "max_batches": self.max_batches,
            "batches_processed": self.batches_processed,
            "scanned_rows": self.scanned_rows,
            "counts": {
                "recoverable_refusals": self.recoverable_refusals,
                "health_only_evaluations": self.health_only_evaluations,
                "proven_variances": self.proven_variances,
                "irrecoverable_records": self.irrecoverable_records,
            },
            "needs_update_rows": self.needs_update_rows,
            "already_repaired_rows": self.already_repaired_rows,
            "updated_rows": self.updated_rows,
            "rolled_back_rows": self.rolled_back_rows,
            "skipped_concurrent_revision": self.skipped_concurrent_revision,
            "before_image": {
                "path": self.before_image_path,
                "records": self.before_image_records,
                "bytes": self.before_image_bytes,
                "max_bytes_cap": DEFAULT_MAX_BEFORE_IMAGE_BYTES,
                "max_records_cap": DEFAULT_MAX_BEFORE_IMAGE_RECORDS,
                "retention_seconds": DEFAULT_TEMP_RETENTION_SECONDS,
            },
            "cursor": {
                "path": self.cursor_path,
                "last_after_key": self.last_after_key,
                "has_more": self.has_more,
            },
            "cleanup": {
                "temp_files_cleaned": self.temp_files_cleaned,
                "temp_bytes_freed": self.temp_bytes_freed,
            },
            "notable_journeys": self.notable_journeys,
        }


class CauseRepairBackend(Protocol):
    """Storage abstraction for bounded read-only inventory, apply, and rollback."""

    def fetch_candidate_batch(
        self,
        *,
        tenant_id: str,
        environment: str,
        batch_size: int,
        after_key: tuple[str, str, str] | None = None,
        journey_ids: Sequence[str] | None = None,
    ) -> list[CandidateStageRow]: ...

    def fetch_telemetry_events(
        self,
        *,
        event_ids: Sequence[str],
    ) -> dict[str, dict[str, Any]]: ...

    def fetch_journey_companion_stages(
        self,
        *,
        tenant_id: str,
        environment: str,
        journey_ids: Sequence[str],
    ) -> dict[tuple[str, str], dict[str, Any]]: ...

    def fetch_drift_evaluations(
        self,
        *,
        tenant_id: str,
        evaluation_ids: Sequence[str],
    ) -> dict[str, dict[str, Any]]: ...

    def apply_stage_update(
        self,
        *,
        row: CandidateStageRow,
        repaired_contract_fields: Mapping[str, Any],
    ) -> int | None: ...

    def rollback_stage_update(
        self,
        *,
        record: Mapping[str, Any],
    ) -> int | None: ...


def _event_scope(raw_row: Mapping[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    """Extract (tenant_id, environment, journey_id, payload_dict) from a telemetry row."""
    raw_payload = raw_row.get("payload")
    if isinstance(raw_payload, str):
        try:
            payload = json.loads(raw_payload)
        except ValueError:
            payload = {}
    elif isinstance(raw_payload, Mapping):
        payload = dict(raw_payload)
    else:
        payload = dict(raw_row)
    for top_field in ("event_id", "event_type", "created_at"):
        if payload.get(top_field) in (None, "") and raw_row.get(top_field) not in (None, ""):
            payload[top_field] = raw_row[top_field]
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    env_map = (
        payload.get("correlation_envelope")
        if isinstance(payload.get("correlation_envelope"), Mapping)
        else (
            meta.get("correlation_envelope")
            if isinstance(meta.get("correlation_envelope"), Mapping)
            else {}
        )
    )
    tenant_id = _first_str(
        env_map.get("tenant_id"),
        payload.get("tenant_id"),
        meta.get("tenant_id"),
    )
    environment = _first_str(
        env_map.get("environment"),
        payload.get("deployment_stage"),
        payload.get("environment"),
        meta.get("environment"),
    )
    journey_id = _first_str(
        env_map.get("journey_id"),
        payload.get("journey_id"),
        meta.get("journey_id"),
        payload.get("aggregate_id"),
    )
    return tenant_id, environment, journey_id, payload


def _verify_scoped_telemetry(
    raw_row: Mapping[str, Any] | None,
    *,
    tenant_id: str,
    environment: str,
    journey_id: str,
    expected_event_id: str,
) -> dict[str, Any] | None:
    if not isinstance(raw_row, Mapping):
        return None
    ev_tenant, ev_env, ev_journey, payload = _event_scope(raw_row)
    ev_id = _first_str(payload.get("event_id"), raw_row.get("event_id"))
    if ev_id != expected_event_id:
        return None
    if ev_tenant != tenant_id or ev_env != environment or ev_journey != journey_id:
        return None
    return payload


def _derive_order_refusal_repair(
    row: CandidateStageRow,
    *,
    source_payload: Mapping[str, Any] | None,
) -> StageRepairDecision:
    updated = dict(row.contract_fields)
    updated["source_event_id"] = row.source_event_id
    existing_reason = _clean_str(updated.get("reason_code"))
    if existing_reason in _EXPRESSION_REASON_CODES and not _clean_str(
        updated.get("expression_reason_code")
    ):
        updated["expression_reason_code"] = existing_reason

    if source_payload is None:
        updated["reason_code"] = "reason_not_recorded"
        updated["missing_evidence"] = ["source_telemetry_event", "reject_reason"]
        needs_update = updated != row.contract_fields
        return StageRepairDecision(
            row=row,
            classification="irrecoverable_records",
            repaired_contract_fields=updated,
            needs_update=needs_update,
            reason_code="reason_not_recorded",
            summary_note="missing_source_telemetry_event",
        )

    try:
        source_event = LifecycleProjector._source_event(
            {
                "event_id": row.source_event_id,
                "event_type": source_payload.get("event_type")
                or updated.get("event_type")
                or "order_rejection",
                "created_at": source_payload.get("created_at")
                or row.occurred_at_iso
                or _utc_now_iso(),
                "payload": dict(source_payload),
            }
        )
        try:
            identity = LifecycleProjector._identity(source_event)
        except Exception:
            identity = {
                "journey_id": row.journey_id,
                "tenant_id": row.tenant_id,
                "environment": row.environment,
            }
        if (
            identity.get("tenant_id") != row.tenant_id
            or identity.get("environment") != row.environment
            or identity.get("journey_id") != row.journey_id
        ):
            raise InvalidLifecycleEvent("source identity scope mismatch")
        try:
            sequence_no = LifecycleProjector._sequence_no(source_event)
        except Exception:
            sequence_no = max(1, int(row.event_sequence or 1))
        derived_events = LifecycleProjector._journey_events(
            {
                "event": source_event,
                "identity": identity,
                "source_mode": _first_str(
                    updated.get("source_mode"),
                    source_event.get("source_mode"),
                    (source_event.get("metadata") or {}).get("source_mode"),
                    "live",
                ),
                "accepted_live": bool(updated.get("accepted_live", True)),
                "ingested_seq": row.source_ingested_seq,
                "ingested_at": row.recorded_at_iso,
                "sequence_no": sequence_no,
            }
        )
        derived = next(
            (ev for ev in derived_events if ev.get("stage") == row.stage_name),
            derived_events[0] if derived_events else {},
        )
    except Exception:
        derived = {}

    if derived:
        for key in (
            "source_event_id",
            "reason_code",
            "expression_reason_code",
            "reason_detail",
            "submitted_to_broker",
            "order_status",
            "risk_status",
            "risk_gate_evaluated",
            "requested_quantity",
            "holding_quantity",
            "quantity",
            "price",
        ):
            if key in derived and derived[key] is not None:
                updated[key] = derived[key]
        if "missing_evidence" in derived and derived["missing_evidence"]:
            updated["missing_evidence"] = list(derived["missing_evidence"])
        else:
            updated.pop("missing_evidence", None)
    else:
        updated["reason_code"] = "reason_not_recorded"
        updated["missing_evidence"] = ["reject_reason"]

    reason_code = _clean_str(updated.get("reason_code")) or "reason_not_recorded"
    if (
        reason_code != "reason_not_recorded"
        and reason_code not in _EXPRESSION_REASON_CODES
    ):
        classification = "recoverable_refusals"
    else:
        classification = "irrecoverable_records"
        updated["reason_code"] = "reason_not_recorded"
        if not updated.get("missing_evidence"):
            updated["missing_evidence"] = ["reject_reason"]

    needs_update = updated != row.contract_fields
    return StageRepairDecision(
        row=row,
        classification=classification,
        repaired_contract_fields=updated,
        needs_update=needs_update,
        reason_code=str(updated["reason_code"]),
    )


def _booking_check(
    check: str,
    expected: float | None,
    actual: float | None,
    *,
    missing_fields: Sequence[str] = (),
) -> dict[str, Any]:
    if expected is None or actual is None:
        missing_sides: list[str] = []
        for field_name in missing_fields:
            if field_name and field_name not in missing_sides:
                missing_sides.append(field_name)
        if expected is None and not missing_fields:
            missing_sides.append("expected")
        if actual is None and "actual" not in missing_sides:
            missing_sides.append("actual")
        return {
            "check": check,
            "status": "unverified",
            "expected": expected,
            "actual": actual,
            "difference": None,
            "delta": None,
            "missing_evidence": missing_sides,
        }
    diff = actual - expected
    return {
        "check": check,
        "status": "passed" if abs(diff) <= _BOOKING_TOLERANCE else "failed",
        "expected": expected,
        "actual": actual,
        "difference": diff,
        "delta": diff,
    }


def _as_companion_stage_list(entry: Any) -> list[dict[str, Any]]:
    if isinstance(entry, Mapping):
        return [dict(entry)]
    if isinstance(entry, Sequence) and not isinstance(entry, (str, bytes)):
        return [dict(item) for item in entry if isinstance(item, Mapping)]
    return []


def _companion_stage_event_id(stage: Mapping[str, Any] | None) -> str:
    if not isinstance(stage, Mapping):
        return ""
    cf = (
        stage.get("contract_fields")
        if isinstance(stage.get("contract_fields"), Mapping)
        else {}
    )
    return _first_str(
        stage.get("source_event_id"),
        cf.get("source_event_id"),
        cf.get("canonical_event_id"),
    )


def _companion_matches_snapshot_id(
    stage: Mapping[str, Any] | None,
    *,
    ref_snap_eid: str,
) -> bool:
    if not isinstance(stage, Mapping) or not ref_snap_eid:
        return False
    comp_eid = _companion_stage_event_id(stage)
    return bool(comp_eid and comp_eid == ref_snap_eid)


def _companion_references_fill(
    stage: Mapping[str, Any] | None,
    *,
    ref_fill_eid: str,
    ref_fill_id: str,
) -> tuple[bool, bool]:
    """Return (is_same_fill_reference, has_id_conflict)."""
    if not isinstance(stage, Mapping) or (not ref_fill_eid and not ref_fill_id):
        return False, False
    cf = (
        stage.get("contract_fields")
        if isinstance(stage.get("contract_fields"), Mapping)
        else {}
    )
    comp_eid = _companion_stage_event_id(stage)
    comp_fid = _clean_str(cf.get("fill_id"))
    comp_inferred_eid = (
        comp_fid[len("paper-fill-") :]
        if comp_fid.startswith("paper-fill-")
        else ""
    )
    ref_inferred_eid = (
        ref_fill_id[len("paper-fill-") :]
        if ref_fill_id.startswith("paper-fill-")
        else ""
    )
    effective_ref_eid = ref_fill_eid or ref_inferred_eid

    matches_eid = bool(
        effective_ref_eid
        and (
            (comp_eid and comp_eid == effective_ref_eid)
            or (comp_inferred_eid and comp_inferred_eid == effective_ref_eid)
        )
    )
    matches_fid = bool(ref_fill_id and comp_fid and comp_fid == ref_fill_id)
    if not matches_eid and not matches_fid:
        return False, False

    conflict = False
    if effective_ref_eid and comp_eid and comp_eid != effective_ref_eid:
        conflict = True
    if effective_ref_eid and comp_inferred_eid and comp_inferred_eid != effective_ref_eid:
        conflict = True
    if ref_fill_id and comp_fid and comp_fid != ref_fill_id:
        conflict = True
    if comp_eid and comp_inferred_eid and comp_eid != comp_inferred_eid:
        conflict = True
    return True, conflict


def _consistent_str_field(
    field_name: str,
    candidates: Sequence[tuple[str, Any]],
    conflicts: list[str],
    *,
    case_lower: bool = False,
) -> str:
    values: list[str] = []
    for _label, raw_val in candidates:
        cleaned = _clean_str(raw_val)
        if not cleaned:
            continue
        if case_lower:
            cleaned = cleaned.lower()
        if cleaned not in values:
            values.append(cleaned)
    if len(values) > 1:
        if field_name not in conflicts:
            conflicts.append(field_name)
        return ""
    return values[0] if values else ""


def _consistent_num_field(
    field_name: str,
    candidates: Sequence[tuple[str, Any]],
    conflicts: list[str],
    *,
    abs_nonzero: bool = False,
    positive_only: bool = False,
) -> float | None:
    values: list[float] = []
    for _label, raw_val in candidates:
        parsed = _num(raw_val)
        if parsed is None:
            continue
        if abs_nonzero and parsed != 0.0:
            parsed = abs(parsed)
        if positive_only and parsed <= 0.0:
            continue
        if not any(abs(parsed - existing) <= _BOOKING_TOLERANCE for existing in values):
            values.append(parsed)
    if len(values) > 1:
        if field_name not in conflicts:
            conflicts.append(field_name)
        return None
    return values[0] if values else None


def _extract_fill_and_snapshot_evidence(
    *,
    row: CandidateStageRow,
    source_payload: Mapping[str, Any] | None,
    fill_stage: Mapping[str, Any] | None,
    fill_payload: Mapping[str, Any] | None,
    snapshot_stage: Mapping[str, Any] | None,
    snapshot_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    src = source_payload or {}
    src_meta = src.get("metadata") if isinstance(src.get("metadata"), Mapping) else {}
    src_metrics = src.get("metrics") if isinstance(src.get("metrics"), Mapping) else {}
    src_env = (
        src.get("correlation_envelope")
        if isinstance(src.get("correlation_envelope"), Mapping)
        else (
            src_meta.get("correlation_envelope")
            if isinstance(src_meta.get("correlation_envelope"), Mapping)
            else {}
        )
    )

    conflicts: list[str] = []
    has_identity_conflict = False

    def add_conflict(name: str, *, identity: bool = False) -> None:
        nonlocal has_identity_conflict
        if identity:
            has_identity_conflict = True
        if name and name not in conflicts:
            conflicts.append(name)

    src_fill_eid = _first_str(
        src.get("source_fill_event_id"),
        src_meta.get("source_fill_event_id"),
    )
    row_fill_eid = _clean_str(row.contract_fields.get("source_fill_event_id"))
    src_fill_id = _first_str(
        src.get("fill_id"),
        src_meta.get("fill_id"),
    )
    row_fill_id = _clean_str(row.contract_fields.get("fill_id"))
    src_snap_eid = _first_str(
        src.get("causal_parent_id"),
        src_meta.get("causal_parent_id"),
        src_env.get("causation_event_id"),
        src.get("snapshot_event_id"),
        src_meta.get("snapshot_event_id"),
    )
    row_snap_eid = _clean_str(row.contract_fields.get("snapshot_event_id"))
    src_broker_oid = _first_str(
        src.get("broker_order_id"),
        src_meta.get("broker_order_id"),
    )
    row_broker_oid = _clean_str(row.contract_fields.get("broker_order_id"))

    if src_fill_eid and src_fill_id.startswith("paper-fill-"):
        inferred_src_eid = src_fill_id[len("paper-fill-") :]
        if inferred_src_eid and inferred_src_eid != src_fill_eid:
            add_conflict("source_fill_event_id", identity=True)
            add_conflict("fill_id", identity=True)

    ref_fill_eid = "" if has_identity_conflict else src_fill_eid
    ref_fill_id = "" if has_identity_conflict else src_fill_id
    if not has_identity_conflict:
        if not ref_fill_eid and ref_fill_id.startswith("paper-fill-"):
            ref_fill_eid = ref_fill_id[len("paper-fill-") :]
        if not ref_fill_eid and row_fill_eid:
            if not (
                ref_fill_id.startswith("paper-fill-")
                and ref_fill_id[len("paper-fill-") :] != row_fill_eid
            ):
                ref_fill_eid = row_fill_eid
        if not ref_fill_id and row_fill_id:
            if not (
                ref_fill_eid
                and row_fill_id.startswith("paper-fill-")
                and row_fill_id[len("paper-fill-") :] != ref_fill_eid
            ):
                ref_fill_id = row_fill_id
        if not ref_fill_eid and ref_fill_id.startswith("paper-fill-"):
            ref_fill_eid = ref_fill_id[len("paper-fill-") :]

    ref_snap_eid = src_snap_eid or row_snap_eid
    ref_broker_oid = src_broker_oid or row_broker_oid

    # Validate snapshot_payload and snapshot_stage by exact snapshot event ID
    if isinstance(snapshot_payload, Mapping):
        sp_eid = _clean_str(snapshot_payload.get("event_id"))
        if not ref_snap_eid or (sp_eid and sp_eid != ref_snap_eid):
            snapshot_payload = None
    else:
        snapshot_payload = None

    if not _companion_matches_snapshot_id(snapshot_stage, ref_snap_eid=ref_snap_eid):
        snapshot_stage = None

    s_pay = snapshot_payload or {}
    s_meta = s_pay.get("metadata") if isinstance(s_pay.get("metadata"), Mapping) else {}
    s_metrics = s_pay.get("metrics") if isinstance(s_pay.get("metrics"), Mapping) else {}
    s_env = (
        s_pay.get("correlation_envelope")
        if isinstance(s_pay.get("correlation_envelope"), Mapping)
        else (
            s_meta.get("correlation_envelope")
            if isinstance(s_meta.get("correlation_envelope"), Mapping)
            else {}
        )
    )
    if snapshot_payload is not None:
        s_fill_eid = _first_str(
            s_pay.get("source_fill_event_id"),
            s_meta.get("source_fill_event_id"),
            s_pay.get("causal_parent_id"),
            s_meta.get("causal_parent_id"),
            s_env.get("causation_event_id"),
        )
        s_fill_id = _first_str(s_pay.get("fill_id"), s_meta.get("fill_id"))
        s_broker_oid = _first_str(
            s_pay.get("broker_order_id"),
            s_meta.get("broker_order_id"),
        )
        if s_fill_eid and s_fill_id.startswith("paper-fill-"):
            s_inferred = s_fill_id[len("paper-fill-") :]
            if s_inferred and s_inferred != s_fill_eid:
                add_conflict("fill_id", identity=True)
        if ref_fill_eid and s_fill_eid and s_fill_eid != ref_fill_eid:
            add_conflict("source_fill_event_id", identity=True)
        if ref_fill_id and s_fill_id and s_fill_id != ref_fill_id:
            add_conflict("fill_id", identity=True)
        if ref_broker_oid and s_broker_oid and s_broker_oid != ref_broker_oid:
            add_conflict("broker_order_id", identity=True)
        if not has_identity_conflict:
            if not ref_fill_eid:
                ref_fill_eid = s_fill_eid or (
                    s_fill_id[len("paper-fill-") :]
                    if s_fill_id.startswith("paper-fill-")
                    else ""
                )
            if not ref_fill_id and s_fill_id:
                ref_fill_id = s_fill_id
            if not ref_broker_oid and s_broker_oid:
                ref_broker_oid = s_broker_oid

    s_stage_fields = (
        snapshot_stage.get("contract_fields")
        if isinstance(snapshot_stage, Mapping)
        and isinstance(snapshot_stage.get("contract_fields"), Mapping)
        else {}
    )
    if snapshot_stage is not None:
        ss_fill_eid = _clean_str(s_stage_fields.get("source_fill_event_id"))
        ss_fill_id = _clean_str(s_stage_fields.get("fill_id"))
        ss_broker_oid = _clean_str(s_stage_fields.get("broker_order_id"))
        if (
            (ref_fill_eid and ss_fill_eid and ss_fill_eid != ref_fill_eid)
            or (ref_fill_id and ss_fill_id and ss_fill_id != ref_fill_id)
            or (ref_broker_oid and ss_broker_oid and ss_broker_oid != ref_broker_oid)
        ):
            add_conflict("snapshot_stage_identity", identity=True)
            snapshot_stage = None
            s_stage_fields = {}
        elif not has_identity_conflict:
            if not ref_fill_eid:
                ref_fill_eid = ss_fill_eid or (
                    ss_fill_id[len("paper-fill-") :]
                    if ss_fill_id.startswith("paper-fill-")
                    else ""
                )
            if not ref_fill_id and ss_fill_id:
                ref_fill_id = ss_fill_id
            if not ref_broker_oid and ss_broker_oid:
                ref_broker_oid = ss_broker_oid

    # Validate fill_payload and fill_stage by exact referenced fill IDs
    if isinstance(fill_payload, Mapping):
        fp_eid = _clean_str(fill_payload.get("event_id"))
        if not ref_fill_eid or (fp_eid and fp_eid != ref_fill_eid):
            fill_payload = None
    else:
        fill_payload = None

    if fill_stage is not None:
        same_fill, fill_id_conflict = _companion_references_fill(
            fill_stage,
            ref_fill_eid=ref_fill_eid,
            ref_fill_id=ref_fill_id,
        )
        if not same_fill:
            # Unrelated companion stage from another fill in the same journey: ignore it
            fill_stage = None
        elif fill_id_conflict:
            add_conflict("fill_stage_identity", identity=True)
            fill_stage = None

    f_pay = fill_payload or {}
    f_meta = f_pay.get("metadata") if isinstance(f_pay.get("metadata"), Mapping) else {}
    f_metrics = f_pay.get("metrics") if isinstance(f_pay.get("metrics"), Mapping) else {}
    if fill_payload is not None:
        f_fill_id = _first_str(f_pay.get("fill_id"), f_meta.get("fill_id"))
        f_broker_oid = _first_str(
            f_pay.get("broker_order_id"),
            f_meta.get("broker_order_id"),
        )
        if ref_fill_id and f_fill_id and f_fill_id != ref_fill_id:
            add_conflict("fill_id", identity=True)
        if ref_broker_oid and f_broker_oid and f_broker_oid != ref_broker_oid:
            add_conflict("broker_order_id", identity=True)
        if not has_identity_conflict:
            if not ref_fill_id and f_fill_id:
                ref_fill_id = f_fill_id
            if not ref_broker_oid and f_broker_oid:
                ref_broker_oid = f_broker_oid

    f_stage_fields = (
        fill_stage.get("contract_fields")
        if isinstance(fill_stage, Mapping)
        and isinstance(fill_stage.get("contract_fields"), Mapping)
        else {}
    )
    if fill_stage is not None:
        fs_broker_oid = _clean_str(f_stage_fields.get("broker_order_id"))
        if ref_broker_oid and fs_broker_oid and fs_broker_oid != ref_broker_oid:
            add_conflict("broker_order_id", identity=True)
            fill_stage = None
            f_stage_fields = {}
        elif not has_identity_conflict and not ref_broker_oid and fs_broker_oid:
            ref_broker_oid = fs_broker_oid

    missing_referenced_fill = bool(
        (ref_fill_eid or ref_fill_id) and fill_payload is None and fill_stage is None
    )
    missing_referenced_snapshot = bool(
        ref_snap_eid and snapshot_payload is None and snapshot_stage is None
    )

    if has_identity_conflict:
        f_pay = {}
        f_meta = {}
        f_metrics = {}
        f_stage_fields = {}
        s_pay = {}
        s_meta = {}
        s_metrics = {}
        s_stage_fields = {}

    fill_event_id = "" if "source_fill_event_id" in conflicts else ref_fill_eid
    fill_id = "" if "fill_id" in conflicts else ref_fill_id
    snapshot_event_id = ref_snap_eid
    broker_order_id = "" if "broker_order_id" in conflicts else ref_broker_oid

    fill_qty = _consistent_num_field(
        "fill_quantity",
        [
            (
                "fill_payload",
                _first_num(
                    f_metrics.get("fill_quantity"),
                    f_pay.get("fill_quantity"),
                    f_meta.get("fill_quantity"),
                    f_pay.get("quantity"),
                ),
            ),
            (
                "fill_stage",
                _first_num(
                    f_stage_fields.get("fill_quantity"),
                    f_stage_fields.get("quantity"),
                ),
            ),
            (
                "snapshot_payload",
                _first_num(s_meta.get("fill_quantity"), s_pay.get("fill_quantity")),
            ),
            ("snapshot_stage", _num(s_stage_fields.get("fill_quantity"))),
            (
                "source_payload",
                _first_num(
                    src_meta.get("fill_quantity"),
                    src_metrics.get("fill_quantity"),
                    src.get("fill_quantity"),
                ),
            ),
        ],
        conflicts,
        abs_nonzero=True,
        positive_only=True,
    )
    fill_price = _consistent_num_field(
        "fill_price",
        [
            (
                "fill_payload",
                _first_num(
                    f_metrics.get("fill_price"),
                    f_pay.get("fill_price"),
                    f_meta.get("fill_price"),
                    f_pay.get("price"),
                ),
            ),
            (
                "fill_stage",
                _first_num(
                    f_stage_fields.get("fill_price"),
                    f_stage_fields.get("price"),
                ),
            ),
            (
                "snapshot_payload",
                _first_num(s_meta.get("fill_price"), s_pay.get("fill_price")),
            ),
            ("snapshot_stage", _num(s_stage_fields.get("fill_price"))),
            (
                "source_payload",
                _first_num(
                    src_meta.get("fill_price"),
                    src_metrics.get("fill_price"),
                    src.get("fill_price"),
                ),
            ),
        ],
        conflicts,
    )
    side = _consistent_str_field(
        "side",
        [
            ("fill_payload", _first_str(f_pay.get("side"), f_meta.get("side"))),
            ("fill_stage", f_stage_fields.get("side")),
            ("snapshot_payload", _first_str(s_pay.get("side"), s_meta.get("side"))),
            ("snapshot_stage", s_stage_fields.get("side")),
            (
                "source_payload",
                _first_str(
                    src.get("side"),
                    src_meta.get("side"),
                    row.contract_fields.get("side"),
                ),
            ),
        ],
        conflicts,
        case_lower=True,
    )
    symbol = _consistent_str_field(
        "symbol",
        [
            ("fill_payload", _first_str(f_pay.get("symbol"), f_meta.get("symbol"))),
            ("fill_stage", f_stage_fields.get("symbol")),
            ("snapshot_payload", _first_str(s_pay.get("symbol"), s_meta.get("symbol"))),
            ("snapshot_stage", s_stage_fields.get("symbol")),
            (
                "source_payload",
                _first_str(
                    src.get("symbol"),
                    src_meta.get("symbol"),
                    row.contract_fields.get("symbol"),
                ),
            ),
        ],
        conflicts,
    )
    instrument_id = _consistent_str_field(
        "instrument_id",
        [
            (
                "fill_payload",
                _first_str(f_pay.get("instrument_id"), f_meta.get("instrument_id")),
            ),
            (
                "snapshot_payload",
                _first_str(s_pay.get("instrument_id"), s_meta.get("instrument_id")),
            ),
            (
                "source_payload",
                _first_str(src.get("instrument_id"), src_meta.get("instrument_id")),
            ),
        ],
        conflicts,
    )
    kind = (
        _consistent_str_field(
            "kind",
            [
                ("fill_payload", _first_str(f_pay.get("kind"), f_meta.get("kind"))),
                ("snapshot_payload", _first_str(s_pay.get("kind"), s_meta.get("kind"))),
                ("source_payload", _first_str(src.get("kind"), src_meta.get("kind"))),
            ],
            conflicts,
        )
        or "cash_share"
    )
    has_explicit_multiplier = any(
        "multiplier" in container
        for container in (f_pay, f_meta, s_pay, s_meta, src, src_meta)
    )
    if not has_explicit_multiplier:
        multiplier: float | None = 1.0
    else:
        raw_mult = _consistent_num_field(
            "multiplier",
            [
                (
                    "fill_payload",
                    _first_num(f_pay.get("multiplier"), f_meta.get("multiplier")),
                ),
                (
                    "snapshot_payload",
                    _first_num(s_pay.get("multiplier"), s_meta.get("multiplier")),
                ),
                (
                    "source_payload",
                    _first_num(src.get("multiplier"), src_meta.get("multiplier")),
                ),
            ],
            conflicts,
        )
        multiplier = raw_mult if (raw_mult is not None and raw_mult > 0.0) else None

    fee = _consistent_num_field(
        "fee",
        [
            (
                "fill_payload",
                _first_num(f_pay.get("fee"), f_meta.get("fee"), f_metrics.get("fee")),
            ),
            (
                "snapshot_payload",
                _first_num(s_pay.get("fee"), s_meta.get("fee"), s_metrics.get("fee")),
            ),
            (
                "source_payload",
                _first_num(src.get("fee"), src_meta.get("fee"), src_metrics.get("fee")),
            ),
        ],
        conflicts,
    )
    tax = _consistent_num_field(
        "tax",
        [
            (
                "fill_payload",
                _first_num(f_pay.get("tax"), f_meta.get("tax"), f_metrics.get("tax")),
            ),
            (
                "snapshot_payload",
                _first_num(s_pay.get("tax"), s_meta.get("tax"), s_metrics.get("tax")),
            ),
            (
                "source_payload",
                _first_num(src.get("tax"), src_meta.get("tax"), src_metrics.get("tax")),
            ),
        ],
        conflicts,
    )
    pos_before = _consistent_num_field(
        "position_quantity_before",
        [
            (
                "snapshot_payload",
                _first_num(
                    s_pay.get("position_quantity_before"),
                    s_meta.get("position_quantity_before"),
                ),
            ),
            (
                "fill_payload",
                _first_num(
                    f_pay.get("position_quantity_before"),
                    f_meta.get("position_quantity_before"),
                ),
            ),
            (
                "source_payload",
                _first_num(
                    src.get("position_quantity_before"),
                    src_meta.get("position_quantity_before"),
                ),
            ),
        ],
        conflicts,
    )
    pos_after = _consistent_num_field(
        "position_quantity_after",
        [
            (
                "snapshot_payload",
                _first_num(
                    s_pay.get("position_quantity_after"),
                    s_meta.get("position_quantity_after"),
                ),
            ),
            (
                "fill_payload",
                _first_num(
                    f_pay.get("position_quantity_after"),
                    f_meta.get("position_quantity_after"),
                ),
            ),
            (
                "source_payload",
                _first_num(
                    src.get("position_quantity_after"),
                    src_meta.get("position_quantity_after"),
                ),
            ),
        ],
        conflicts,
    )
    cash_before = _consistent_num_field(
        "cash_before",
        [
            (
                "snapshot_payload",
                _first_num(s_pay.get("cash_before"), s_meta.get("cash_before")),
            ),
            (
                "fill_payload",
                _first_num(f_pay.get("cash_before"), f_meta.get("cash_before")),
            ),
            (
                "source_payload",
                _first_num(src.get("cash_before"), src_meta.get("cash_before")),
            ),
        ],
        conflicts,
    )
    cash_after = _consistent_num_field(
        "cash_after",
        [
            (
                "snapshot_payload",
                _first_num(s_pay.get("cash_after"), s_meta.get("cash_after")),
            ),
            (
                "fill_payload",
                _first_num(f_pay.get("cash_after"), f_meta.get("cash_after")),
            ),
            (
                "source_payload",
                _first_num(src.get("cash_after"), src_meta.get("cash_after")),
            ),
        ],
        conflicts,
    )
    snapshot_position_qty = _consistent_num_field(
        "snapshot_position_qty",
        [
            (
                "snapshot_payload",
                _first_num(s_metrics.get("position_qty"), s_pay.get("position_qty")),
            ),
            ("snapshot_stage", _num(s_stage_fields.get("position_qty"))),
            (
                "source_payload",
                _first_num(src_metrics.get("position_qty"), src.get("position_qty")),
            ),
        ],
        conflicts,
    )
    snapshot_cash = _consistent_num_field(
        "snapshot_cash",
        [
            (
                "snapshot_payload",
                _first_num(s_metrics.get("cash"), s_pay.get("cash")),
            ),
            ("snapshot_stage", _num(s_stage_fields.get("cash"))),
            (
                "source_payload",
                _first_num(src_metrics.get("cash"), src.get("cash")),
            ),
        ],
        conflicts,
    )

    if conflicts:
        # Reject conflicting fill/snapshot evidence instead of writing untrusted fill values
        fill_qty = None
        fill_price = None

    has_fill_evidence = bool(
        fill_event_id
        or fill_id
        or broker_order_id
        or conflicts
        or (fill_qty is not None and fill_qty > 0.0)
        or any(
            val is not None
            for val in (pos_before, pos_after, cash_before, cash_after, fee, tax)
        )
    )
    return {
        "has_fill_evidence": has_fill_evidence,
        "fill_event_id": fill_event_id,
        "fill_id": fill_id,
        "snapshot_event_id": snapshot_event_id,
        "broker_order_id": broker_order_id,
        "fill_quantity": fill_qty,
        "fill_price": fill_price,
        "side": side,
        "symbol": symbol,
        "instrument_id": instrument_id,
        "kind": kind,
        "multiplier": multiplier,
        "fee": fee,
        "tax": tax,
        "position_quantity_before": pos_before,
        "position_quantity_after": pos_after,
        "cash_before": cash_before,
        "cash_after": cash_after,
        "snapshot_position_qty": snapshot_position_qty,
        "snapshot_cash": snapshot_cash,
        "missing_referenced_fill": missing_referenced_fill,
        "missing_referenced_snapshot": missing_referenced_snapshot,
        "conflicting_fields": conflicts,
        "has_identity_conflict": has_identity_conflict,
    }


def _evaluate_booking_checks(
    evidence: Mapping[str, Any],
    *,
    row: CandidateStageRow,
    broker_orders: Mapping[str, Mapping[str, Any] | None] | None = None,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Return (booking_checks, failure_codes, missing_evidence_fields)."""
    if not evidence.get("has_fill_evidence"):
        return (
            [
                {
                    "check": "position_change",
                    "status": "unverified",
                    "expected": None,
                    "actual": None,
                    "difference": None,
                    "delta": None,
                    "missing_evidence": ["fill_event", "expected", "actual"],
                }
            ],
            [],
            ["fill_event", "booking_comparison"],
        )

    side = _clean_str(evidence.get("side")).lower()
    sign = -1.0 if side == "sell" else (1.0 if side == "buy" else None)
    fill_qty = _num(evidence.get("fill_quantity"))
    fill_price = _num(evidence.get("fill_price"))
    kind = _clean_str(evidence.get("kind")) or "cash_share"
    multiplier = _num(evidence.get("multiplier"))
    broker_order_id = _clean_str(evidence.get("broker_order_id"))
    conflicting_fields = [
        _clean_str(c) for c in (evidence.get("conflicting_fields") or ()) if _clean_str(c)
    ]

    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    missing_fields: list[str] = []

    def add_missing(name: str) -> None:
        if name and name not in missing_fields:
            missing_fields.append(name)

    if conflicting_fields:
        add_missing("conflicting_fill_snapshot_evidence")
        for c_field in conflicting_fields:
            add_missing(c_field)
        return (
            [
                {
                    "check": "position_change",
                    "status": "unverified",
                    "expected": None,
                    "actual": None,
                    "difference": None,
                    "delta": None,
                    "missing_evidence": list(missing_fields),
                }
            ],
            [],
            missing_fields,
        )

    if evidence.get("missing_referenced_fill"):
        add_missing("fill_event")
    if evidence.get("missing_referenced_snapshot"):
        add_missing("snapshot_event")

    # 1. Optional read-only paper broker comparison when broker_orders is supplied
    if broker_orders is not None:
        if not broker_order_id:
            checks.append(
                {
                    "check": "broker_record",
                    "status": "unverified",
                    "broker_order_id": None,
                    "expected": "broker_order_id present",
                    "actual": None,
                    "missing_evidence": ["broker_order_id"],
                }
            )
            add_missing("broker_order_id")
        elif broker_order_id in broker_orders:
            order = broker_orders[broker_order_id]
            if order is None:
                checks.append(
                    {
                        "check": "broker_record",
                        "status": "failed",
                        "broker_order_id": broker_order_id,
                        "expected": "paper broker order present",
                        "actual": "missing",
                    }
                )
                failures.append("broker_record_missing")
            else:
                order_env = (
                    order.get("correlation_envelope")
                    if isinstance(order.get("correlation_envelope"), Mapping)
                    else {}
                )
                order_journey = _clean_str(order_env.get("journey_id"))
                order_tenant = _clean_str(order_env.get("tenant_id"))
                if (order_journey and order_journey != row.journey_id) or (
                    order_tenant and order_tenant != row.tenant_id
                ):
                    checks.append(
                        {
                            "check": "broker_record",
                            "status": "unverified",
                            "broker_order_id": broker_order_id,
                            "expected": row.journey_id,
                            "actual": order_journey or None,
                            "missing_evidence": ["broker_order_scope_match"],
                        }
                    )
                    add_missing("broker_order_scope_match")
                else:
                    b_checks = [
                        _booking_check(
                            "broker_fill_qty", fill_qty, _num(order.get("fill_qty"))
                        ),
                        _booking_check(
                            "broker_fill_price", fill_price, _num(order.get("fill_price"))
                        ),
                    ]
                    for name, exp_str, act_str in (
                        ("broker_status", "filled", _clean_str(order.get("status")).lower()),
                        ("broker_side", side, _clean_str(order.get("side")).lower()),
                        (
                            "broker_instrument_id",
                            _clean_str(evidence.get("instrument_id")),
                            _clean_str(order.get("instrument_id")),
                        ),
                    ):
                        if not exp_str or not act_str:
                            m_sides: list[str] = []
                            if not exp_str:
                                m_sides.append("expected")
                            if not act_str:
                                m_sides.append("actual")
                            b_checks.append(
                                {
                                    "check": name,
                                    "status": "unverified",
                                    "expected": exp_str or None,
                                    "actual": act_str or None,
                                    "missing_evidence": m_sides,
                                }
                            )
                        else:
                            b_checks.append(
                                {
                                    "check": name,
                                    "status": "passed" if exp_str == act_str else "failed",
                                    "expected": exp_str,
                                    "actual": act_str,
                                }
                            )
                    checks.extend(b_checks)
                    if any(c["status"] == "failed" for c in b_checks):
                        failures.append("broker_fill_mismatch")

    # 2. Position change vs signed fill quantity
    pos_before = _num(evidence.get("position_quantity_before"))
    pos_after = _num(evidence.get("position_quantity_after"))
    snap_pos = _num(evidence.get("snapshot_position_qty"))
    signed_qty = None if (fill_qty is None or sign is None) else sign * fill_qty
    pos_missing: list[str] = []
    if sign is None:
        pos_missing.append("side")
        add_missing("side")
    if fill_qty is None:
        pos_missing.append("fill_quantity")
        add_missing("fill_quantity")
    if pos_before is None:
        pos_missing.append("position_quantity_before")
        add_missing("position_quantity_before")
    if pos_after is None:
        pos_missing.append("position_quantity_after")
        add_missing("position_quantity_after")
    position_check = _booking_check(
        "position_change",
        signed_qty,
        None if (pos_before is None or pos_after is None) else (pos_after - pos_before),
        missing_fields=pos_missing,
    )
    checks.append(position_check)
    if pos_after is not None or snap_pos is not None:
        snap_pos_check = _booking_check(
            "committed_snapshot_position",
            pos_after,
            snap_pos,
            missing_fields=(["position_quantity_after"] if pos_after is None else ()),
        )
        checks.append(snap_pos_check)
        if snap_pos_check["status"] == "failed":
            failures.append("position_variance")
    if position_check["status"] == "failed" and "position_variance" not in failures:
        failures.append("position_variance")

    # 3. Cash change vs -(signed_qty * fill_price * multiplier) - fee - tax
    if kind == "cash_share":
        cash_before = _num(evidence.get("cash_before"))
        cash_after = _num(evidence.get("cash_after"))
        snap_cash = _num(evidence.get("snapshot_cash"))
        fee = _num(evidence.get("fee"))
        tax = _num(evidence.get("tax"))
        cash_missing: list[str] = []
        if sign is None:
            cash_missing.append("side")
            add_missing("side")
        if fill_qty is None:
            cash_missing.append("fill_quantity")
            add_missing("fill_quantity")
        if fill_price is None:
            cash_missing.append("fill_price")
            add_missing("fill_price")
        if multiplier is None:
            cash_missing.append("multiplier")
            add_missing("multiplier")
        if fee is None:
            cash_missing.append("fee")
            add_missing("fee")
        if tax is None:
            cash_missing.append("tax")
            add_missing("tax")
        if cash_before is None:
            cash_missing.append("cash_before")
            add_missing("cash_before")
        if cash_after is None:
            cash_missing.append("cash_after")
            add_missing("cash_after")
        expected_cash = (
            None
            if (
                signed_qty is None
                or fill_price is None
                or multiplier is None
                or fee is None
                or tax is None
            )
            else -(signed_qty * fill_price * multiplier) - fee - tax
        )
        cash_check = _booking_check(
            "cash_change",
            expected_cash,
            None if (cash_before is None or cash_after is None) else (cash_after - cash_before),
            missing_fields=cash_missing,
        )
        checks.append(cash_check)
        if cash_after is not None or snap_cash is not None:
            snap_cash_check = _booking_check(
                "committed_snapshot_cash",
                cash_after,
                snap_cash,
                missing_fields=(["cash_after"] if cash_after is None else ()),
            )
            checks.append(snap_cash_check)
            if snap_cash_check["status"] == "failed":
                failures.append("cash_variance")
        if cash_check["status"] == "failed" and "cash_variance" not in failures:
            failures.append("cash_variance")

    return checks, failures, missing_fields


def _extract_evaluation_checks(
    evaluation: Mapping[str, Any] | None,
) -> tuple[list[dict[str, Any]], bool]:
    """Return (non_ok_checks, is_health_or_drift_only)."""
    if not isinstance(evaluation, Mapping):
        return [], False
    non_ok_recon = [
        {
            "check": check.get("check"),
            "status": check.get("status"),
            "detail": check.get("detail"),
            "observed": check.get("observed"),
            "threshold": check.get(
                "threshold",
                check.get("critical_threshold", check.get("warning_threshold")),
            ),
        }
        for check in (evaluation.get("reconciliation_checks") or [])
        if isinstance(check, Mapping)
        and _clean_str(check.get("status")).lower() not in {"", "ok"}
    ]
    if non_ok_recon:
        names = {_clean_str(c.get("check")) for c in non_ok_recon}
        return non_ok_recon, bool(names and names <= _RUNTIME_HEALTH_CHECKS)

    non_ok_drift = [
        {
            "check": "runtime_health_summary",
            "status": check.get("status"),
            "detail": f"evaluation drift check {check.get('metric') or 'drift'}",
            "observed": check.get("observed"),
            "threshold": check.get(
                "threshold",
                check.get("critical_threshold", check.get("warning_threshold")),
            ),
        }
        for check in (evaluation.get("drift_checks") or [])
        if isinstance(check, Mapping)
        and _clean_str(check.get("status")).lower() not in {"", "ok"}
    ]
    if non_ok_drift:
        return non_ok_drift, True
    return [], True


def _reason_detail_dedup_key(item: Mapping[str, Any]) -> tuple[str, ...]:
    check_name = _clean_str(item.get("check"))
    detail_text = _clean_str(item.get("detail"))
    if check_name in _TRADE_COMPARISON_CHECKS:
        return ("trade", check_name)
    if check_name in _RUNTIME_HEALTH_CHECKS:
        if detail_text.startswith("evaluation drift check"):
            return ("drift", check_name, detail_text)
        return ("health", check_name)
    return ("other", check_name, json.dumps(dict(item), sort_keys=True))


def _deduplicate_reason_detail(
    eval_checks: Sequence[Mapping[str, Any]],
    detail_checks: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    index_by_key: dict[tuple[str, ...], int] = {}
    for raw_item in (*eval_checks, *detail_checks):
        if not isinstance(raw_item, Mapping):
            continue
        entry = dict(raw_item)
        key = _reason_detail_dedup_key(entry)
        existing_idx = index_by_key.get(key)
        if existing_idx is None:
            index_by_key[key] = len(deduped)
            deduped.append(entry)
        else:
            existing_status = _clean_str(deduped[existing_idx].get("status")).lower()
            new_status = _clean_str(entry.get("status")).lower()
            if existing_status == "unverified" and new_status in {"failed", "passed"}:
                deduped[existing_idx] = entry
    return deduped


def _has_preexisting_proven_variance(
    detail: Any,
) -> tuple[bool, list[dict[str, Any]], str]:
    if not isinstance(detail, Sequence) or isinstance(detail, (str, bytes)):
        return False, [], ""
    normalized: list[dict[str, Any]] = []
    failed_checks: set[str] = set()
    for item in detail:
        if not isinstance(item, Mapping):
            continue
        entry = dict(item)
        check_name = _clean_str(entry.get("check"))
        status_val = _clean_str(entry.get("status")).lower()
        if check_name in _NUMERIC_COMPARISON_CHECKS:
            exp_num = _num(entry.get("expected"))
            act_num = _num(entry.get("actual"))
            if exp_num is not None and act_num is not None:
                diff = act_num - exp_num
                entry["difference"] = diff
                entry["delta"] = diff
                if status_val == "failed" and abs(diff) > _BOOKING_TOLERANCE:
                    failed_checks.add(check_name)
        elif check_name in _TRADE_COMPARISON_CHECKS and status_val == "failed":
            exp_val = entry.get("expected")
            act_val = entry.get("actual")
            if exp_val not in (None, "") and act_val not in (None, "") and exp_val != act_val:
                failed_checks.add(check_name)
        normalized.append(entry)
    if not failed_checks:
        return False, [], ""
    if "broker_record" in failed_checks:
        code = "broker_record_missing"
    elif failed_checks & {
        "broker_fill_qty",
        "broker_fill_price",
        "broker_status",
        "broker_side",
        "broker_instrument_id",
    }:
        code = "broker_fill_mismatch"
    elif failed_checks & {"position_change", "committed_snapshot_position"}:
        code = "position_variance"
    else:
        code = "cash_variance"
    return True, _deduplicate_reason_detail((), normalized), code


def _derive_reconciliation_repair(
    row: CandidateStageRow,
    *,
    source_payload: Mapping[str, Any] | None,
    fill_stage: Mapping[str, Any] | None,
    fill_payload: Mapping[str, Any] | None,
    snapshot_stage: Mapping[str, Any] | None,
    snapshot_payload: Mapping[str, Any] | None,
    evaluation: Mapping[str, Any] | None,
    broker_orders: Mapping[str, Mapping[str, Any] | None] | None = None,
) -> StageRepairDecision:
    updated = dict(row.contract_fields)
    updated["source_event_id"] = row.source_event_id

    src = source_payload or {}
    src_meta = src.get("metadata") if isinstance(src.get("metadata"), Mapping) else {}
    eval_id = _first_str(
        src.get("reconciliation_evaluation_id"),
        src_meta.get("reconciliation_evaluation_id"),
        updated.get("reconciliation_evaluation_id"),
    )
    if eval_id:
        updated["reconciliation_evaluation_id"] = eval_id

    evidence = _extract_fill_and_snapshot_evidence(
        row=row,
        source_payload=source_payload,
        fill_stage=fill_stage,
        fill_payload=fill_payload,
        snapshot_stage=snapshot_stage,
        snapshot_payload=snapshot_payload,
    )
    fill_qty = evidence.get("fill_quantity")
    fill_price = evidence.get("fill_price")
    if fill_qty is not None and fill_qty > 0.0:
        updated["quantity"] = fill_qty
        updated["fill_quantity"] = fill_qty
    elif evidence.get("missing_referenced_fill") or evidence.get("conflicting_fields"):
        updated.pop("quantity", None)
        updated.pop("fill_quantity", None)
    if fill_price is not None:
        updated["price"] = fill_price
        updated["fill_price"] = fill_price
    elif evidence.get("missing_referenced_fill") or evidence.get("conflicting_fields"):
        updated.pop("price", None)
        updated.pop("fill_price", None)
    if evidence.get("fill_event_id"):
        updated["source_fill_event_id"] = evidence["fill_event_id"]
    elif "source_fill_event_id" in (evidence.get("conflicting_fields") or ()):
        updated.pop("source_fill_event_id", None)
    if evidence.get("fill_id"):
        updated["fill_id"] = evidence["fill_id"]
    elif "fill_id" in (evidence.get("conflicting_fields") or ()):
        updated.pop("fill_id", None)
    if evidence.get("snapshot_event_id"):
        updated["snapshot_event_id"] = evidence["snapshot_event_id"]
    if evidence.get("broker_order_id"):
        updated["broker_order_id"] = evidence["broker_order_id"]
    elif "broker_order_id" in (evidence.get("conflicting_fields") or ()):
        updated.pop("broker_order_id", None)

    # Check if source event or stage row already carries a proven variance
    pre_proven, pre_detail, pre_code = _has_preexisting_proven_variance(
        src.get("reason_detail")
        or src_meta.get("reason_detail")
        or updated.get("reason_detail")
    )
    booking_checks, booking_failures, missing_fields = _evaluate_booking_checks(
        evidence,
        row=row,
        broker_orders=broker_orders,
    )
    eval_checks, eval_is_health_only = _extract_evaluation_checks(evaluation)

    if not evidence.get("conflicting_fields") and (booking_failures or pre_proven):
        variance_code = booking_failures[0] if booking_failures else pre_code
        raw_src_code = _first_str(
            src.get("reason_code"),
            src_meta.get("reason_code"),
            updated.get("reason_code"),
        )
        if raw_src_code in _ACCOUNTING_VARIANCE_REASONS and (
            raw_src_code in booking_failures or raw_src_code == pre_code
        ):
            variance_code = raw_src_code
        combined_detail = _deduplicate_reason_detail(
            eval_checks,
            booking_checks if booking_failures else pre_detail,
        )
        updated["reason_code"] = variance_code
        updated["reason_detail"] = combined_detail
        if missing_fields:
            updated["missing_evidence"] = list(missing_fields)
        else:
            updated.pop("missing_evidence", None)
        needs_update = updated != row.contract_fields
        return StageRepairDecision(
            row=row,
            classification="proven_variances",
            repaired_contract_fields=updated,
            needs_update=needs_update,
            reason_code=variance_code,
        )

    if source_payload is not None and evaluation is not None and eval_is_health_only:
        has_unverified_booking = any(
            c.get("status") == "unverified" for c in booking_checks
        )
        if has_unverified_booking:
            updated["reason_code"] = "reconciliation_evidence_incomplete"
            updated["reason_detail"] = _deduplicate_reason_detail(
                eval_checks, booking_checks
            )
            updated["missing_evidence"] = (
                list(missing_fields) if missing_fields else ["booking_comparison"]
            )
        else:
            updated["reason_code"] = "runtime_unhealthy_at_reconciliation"
            updated["reason_detail"] = _deduplicate_reason_detail(
                eval_checks, booking_checks
            )
            updated.pop("missing_evidence", None)
        needs_update = updated != row.contract_fields
        return StageRepairDecision(
            row=row,
            classification="health_only_evaluations",
            repaired_contract_fields=updated,
            needs_update=needs_update,
            reason_code=str(updated["reason_code"]),
        )

    # Irrecoverable record: missing source telemetry row, missing evaluation row, or unverified unknown failure
    irrecoverable_missing: list[str] = []
    if source_payload is None:
        irrecoverable_missing.append("source_telemetry_event")
    if evaluation is None:
        irrecoverable_missing.append("reconciliation_evaluation")
    irrecoverable_missing.extend(["reconciliation_reason", "booking_comparison"])
    for m_field in missing_fields:
        if m_field not in irrecoverable_missing:
            irrecoverable_missing.append(m_field)
    updated["reason_code"] = "reason_not_recorded"
    updated["missing_evidence"] = irrecoverable_missing
    needs_update = updated != row.contract_fields
    return StageRepairDecision(
        row=row,
        classification="irrecoverable_records",
        repaired_contract_fields=updated,
        needs_update=needs_update,
        reason_code="reason_not_recorded",
    )


class InMemoryCauseRepairBackend:
    """Deterministic in-memory backend for unit and contract tests."""

    def __init__(
        self,
        *,
        stages: Sequence[CandidateStageRow] = (),
        telemetry_events: Mapping[str, Mapping[str, Any]] | None = None,
        companion_stages: Mapping[tuple[str, ...], Any] | None = None,
        drift_evaluations: Mapping[str, Mapping[str, Any]] | None = None,
        on_before_apply: Callable[[CandidateStageRow], None] | None = None,
    ) -> None:
        self.stages: dict[tuple[str, str, str, str, str], CandidateStageRow] = {
            (
                row.tenant_id,
                row.environment,
                row.journey_id,
                row.source_event_id,
                row.stage_name,
            ): row
            for row in stages
        }
        self.journey_revisions: dict[tuple[str, str, str], int] = {
            (row.tenant_id, row.environment, row.journey_id): row.journey_projection_revision
            for row in stages
        }
        self.telemetry_events: dict[str, dict[str, Any]] = {
            str(k): json.loads(json.dumps(dict(v)))
            for k, v in (telemetry_events or {}).items()
        }
        self.companion_stages: dict[tuple[str, ...], Any] = {
            tuple(str(part) for part in k): json.loads(
                json.dumps(
                    dict(v)
                    if isinstance(v, Mapping)
                    else [dict(item) for item in v if isinstance(item, Mapping)]
                )
            )
            for k, v in (companion_stages or {}).items()
        }
        self.drift_evaluations: dict[str, dict[str, Any]] = {}
        for k, v in (drift_evaluations or {}).items():
            record = json.loads(json.dumps(dict(v)))
            self.drift_evaluations[str(k)] = record
            eval_id = _clean_str(record.get("evaluation_id") or k)
            tenant_id = _clean_str(record.get("tenant_id"))
            if tenant_id and eval_id:
                self.drift_evaluations[
                    evaluation_storage_record_id(tenant_id, eval_id)
                ] = record
        self.on_before_apply = on_before_apply

    def fetch_candidate_batch(
        self,
        *,
        tenant_id: str,
        environment: str,
        batch_size: int,
        after_key: tuple[str, str, str] | None = None,
        journey_ids: Sequence[str] | None = None,
    ) -> list[CandidateStageRow]:
        allowed_journeys = set(journey_ids) if journey_ids else None
        candidates = [
            row
            for row in self.stages.values()
            if row.tenant_id == tenant_id
            and row.environment == environment
            and row.stage_status == "failed"
            and row.stage_name in {"order_submission", "reconciliation"}
            and (allowed_journeys is None or row.journey_id in allowed_journeys)
            and (after_key is None or row.cursor_key > after_key)
        ]
        candidates.sort(key=lambda r: r.cursor_key)
        return candidates[: int(batch_size)]

    def fetch_telemetry_events(
        self,
        *,
        event_ids: Sequence[str],
    ) -> dict[str, dict[str, Any]]:
        return {
            eid: json.loads(json.dumps(self.telemetry_events[eid]))
            for eid in event_ids
            if eid in self.telemetry_events
        }

    def fetch_journey_companion_stages(
        self,
        *,
        tenant_id: str,
        environment: str,
        journey_ids: Sequence[str],
    ) -> dict[tuple[str, str], Any]:
        wanted = set(journey_ids)
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for raw_key, stage_data in self.companion_stages.items():
            if len(raw_key) < 2:
                continue
            jid = str(raw_key[0])
            sname = (
                str(raw_key[1])
                if str(raw_key[1]) in {"fill_management", "ledger_booking"}
                else (
                    str(raw_key[2])
                    if len(raw_key) >= 3
                    and str(raw_key[2]) in {"fill_management", "ledger_booking"}
                    else ""
                )
            )
            if jid not in wanted or sname not in {"fill_management", "ledger_booking"}:
                continue
            for item in _as_companion_stage_list(stage_data):
                if (
                    item.get("tenant_id", tenant_id) == tenant_id
                    and item.get("environment", environment) == environment
                ):
                    grouped.setdefault((jid, sname), []).append(
                        json.loads(json.dumps(item))
                    )
        for row in self.stages.values():
            if (
                row.tenant_id == tenant_id
                and row.environment == environment
                and row.journey_id in wanted
                and row.stage_name in {"fill_management", "ledger_booking"}
            ):
                bucket = grouped.setdefault((row.journey_id, row.stage_name), [])
                if not any(
                    _companion_stage_event_id(existing) == row.source_event_id
                    for existing in bucket
                ):
                    bucket.append(
                        {
                            "tenant_id": row.tenant_id,
                            "environment": row.environment,
                            "journey_id": row.journey_id,
                            "stage_name": row.stage_name,
                            "source_event_id": row.source_event_id,
                            "stage_status": row.stage_status,
                            "contract_fields": json.loads(
                                json.dumps(row.contract_fields)
                            ),
                        }
                    )
        out: dict[tuple[str, str], Any] = {}
        for key, items in grouped.items():
            out[key] = items[0] if len(items) == 1 else items
        return out

    def fetch_drift_evaluations(
        self,
        *,
        tenant_id: str,
        evaluation_ids: Sequence[str],
    ) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for eval_id in evaluation_ids:
            clean_eval = _clean_str(eval_id)
            if not clean_eval:
                continue
            scoped_id = evaluation_storage_record_id(tenant_id, clean_eval)
            record = self.drift_evaluations.get(scoped_id) or self.drift_evaluations.get(
                clean_eval
            )
            if isinstance(record, Mapping):
                if (
                    _clean_str(record.get("tenant_id")) == tenant_id
                    and _clean_str(record.get("evaluation_id")) == clean_eval
                ):
                    out[clean_eval] = json.loads(json.dumps(dict(record)))
        return out

    def apply_stage_update(
        self,
        *,
        row: CandidateStageRow,
        repaired_contract_fields: Mapping[str, Any],
    ) -> int | None:
        if self.on_before_apply is not None:
            self.on_before_apply(row)
        key = (
            row.tenant_id,
            row.environment,
            row.journey_id,
            row.source_event_id,
            row.stage_name,
        )
        current = self.stages.get(key)
        current_journey_rev = self.journey_revisions.get(
            (row.tenant_id, row.environment, row.journey_id)
        )
        if (
            current is None
            or current.projection_revision != row.projection_revision
            or current.fingerprint != row.fingerprint
            or current.contract_fields != row.contract_fields
            or current_journey_rev != row.journey_projection_revision
        ):
            return None
        new_rev = current.projection_revision + 1
        self.stages[key] = CandidateStageRow(
            tenant_id=current.tenant_id,
            environment=current.environment,
            journey_id=current.journey_id,
            source_event_id=current.source_event_id,
            stage_name=current.stage_name,
            stage_status=current.stage_status,
            stage_ordinal=current.stage_ordinal,
            source_ingested_seq=current.source_ingested_seq,
            event_sequence=current.event_sequence,
            occurred_at_iso=current.occurred_at_iso,
            recorded_at_iso=_utc_now_iso(),
            contract_fields=json.loads(json.dumps(dict(repaired_contract_fields))),
            evidence_references=list(current.evidence_references),
            projection_revision=new_rev,
            fingerprint=current.fingerprint,
            journey_status=current.journey_status,
            journey_projection_revision=current.journey_projection_revision,
        )
        return new_rev

    def rollback_stage_update(
        self,
        *,
        record: Mapping[str, Any],
    ) -> int | None:
        key = (
            str(record["tenant_id"]),
            str(record["environment"]),
            str(record["journey_id"]),
            str(record["source_event_id"]),
            str(record["stage_name"]),
        )
        current = self.stages.get(key)
        current_journey_rev = self.journey_revisions.get(
            (key[0], key[1], key[2])
        )
        if (
            current is None
            or current.projection_revision != int(record["after_projection_revision"])
            or current.fingerprint != str(record["fingerprint"])
            or current_journey_rev != int(record["journey_projection_revision"])
        ):
            return None
        restored_rev = int(record["before_projection_revision"])
        self.stages[key] = CandidateStageRow(
            tenant_id=current.tenant_id,
            environment=current.environment,
            journey_id=current.journey_id,
            source_event_id=current.source_event_id,
            stage_name=current.stage_name,
            stage_status=current.stage_status,
            stage_ordinal=current.stage_ordinal,
            source_ingested_seq=current.source_ingested_seq,
            event_sequence=current.event_sequence,
            occurred_at_iso=current.occurred_at_iso,
            recorded_at_iso=current.recorded_at_iso,
            contract_fields=json.loads(
                json.dumps(dict(record["before_contract_fields"]))
            ),
            evidence_references=list(current.evidence_references),
            projection_revision=restored_rev,
            fingerprint=current.fingerprint,
            journey_status=current.journey_status,
            journey_projection_revision=current.journey_projection_revision,
        )
        return restored_rev


class PostgresCauseRepairBackend:
    """PostgreSQL backend for dry-run inventory, bounded apply, and rollback."""

    def __init__(
        self,
        dsn: str,
        *,
        projection_schema: str = DEFAULT_PROJECTION_SCHEMA,
        source_schema: str = DEFAULT_SOURCE_SCHEMA,
        source_relation: str = DEFAULT_SOURCE_TABLE,
        evaluation_schema: str = DEFAULT_EVALUATION_SCHEMA,
        evaluation_relation: str = DEFAULT_EVALUATION_TABLE,
        statement_timeout_ms: int = 30000,
        lock_timeout_ms: int = 5000,
        read_only: bool = True,
    ) -> None:
        self.dsn = dsn
        self.projection_schema = projection_schema
        self.source_schema = source_schema
        self.source_relation = source_relation
        self.evaluation_schema = evaluation_schema
        self.evaluation_relation = evaluation_relation
        self.statement_timeout_ms = max(1000, int(statement_timeout_ms))
        self.lock_timeout_ms = max(500, int(lock_timeout_ms))
        self.read_only = bool(read_only)

    def _connect(self, *, force_read_only: bool = False) -> Any:
        import psycopg

        conn = psycopg.connect(self.dsn)
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {int(self.statement_timeout_ms)}")
            cur.execute(f"SET lock_timeout = {int(self.lock_timeout_ms)}")
            if self.read_only or force_read_only:
                cur.execute("SET default_transaction_read_only = on")
        return conn

    @staticmethod
    def _iso(val: Any) -> str:
        if isinstance(val, datetime):
            dt = val if val.tzinfo is not None else val.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return _clean_str(val)

    @staticmethod
    def _json_obj(val: Any) -> dict[str, Any]:
        if isinstance(val, Mapping):
            return dict(val)
        if isinstance(val, str) and val.strip():
            try:
                parsed = json.loads(val)
                return dict(parsed) if isinstance(parsed, Mapping) else {}
            except ValueError:
                return {}
        return {}

    @staticmethod
    def _json_list(val: Any) -> list[Any]:
        if isinstance(val, list):
            return list(val)
        if isinstance(val, str) and val.strip():
            try:
                parsed = json.loads(val)
                return list(parsed) if isinstance(parsed, list) else []
            except ValueError:
                return []
        return []

    def fetch_candidate_batch(
        self,
        *,
        tenant_id: str,
        environment: str,
        batch_size: int,
        after_key: tuple[str, str, str] | None = None,
        journey_ids: Sequence[str] | None = None,
    ) -> list[CandidateStageRow]:
        clauses = [
            "js.tenant_id = %s",
            "js.environment = %s",
            "js.stage_status = 'failed'",
            "js.stage_name IN ('order_submission', 'reconciliation')",
        ]
        params: list[Any] = [tenant_id, environment]
        if journey_ids:
            clauses.append("js.journey_id = ANY(%s)")
            params.append(list(journey_ids))
        if after_key is not None:
            clauses.append("(js.journey_id, js.source_event_id, js.stage_name) > (%s, %s, %s)")
            params.extend([after_key[0], after_key[1], after_key[2]])
        params.append(int(batch_size))
        where_sql = " AND ".join(clauses)
        query = (
            "SELECT js.tenant_id, js.environment, js.journey_id, js.source_event_id, "
            "js.stage_name, js.stage_status, js.stage_ordinal, js.source_ingested_seq, "
            "js.event_sequence, js.occurred_at, js.recorded_at, js.contract_fields, "
            "js.evidence_references, js.projection_revision, js.fingerprint, "
            "COALESCE(j.status, 'incomplete') AS journey_status, "
            "COALESCE(j.projection_revision, 0) AS journey_projection_revision "
            f"FROM {self.projection_schema}.journey_stages js "
            f"JOIN {self.projection_schema}.journeys j "
            "ON j.tenant_id = js.tenant_id AND j.environment = js.environment AND j.journey_id = js.journey_id "
            f"WHERE {where_sql} "
            "ORDER BY js.journey_id ASC, js.source_event_id ASC, js.stage_name ASC "
            "LIMIT %s"
        )
        out: list[CandidateStageRow] = []
        with self._connect(force_read_only=True) as conn, conn.cursor() as cur:
            cur.execute(query, params)
            for r in cur.fetchall():
                out.append(
                    CandidateStageRow(
                        tenant_id=str(r[0]),
                        environment=str(r[1]),
                        journey_id=str(r[2]),
                        source_event_id=str(r[3]),
                        stage_name=str(r[4]),
                        stage_status=str(r[5]),
                        stage_ordinal=int(r[6] or 0),
                        source_ingested_seq=int(r[7] or 0),
                        event_sequence=int(r[8] or 0),
                        occurred_at_iso=self._iso(r[9]),
                        recorded_at_iso=self._iso(r[10]),
                        contract_fields=self._json_obj(r[11]),
                        evidence_references=self._json_list(r[12]),
                        projection_revision=int(r[13] or 0),
                        fingerprint=str(r[14] or ""),
                        journey_status=str(r[15] or ""),
                        journey_projection_revision=int(r[16] or 0),
                    )
                )
        return out

    def fetch_telemetry_events(
        self,
        *,
        event_ids: Sequence[str],
    ) -> dict[str, dict[str, Any]]:
        clean_ids = sorted({_clean_str(eid) for eid in event_ids if _clean_str(eid)})
        if not clean_ids:
            return {}
        query = (
            "SELECT event_id, event_type, created_at, payload "
            f'FROM "{self.source_schema}"."{self.source_relation}" '
            "WHERE event_id = ANY(%s)"
        )
        out: dict[str, dict[str, Any]] = {}
        with self._connect(force_read_only=True) as conn, conn.cursor() as cur:
            cur.execute(query, (clean_ids,))
            for r in cur.fetchall():
                eid = str(r[0])
                out[eid] = {
                    "event_id": eid,
                    "event_type": str(r[1] or ""),
                    "created_at": self._iso(r[2]),
                    "payload": self._json_obj(r[3]),
                }
        return out

    def fetch_journey_companion_stages(
        self,
        *,
        tenant_id: str,
        environment: str,
        journey_ids: Sequence[str],
    ) -> dict[tuple[str, str], Any]:
        clean_jids = sorted({_clean_str(jid) for jid in journey_ids if _clean_str(jid)})
        if not clean_jids:
            return {}
        query = (
            "SELECT journey_id, stage_name, source_event_id, stage_status, contract_fields "
            f"FROM {self.projection_schema}.journey_stages "
            "WHERE tenant_id = %s AND environment = %s "
            "AND journey_id = ANY(%s) "
            "AND stage_name IN ('fill_management', 'ledger_booking') "
            "ORDER BY journey_id, stage_name, stage_ordinal DESC, event_sequence DESC, "
            "source_ingested_seq DESC, source_event_id DESC"
        )
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        with self._connect(force_read_only=True) as conn, conn.cursor() as cur:
            cur.execute(query, (tenant_id, environment, clean_jids))
            for r in cur.fetchall():
                jid = str(r[0])
                sname = str(r[1])
                grouped.setdefault((jid, sname), []).append(
                    {
                        "tenant_id": tenant_id,
                        "environment": environment,
                        "journey_id": jid,
                        "stage_name": sname,
                        "source_event_id": str(r[2] or ""),
                        "stage_status": str(r[3] or ""),
                        "contract_fields": self._json_obj(r[4]),
                    }
                )
        out: dict[tuple[str, str], Any] = {}
        for key, items in grouped.items():
            out[key] = items[0] if len(items) == 1 else items
        return out

    def fetch_drift_evaluations(
        self,
        *,
        tenant_id: str,
        evaluation_ids: Sequence[str],
    ) -> dict[str, dict[str, Any]]:
        clean_evals = sorted({_clean_str(eid) for eid in evaluation_ids if _clean_str(eid)})
        if not clean_evals:
            return {}
        lookup_keys: list[str] = []
        for eid in clean_evals:
            lookup_keys.append(evaluation_storage_record_id(tenant_id, eid))
            lookup_keys.append(eid)
        query = (
            "SELECT record_id, payload "
            f'FROM "{self.evaluation_schema}"."{self.evaluation_relation}" '
            "WHERE record_id = ANY(%s)"
        )
        out: dict[str, dict[str, Any]] = {}
        with self._connect(force_read_only=True) as conn, conn.cursor() as cur:
            cur.execute(query, (lookup_keys,))
            for r in cur.fetchall():
                payload = self._json_obj(r[1])
                eval_id = _clean_str(payload.get("evaluation_id"))
                rec_tenant = _clean_str(payload.get("tenant_id"))
                if eval_id in clean_evals and rec_tenant == tenant_id:
                    out[eval_id] = payload
        return out

    def apply_stage_update(
        self,
        *,
        row: CandidateStageRow,
        repaired_contract_fields: Mapping[str, Any],
    ) -> int | None:
        if self.read_only:
            raise RuntimeError("Cannot apply stage update when backend is read_only")
        query = (
            f"UPDATE {self.projection_schema}.journey_stages "
            "SET contract_fields = %s::jsonb, "
            "    projection_revision = projection_revision + 1, "
            "    recorded_at = clock_timestamp() "
            "WHERE tenant_id = %s "
            "  AND environment = %s "
            "  AND journey_id = %s "
            "  AND source_event_id = %s "
            "  AND stage_name = %s "
            "  AND projection_revision = %s "
            "  AND fingerprint = %s "
            "  AND contract_fields = %s::jsonb "
            f"  AND EXISTS (SELECT 1 FROM {self.projection_schema}.journeys j "
            "              WHERE j.tenant_id = %s AND j.environment = %s "
            "                AND j.journey_id = %s AND j.projection_revision = %s) "
            "RETURNING projection_revision"
        )
        with self._connect(force_read_only=False) as conn, conn.cursor() as cur:
            cur.execute(
                query,
                (
                    json.dumps(dict(repaired_contract_fields), sort_keys=True),
                    row.tenant_id,
                    row.environment,
                    row.journey_id,
                    row.source_event_id,
                    row.stage_name,
                    row.projection_revision,
                    row.fingerprint,
                    json.dumps(row.contract_fields, sort_keys=True),
                    row.tenant_id,
                    row.environment,
                    row.journey_id,
                    row.journey_projection_revision,
                ),
            )
            fetched = cur.fetchone()
            conn.commit()
            return int(fetched[0]) if fetched else None

    def rollback_stage_update(
        self,
        *,
        record: Mapping[str, Any],
    ) -> int | None:
        if self.read_only:
            raise RuntimeError("Cannot rollback stage update when backend is read_only")
        query = (
            f"UPDATE {self.projection_schema}.journey_stages "
            "SET contract_fields = %s::jsonb, "
            "    projection_revision = %s "
            "WHERE tenant_id = %s "
            "  AND environment = %s "
            "  AND journey_id = %s "
            "  AND source_event_id = %s "
            "  AND stage_name = %s "
            "  AND projection_revision = %s "
            "  AND fingerprint = %s "
            f"  AND EXISTS (SELECT 1 FROM {self.projection_schema}.journeys j "
            "              WHERE j.tenant_id = %s AND j.environment = %s "
            "                AND j.journey_id = %s AND j.projection_revision = %s) "
            "RETURNING projection_revision"
        )
        with self._connect(force_read_only=False) as conn, conn.cursor() as cur:
            cur.execute(
                query,
                (
                    json.dumps(dict(record["before_contract_fields"]), sort_keys=True),
                    int(record["before_projection_revision"]),
                    str(record["tenant_id"]),
                    str(record["environment"]),
                    str(record["journey_id"]),
                    str(record["source_event_id"]),
                    str(record["stage_name"]),
                    int(record["after_projection_revision"]),
                    str(record["fingerprint"]),
                    str(record["tenant_id"]),
                    str(record["environment"]),
                    str(record["journey_id"]),
                    int(record["journey_projection_revision"]),
                ),
            )
            fetched = cur.fetchone()
            conn.commit()
            return int(fetched[0]) if fetched else None


def cleanup_temp_evidence(
    work_dir: Path = DEFAULT_TEMP_DIR,
    *,
    retention_seconds: int = DEFAULT_TEMP_RETENTION_SECONDS,
    remove_all: bool = False,
    now_epoch: float | None = None,
) -> tuple[int, int]:
    """Prune expired or all temporary repair before-image and cursor files."""
    if not work_dir.is_dir():
        return 0, 0
    now_ts = time.time() if now_epoch is None else float(now_epoch)
    cleaned_files = 0
    freed_bytes = 0
    for item in sorted(work_dir.iterdir()):
        if not item.is_file():
            continue
        if item.name not in {
            BEFORE_IMAGE_FILENAME,
            CURSOR_FILENAME,
            PREVIEW_CURSOR_FILENAME,
        } and not (
            item.name.startswith("paper_journey_cause_repair_")
            and item.suffix in {".json", ".jsonl"}
        ):
            continue
        try:
            st = item.stat()
        except OSError:
            continue
        age = max(0.0, now_ts - st.st_mtime)
        if remove_all or age >= float(retention_seconds):
            size = int(st.st_size)
            try:
                item.unlink()
                cleaned_files += 1
                freed_bytes += size
            except OSError:
                continue
    return cleaned_files, freed_bytes


def _append_before_image_record(
    path: Path,
    record: Mapping[str, Any],
    *,
    max_bytes: int = DEFAULT_MAX_BEFORE_IMAGE_BYTES,
    max_records: int = DEFAULT_MAX_BEFORE_IMAGE_RECORDS,
) -> tuple[int, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_bytes = path.stat().st_size if path.is_file() else 0
    existing_records = 0
    if path.is_file():
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    existing_records += 1
    serialized = json.dumps(dict(record), sort_keys=True) + "\n"
    encoded = serialized.encode("utf-8")
    if existing_records + 1 > int(max_records):
        raise RuntimeError(
            f"Before-image record cap ({max_records}) exceeded at {path}"
        )
    if existing_bytes + len(encoded) > int(max_bytes):
        raise RuntimeError(
            f"Before-image byte cap ({max_bytes}) exceeded at {path}"
        )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(serialized)
    return existing_records + 1, existing_bytes + len(encoded)


def _normalize_scope_journey_ids(
    journey_ids: Sequence[str] | None,
) -> list[str]:
    if not journey_ids:
        return []
    return sorted({_clean_str(jid) for jid in journey_ids if _clean_str(jid)})


def _read_cursor(
    path: Path,
    *,
    tenant_id: str,
    environment: str,
    mode: str = "apply",
    journey_ids: Sequence[str] | None = None,
) -> tuple[str, str, str] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        payload.get("tenant_id") != tenant_id
        or payload.get("environment") != environment
    ):
        return None
    saved_mode = _clean_str(payload.get("mode")) or "apply"
    if saved_mode != mode:
        return None
    raw_saved_jids = payload.get("journey_ids")
    saved_jids = (
        _normalize_scope_journey_ids(raw_saved_jids)
        if isinstance(raw_saved_jids, list)
        else []
    )
    if saved_jids != _normalize_scope_journey_ids(journey_ids):
        return None
    after = payload.get("last_after_key")
    if (
        isinstance(after, list)
        and len(after) == 3
        and all(isinstance(x, str) for x in after)
    ):
        return (after[0], after[1], after[2])
    return None


def _write_cursor(
    path: Path,
    *,
    tenant_id: str,
    environment: str,
    mode: str,
    journey_ids: Sequence[str] | None,
    after_key: tuple[str, str, str] | None,
    batches_processed: int,
    has_more: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": REPAIR_SCHEMA_VERSION,
        "mode": mode,
        "tenant_id": tenant_id,
        "environment": environment,
        "journey_ids": _normalize_scope_journey_ids(journey_ids),
        "last_after_key": list(after_key) if after_key is not None else None,
        "batches_processed": int(batches_processed),
        "has_more": bool(has_more),
        "updated_at": _utc_now_iso(),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


_NOTABLE_JOURNEY_IDS = frozenset(
    {
        "tj_79726064-4cc2-5510-8e11-7f5e59ff89bd",
        "tj_57418ba7-ef37-5015-9b5e-b6423872da89",
    }
)


def _select_companion_fill_stage(
    candidates: Sequence[Mapping[str, Any]],
    *,
    ref_fill_eid: str,
    ref_fill_id: str,
) -> Mapping[str, Any] | None:
    if not ref_fill_eid and not ref_fill_id:
        return None
    conflicted_match: Mapping[str, Any] | None = None
    for comp in candidates:
        same_fill, has_conflict = _companion_references_fill(
            comp,
            ref_fill_eid=ref_fill_eid,
            ref_fill_id=ref_fill_id,
        )
        if not same_fill:
            continue
        if not has_conflict:
            return comp
        if conflicted_match is None:
            conflicted_match = comp
    return conflicted_match


def _select_companion_snapshot_stage(
    candidates: Sequence[Mapping[str, Any]],
    *,
    ref_snap_eid: str,
) -> Mapping[str, Any] | None:
    if not ref_snap_eid:
        return None
    for comp in candidates:
        if _companion_matches_snapshot_id(comp, ref_snap_eid=ref_snap_eid):
            return comp
    return None


def run_cause_repair(
    backend: CauseRepairBackend,
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
    environment: str = DEFAULT_ENVIRONMENT,
    apply: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = DEFAULT_MAX_BATCHES,
    after_key: tuple[str, str, str] | None = None,
    resume: bool = False,
    journey_ids: Sequence[str] | None = None,
    work_dir: Path = DEFAULT_TEMP_DIR,
    before_image_path: Path | None = None,
    cursor_path: Path | None = None,
    max_before_image_bytes: int = DEFAULT_MAX_BEFORE_IMAGE_BYTES,
    max_before_image_records: int = DEFAULT_MAX_BEFORE_IMAGE_RECORDS,
    retention_seconds: int = DEFAULT_TEMP_RETENTION_SECONDS,
    broker_orders: Mapping[str, Mapping[str, Any] | None] | None = None,
) -> RepairRunReport:
    clean_tenant = _clean_str(tenant_id)
    clean_env = _clean_str(environment)
    if not clean_tenant or not clean_env:
        raise ValueError("tenant_id and environment are required")
    if not 1 <= int(batch_size) <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    if int(max_batches) < 1:
        raise ValueError("max_batches must be >= 1")

    mode = "apply" if apply else "dry_run"
    cleaned_files, freed_bytes = cleanup_temp_evidence(
        work_dir, retention_seconds=retention_seconds, remove_all=False
    )
    resolved_before_image = before_image_path or (work_dir / BEFORE_IMAGE_FILENAME)
    resolved_cursor = cursor_path or (
        work_dir / (CURSOR_FILENAME if apply else PREVIEW_CURSOR_FILENAME)
    )

    current_after = after_key
    if current_after is None and resume:
        current_after = _read_cursor(
            resolved_cursor,
            tenant_id=clean_tenant,
            environment=clean_env,
            mode=mode,
            journey_ids=journey_ids,
        )

    report = RepairRunReport(
        mode=mode,
        tenant_id=clean_tenant,
        environment=clean_env,
        batch_size=int(batch_size),
        max_batches=int(max_batches),
        before_image_path=str(resolved_before_image) if apply else None,
        cursor_path=str(resolved_cursor),
        temp_files_cleaned=cleaned_files,
        temp_bytes_freed=freed_bytes,
    )

    for _ in range(int(max_batches)):
        batch = backend.fetch_candidate_batch(
            tenant_id=clean_tenant,
            environment=clean_env,
            batch_size=int(batch_size),
            after_key=current_after,
            journey_ids=journey_ids,
        )
        if not batch:
            report.has_more = False
            if report.batches_processed > 0:
                _write_cursor(
                    resolved_cursor,
                    tenant_id=clean_tenant,
                    environment=clean_env,
                    mode=mode,
                    journey_ids=journey_ids,
                    after_key=current_after,
                    batches_processed=report.batches_processed,
                    has_more=False,
                )
            break

        report.batches_processed += 1
        batch_journey_ids = sorted({r.journey_id for r in batch})
        companions = backend.fetch_journey_companion_stages(
            tenant_id=clean_tenant,
            environment=clean_env,
            journey_ids=batch_journey_ids,
        )

        # First fetch primary source events for candidate rows + companion stages
        initial_event_ids: set[str] = {r.source_event_id for r in batch if r.source_event_id}
        for comp_entry in companions.values():
            for comp in _as_companion_stage_list(comp_entry):
                comp_eid = _companion_stage_event_id(comp)
                if comp_eid:
                    initial_event_ids.add(comp_eid)
        telemetry_map = backend.fetch_telemetry_events(event_ids=sorted(initial_event_ids))

        # Discover secondary fill/snapshot event IDs and evaluation IDs referenced in source payloads
        secondary_event_ids: set[str] = set()
        eval_ids: set[str] = set()
        for r in batch:
            raw_src = telemetry_map.get(r.source_event_id)
            src_pay = _verify_scoped_telemetry(
                raw_src,
                tenant_id=clean_tenant,
                environment=clean_env,
                journey_id=r.journey_id,
                expected_event_id=r.source_event_id,
            )
            src_meta = (
                src_pay.get("metadata")
                if isinstance(src_pay, Mapping)
                and isinstance(src_pay.get("metadata"), Mapping)
                else {}
            )
            if r.stage_name == "reconciliation":
                eval_id = _first_str(
                    (src_pay or {}).get("reconciliation_evaluation_id"),
                    src_meta.get("reconciliation_evaluation_id"),
                    r.contract_fields.get("reconciliation_evaluation_id"),
                )
                if eval_id:
                    eval_ids.add(eval_id)
                fill_eid = _first_str(
                    (src_pay or {}).get("source_fill_event_id"),
                    src_meta.get("source_fill_event_id"),
                    r.contract_fields.get("source_fill_event_id"),
                )
                if fill_eid and fill_eid not in telemetry_map:
                    secondary_event_ids.add(fill_eid)
                fill_id = _first_str(
                    (src_pay or {}).get("fill_id"),
                    src_meta.get("fill_id"),
                    r.contract_fields.get("fill_id"),
                )
                if fill_id.startswith("paper-fill-"):
                    inferred_eid = fill_id[len("paper-fill-") :]
                    if inferred_eid and inferred_eid not in telemetry_map:
                        secondary_event_ids.add(inferred_eid)
                snap_eid = _first_str(
                    (src_pay or {}).get("causal_parent_id"),
                    src_meta.get("causal_parent_id"),
                    ((src_pay or {}).get("correlation_envelope") or {}).get(
                        "causation_event_id"
                    ),
                    (src_pay or {}).get("snapshot_event_id"),
                    src_meta.get("snapshot_event_id"),
                    r.contract_fields.get("snapshot_event_id"),
                )
                if snap_eid and snap_eid not in telemetry_map:
                    secondary_event_ids.add(snap_eid)

        if secondary_event_ids:
            telemetry_map.update(
                backend.fetch_telemetry_events(event_ids=sorted(secondary_event_ids))
            )

        # Discover any fill event IDs referenced inside newly fetched snapshot payloads
        tertiary_event_ids: set[str] = set()
        for r in batch:
            if r.stage_name != "reconciliation":
                continue
            src_pay = _verify_scoped_telemetry(
                telemetry_map.get(r.source_event_id),
                tenant_id=clean_tenant,
                environment=clean_env,
                journey_id=r.journey_id,
                expected_event_id=r.source_event_id,
            )
            src_meta = (
                src_pay.get("metadata")
                if isinstance(src_pay, Mapping)
                and isinstance(src_pay.get("metadata"), Mapping)
                else {}
            )
            snap_eid = _first_str(
                (src_pay or {}).get("causal_parent_id"),
                src_meta.get("causal_parent_id"),
                ((src_pay or {}).get("correlation_envelope") or {}).get(
                    "causation_event_id"
                ),
                (src_pay or {}).get("snapshot_event_id"),
                src_meta.get("snapshot_event_id"),
                r.contract_fields.get("snapshot_event_id"),
            )
            if not snap_eid:
                continue
            snap_pay = _verify_scoped_telemetry(
                telemetry_map.get(snap_eid),
                tenant_id=clean_tenant,
                environment=clean_env,
                journey_id=r.journey_id,
                expected_event_id=snap_eid,
            )
            if not isinstance(snap_pay, Mapping):
                continue
            s_meta = (
                snap_pay.get("metadata")
                if isinstance(snap_pay.get("metadata"), Mapping)
                else {}
            )
            s_fill_eid = _first_str(
                snap_pay.get("source_fill_event_id"),
                s_meta.get("source_fill_event_id"),
                snap_pay.get("causal_parent_id"),
                s_meta.get("causal_parent_id"),
                (snap_pay.get("correlation_envelope") or {}).get("causation_event_id"),
            )
            if s_fill_eid and s_fill_eid not in telemetry_map:
                tertiary_event_ids.add(s_fill_eid)
            s_fill_id = _first_str(snap_pay.get("fill_id"), s_meta.get("fill_id"))
            if s_fill_id.startswith("paper-fill-"):
                s_inferred = s_fill_id[len("paper-fill-") :]
                if s_inferred and s_inferred not in telemetry_map:
                    tertiary_event_ids.add(s_inferred)
        if tertiary_event_ids:
            telemetry_map.update(
                backend.fetch_telemetry_events(event_ids=sorted(tertiary_event_ids))
            )

        evaluations_map = (
            backend.fetch_drift_evaluations(
                tenant_id=clean_tenant,
                evaluation_ids=sorted(eval_ids),
            )
            if eval_ids
            else {}
        )

        for r in batch:
            report.scanned_rows += 1
            current_after = r.cursor_key
            report.last_after_key = list(current_after)

            src_pay = _verify_scoped_telemetry(
                telemetry_map.get(r.source_event_id),
                tenant_id=clean_tenant,
                environment=clean_env,
                journey_id=r.journey_id,
                expected_event_id=r.source_event_id,
            )
            if r.stage_name == "order_submission":
                decision = _derive_order_refusal_repair(r, source_payload=src_pay)
            else:
                src_meta = (
                    src_pay.get("metadata")
                    if isinstance(src_pay, Mapping)
                    and isinstance(src_pay.get("metadata"), Mapping)
                    else {}
                )
                snap_eid = _first_str(
                    (src_pay or {}).get("causal_parent_id"),
                    src_meta.get("causal_parent_id"),
                    ((src_pay or {}).get("correlation_envelope") or {}).get(
                        "causation_event_id"
                    ),
                    (src_pay or {}).get("snapshot_event_id"),
                    src_meta.get("snapshot_event_id"),
                    r.contract_fields.get("snapshot_event_id"),
                )
                snap_pay = (
                    _verify_scoped_telemetry(
                        telemetry_map.get(snap_eid),
                        tenant_id=clean_tenant,
                        environment=clean_env,
                        journey_id=r.journey_id,
                        expected_event_id=snap_eid,
                    )
                    if snap_eid
                    else None
                )
                snapshot_stage = _select_companion_snapshot_stage(
                    _as_companion_stage_list(
                        companions.get((r.journey_id, "ledger_booking"))
                    ),
                    ref_snap_eid=snap_eid,
                )

                fill_eid = _first_str(
                    (src_pay or {}).get("source_fill_event_id"),
                    src_meta.get("source_fill_event_id"),
                    r.contract_fields.get("source_fill_event_id"),
                )
                raw_fid = _first_str(
                    (src_pay or {}).get("fill_id"),
                    src_meta.get("fill_id"),
                    r.contract_fields.get("fill_id"),
                )
                if not fill_eid and raw_fid.startswith("paper-fill-"):
                    fill_eid = raw_fid[len("paper-fill-") :]
                if not fill_eid and not raw_fid and isinstance(snap_pay, Mapping):
                    s_meta = (
                        snap_pay.get("metadata")
                        if isinstance(snap_pay.get("metadata"), Mapping)
                        else {}
                    )
                    fill_eid = _first_str(
                        snap_pay.get("source_fill_event_id"),
                        s_meta.get("source_fill_event_id"),
                        snap_pay.get("causal_parent_id"),
                        s_meta.get("causal_parent_id"),
                        (snap_pay.get("correlation_envelope") or {}).get(
                            "causation_event_id"
                        ),
                    )
                    raw_fid = _first_str(
                        snap_pay.get("fill_id"),
                        s_meta.get("fill_id"),
                    )
                    if not fill_eid and raw_fid.startswith("paper-fill-"):
                        fill_eid = raw_fid[len("paper-fill-") :]

                fill_stage = _select_companion_fill_stage(
                    _as_companion_stage_list(
                        companions.get((r.journey_id, "fill_management"))
                    ),
                    ref_fill_eid=fill_eid,
                    ref_fill_id=raw_fid,
                )
                fill_pay = (
                    _verify_scoped_telemetry(
                        telemetry_map.get(fill_eid),
                        tenant_id=clean_tenant,
                        environment=clean_env,
                        journey_id=r.journey_id,
                        expected_event_id=fill_eid,
                    )
                    if fill_eid
                    else None
                )
                eval_id = _first_str(
                    (src_pay or {}).get("reconciliation_evaluation_id"),
                    src_meta.get("reconciliation_evaluation_id"),
                    r.contract_fields.get("reconciliation_evaluation_id"),
                )
                evaluation = evaluations_map.get(eval_id) if eval_id else None
                decision = _derive_reconciliation_repair(
                    r,
                    source_payload=src_pay,
                    fill_stage=fill_stage,
                    fill_payload=fill_pay,
                    snapshot_stage=snapshot_stage,
                    snapshot_payload=snap_pay,
                    evaluation=evaluation,
                    broker_orders=broker_orders,
                )

            if decision.classification == "recoverable_refusals":
                report.recoverable_refusals += 1
            elif decision.classification == "health_only_evaluations":
                report.health_only_evaluations += 1
            elif decision.classification == "proven_variances":
                report.proven_variances += 1
            else:
                report.irrecoverable_records += 1

            if r.journey_id in _NOTABLE_JOURNEY_IDS:
                report.notable_journeys[r.journey_id] = {
                    "source_event_id": r.source_event_id,
                    "stage_name": r.stage_name,
                    "classification": decision.classification,
                    "needs_update": decision.needs_update,
                    "reason_code": decision.repaired_contract_fields.get("reason_code"),
                    "expression_reason_code": decision.repaired_contract_fields.get(
                        "expression_reason_code"
                    ),
                    "reason_detail": decision.repaired_contract_fields.get("reason_detail"),
                    "submitted_to_broker": decision.repaired_contract_fields.get(
                        "submitted_to_broker"
                    ),
                    "order_status": decision.repaired_contract_fields.get("order_status"),
                    "quantity": decision.repaired_contract_fields.get("quantity"),
                    "fill_quantity": decision.repaired_contract_fields.get("fill_quantity"),
                    "price": decision.repaired_contract_fields.get("price"),
                    "fill_price": decision.repaired_contract_fields.get("fill_price"),
                    "source_fill_event_id": decision.repaired_contract_fields.get(
                        "source_fill_event_id"
                    ),
                    "snapshot_event_id": decision.repaired_contract_fields.get(
                        "snapshot_event_id"
                    ),
                    "reconciliation_evaluation_id": decision.repaired_contract_fields.get(
                        "reconciliation_evaluation_id"
                    ),
                    "missing_evidence": decision.repaired_contract_fields.get(
                        "missing_evidence"
                    ),
                }

            if not decision.needs_update:
                report.already_repaired_rows += 1
                continue

            report.needs_update_rows += 1
            if not apply:
                continue

            before_record = {
                "schema_version": REPAIR_SCHEMA_VERSION,
                "recorded_at": _utc_now_iso(),
                "tenant_id": r.tenant_id,
                "environment": r.environment,
                "journey_id": r.journey_id,
                "source_event_id": r.source_event_id,
                "stage_name": r.stage_name,
                "stage_status": r.stage_status,
                "fingerprint": r.fingerprint,
                "before_projection_revision": r.projection_revision,
                "after_projection_revision": r.projection_revision + 1,
                "journey_projection_revision": r.journey_projection_revision,
                "before_contract_fields": r.contract_fields,
                "after_contract_fields": decision.repaired_contract_fields,
            }
            rec_count, byte_count = _append_before_image_record(
                resolved_before_image,
                before_record,
                max_bytes=max_before_image_bytes,
                max_records=max_before_image_records,
            )
            report.before_image_records = rec_count
            report.before_image_bytes = byte_count

            new_revision = backend.apply_stage_update(
                row=r,
                repaired_contract_fields=decision.repaired_contract_fields,
            )
            if new_revision is None:
                report.skipped_concurrent_revision += 1
            else:
                report.updated_rows += 1

        if len(batch) < int(batch_size):
            report.has_more = False
            _write_cursor(
                resolved_cursor,
                tenant_id=clean_tenant,
                environment=clean_env,
                mode=mode,
                journey_ids=journey_ids,
                after_key=current_after,
                batches_processed=report.batches_processed,
                has_more=False,
            )
            break
        report.has_more = True
        _write_cursor(
            resolved_cursor,
            tenant_id=clean_tenant,
            environment=clean_env,
            mode=mode,
            journey_ids=journey_ids,
            after_key=current_after,
            batches_processed=report.batches_processed,
            has_more=True,
        )

    if resolved_before_image.is_file():
        report.before_image_bytes = int(resolved_before_image.stat().st_size)
        with resolved_before_image.open("r", encoding="utf-8") as handle:
            report.before_image_records = sum(1 for line in handle if line.strip())
    return report


def run_cause_rollback(
    backend: CauseRepairBackend,
    *,
    before_image_path: Path,
    tenant_id: str = DEFAULT_TENANT_ID,
    environment: str = DEFAULT_ENVIRONMENT,
) -> RepairRunReport:
    if not before_image_path.is_file():
        raise FileNotFoundError(f"Before-image file not found: {before_image_path}")
    clean_tenant = _clean_str(tenant_id)
    clean_env = _clean_str(environment)
    report = RepairRunReport(
        mode="rollback",
        tenant_id=clean_tenant,
        environment=clean_env,
        before_image_path=str(before_image_path),
        before_image_bytes=int(before_image_path.stat().st_size),
    )
    records: list[dict[str, Any]] = []
    with before_image_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if (
                payload.get("tenant_id") == clean_tenant
                and payload.get("environment") == clean_env
            ):
                records.append(payload)
    report.before_image_records = len(records)
    # Apply rollback in reverse order of application
    for record in reversed(records):
        report.scanned_rows += 1
        restored_rev = backend.rollback_stage_update(record=record)
        if restored_rev is None:
            report.skipped_concurrent_revision += 1
        else:
            report.rolled_back_rows += 1
    return report


def configure_cli_parser(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument(
        "--dsn",
        default=os.getenv("PROJECTION_DB_DSN")
        or os.getenv("TELEMETRY_DB_DSN")
        or os.getenv("DATABASE_URL")
        or "",
        help="PostgreSQL connection DSN",
    )
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID)
    parser.add_argument("--environment", default=DEFAULT_ENVIRONMENT)
    parser.add_argument(
        "--projection-schema",
        default=os.getenv("TRADE_JOURNEY_PROJECTION_SCHEMA") or DEFAULT_PROJECTION_SCHEMA,
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--max-batches", type=int, default=DEFAULT_MAX_BATCHES)
    parser.add_argument(
        "--journey-id",
        action="append",
        dest="journey_ids",
        default=None,
        help="Optional journey_id filter (may be repeated)",
    )
    parser.add_argument(
        "--after-key",
        nargs=3,
        metavar=("JOURNEY_ID", "SOURCE_EVENT_ID", "STAGE_NAME"),
        default=None,
        help="Explicit keyset cursor (journey_id source_event_id stage_name)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from the saved cursor in --work-dir",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=DEFAULT_TEMP_DIR,
        help="Bounded temporary evidence directory",
    )
    parser.add_argument(
        "--before-image",
        type=Path,
        default=None,
        help="Explicit path for the scoped before-image JSONL file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write the JSON report",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Preview inventory and repair classifications without writing (default)",
    )
    mode_group.add_argument(
        "--apply",
        action="store_true",
        help="Apply bounded stage updates and save scoped before-image",
    )
    mode_group.add_argument(
        "--rollback",
        action="store_true",
        help="Rollback previously applied stage updates from --before-image",
    )
    mode_group.add_argument(
        "--cleanup-temp",
        action="store_true",
        help="Remove temporary before-image and cursor files from --work-dir",
    )
    return parser


def run_from_cli_args(args: argparse.Namespace) -> int:
    work_dir: Path = args.work_dir
    if getattr(args, "cleanup_temp", False):
        cleaned, freed = cleanup_temp_evidence(work_dir, remove_all=True)
        payload = {
            "schema_version": REPAIR_SCHEMA_VERSION,
            "mode": "cleanup_temp",
            "work_dir": str(work_dir),
            "temp_files_cleaned": cleaned,
            "temp_bytes_freed": freed,
        }
        text = json.dumps(payload, indent=2, sort_keys=True)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0

    dsn = _clean_str(args.dsn)
    if not dsn:
        raise RuntimeError(
            "PostgreSQL DSN is required (--dsn or PROJECTION_DB_DSN / TELEMETRY_DB_DSN)"
        )
    is_write = bool(getattr(args, "apply", False) or getattr(args, "rollback", False))
    backend = PostgresCauseRepairBackend(
        dsn,
        projection_schema=args.projection_schema,
        read_only=not is_write,
    )
    before_image = args.before_image or (work_dir / BEFORE_IMAGE_FILENAME)
    if getattr(args, "rollback", False):
        report = run_cause_rollback(
            backend,
            before_image_path=before_image,
            tenant_id=args.tenant_id,
            environment=args.environment,
        )
    else:
        after_tuple = (
            (str(args.after_key[0]), str(args.after_key[1]), str(args.after_key[2]))
            if args.after_key
            else None
        )
        report = run_cause_repair(
            backend,
            tenant_id=args.tenant_id,
            environment=args.environment,
            apply=bool(getattr(args, "apply", False)),
            batch_size=args.batch_size,
            max_batches=args.max_batches,
            after_key=after_tuple,
            resume=bool(getattr(args, "resume", False)),
            journey_ids=args.journey_ids,
            work_dir=work_dir,
            before_image_path=before_image,
        )
    text = json.dumps(report.to_dict(), indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    configure_cli_parser(parser)
    args = parser.parse_args(argv)
    return run_from_cli_args(args)


if __name__ == "__main__":
    raise SystemExit(main())
