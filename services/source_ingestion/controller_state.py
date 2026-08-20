"""Durable local state for the supervised source-ingestion controller.

The canonical cross-service controller record is written to Postgres through
``services.loop-control``.  This small snapshot is the worker's restart anchor
and the source-ingest service's local readback.  Writes use fsync + atomic
rename, and every snapshot carries a checksum so a torn/corrupt state file is
an explicit failure instead of a silent reset.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "source_ingest_controller_state.v2"
LEGACY_SCHEMA_VERSION = "source_ingest_controller_state.v1"
MAX_TRACKED_CONNECTORS = 512
MAX_TEXT_LENGTH = 512


class ControllerStateError(RuntimeError):
    """Raised when controller restart truth is missing or corrupt."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ControllerStateError(f"invalid controller timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _checksum(payload: Mapping[str, Any]) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _bounded_text(value: Any, *, limit: int = MAX_TEXT_LENGTH) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:limit]


def _bounded_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _scalar_summary(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep scalar counters/statuses without retaining unbounded detail payloads."""

    summary: dict[str, Any] = {}
    for key, item in dict(value or {}).items():
        if isinstance(item, bool) or item is None:
            summary[str(key)] = item
        elif isinstance(item, int):
            summary[str(key)] = item
        elif isinstance(item, float):
            summary[str(key)] = item
        elif isinstance(item, str):
            summary[str(key)] = _bounded_text(item)
    return summary


def _identity_inventory(values: Any) -> dict[str, Any]:
    identities = sorted(
        {
            str(value).strip()
            for value in (values or ())
            if isinstance(value, (str, int)) and str(value).strip()
        }
    )
    retained = identities[:MAX_TRACKED_CONNECTORS]
    return {
        "count": len(identities),
        "sha256": _checksum({"connector_ids": identities}),
        "connector_ids": retained,
        "truncated": len(retained) != len(identities),
    }


def _terminal_connector_summary(connector: Mapping[str, Any]) -> dict[str, Any] | None:
    connector_id = _bounded_text(connector.get("connector_id"))
    if not connector_id:
        return None
    schedule = connector.get("schedule")
    freshness = connector.get("freshness")
    record = connector.get("latest_source_record")
    health = connector.get("source_health")
    return {
        "connector_id": connector_id,
        "configured": bool(connector.get("configured")),
        "schedule": {
            "enabled": bool(schedule.get("enabled")) if isinstance(schedule, Mapping) else False,
            "interval_seconds": (
                _bounded_int(schedule.get("interval_seconds")) if isinstance(schedule, Mapping) else None
            ),
        },
        "freshness": {
            "status": _bounded_text(freshness.get("status")) if isinstance(freshness, Mapping) else None,
            "last_ingest_run_id": (
                _bounded_text(freshness.get("last_ingest_run_id")) if isinstance(freshness, Mapping) else None
            ),
            "staleness_seconds": (
                _bounded_int(freshness.get("staleness_seconds")) if isinstance(freshness, Mapping) else None
            ),
        },
        "terminal": {
            "source_id": _bounded_text(record.get("source_id")) if isinstance(record, Mapping) else None,
            "status": _bounded_text(record.get("status")) if isinstance(record, Mapping) else None,
            "created_at": _bounded_text(record.get("created_at")) if isinstance(record, Mapping) else None,
        },
        "source_health": {
            "status": _bounded_text(health.get("status")) if isinstance(health, Mapping) else None,
            "last_success_at": _bounded_text(health.get("last_success_at")) if isinstance(health, Mapping) else None,
        },
    }


def _summary_terminal_connector(item: Mapping[str, Any]) -> dict[str, Any] | None:
    return _terminal_connector_summary(
        {
            "connector_id": item.get("connector_id"),
            "configured": item.get("configured"),
            "schedule": item.get("schedule"),
            "freshness": item.get("freshness"),
            "latest_source_record": item.get("terminal"),
            "source_health": item.get("source_health"),
        }
    )


def summarize_actual_readback(actual: Mapping[str, Any] | None) -> dict[str, Any]:
    """Project API readback into a restart-safe, non-recursive state summary.

    ``/controller/readback`` intentionally contains operational detail for an
    operator.  Persisting it made the snapshot include its prior snapshot on
    every failure.  This projection retains the controller's cursor, counters,
    schedule and terminal identity truth without retaining a prior state or
    arbitrary connector payloads.
    """

    payload = dict(actual or {})
    is_summary = payload.get("schema_version") == "source_ingest_controller_readback_summary.v1"
    raw_connectors = (
        (payload.get("terminal_connectors") or {}).get("items")
        if is_summary and isinstance(payload.get("terminal_connectors"), Mapping)
        else payload.get("connectors")
    )
    compact = _summary_terminal_connector if is_summary else _terminal_connector_summary
    connectors = [
        compacted
        for item in (raw_connectors if isinstance(raw_connectors, list) else ())
        if isinstance(item, Mapping)
        for compacted in [compact(item)]
        if compacted is not None
    ]
    connectors.sort(key=lambda item: str(item["connector_id"]))
    retained = connectors[:MAX_TRACKED_CONNECTORS]
    stored_inventory = payload.get("terminal_connectors") if is_summary else None
    inventory_count = (
        _bounded_int(stored_inventory.get("count"))
        if isinstance(stored_inventory, Mapping)
        else len(connectors)
    )
    inventory_count = max(len(connectors), inventory_count or 0)
    inventory_sha256 = (
        _bounded_text(stored_inventory.get("sha256"))
        if isinstance(stored_inventory, Mapping)
        else _checksum({"terminal_connectors": connectors})
    )
    inventory_truncated = (
        bool(stored_inventory.get("truncated"))
        if isinstance(stored_inventory, Mapping)
        else len(retained) != len(connectors)
    )
    dlq_status_counts = _scalar_summary(
        payload.get("dlq_status_counts") if isinstance(payload.get("dlq_status_counts"), Mapping) else {}
    )
    return {
        "schema_version": "source_ingest_controller_readback_summary.v1",
        "pre_captured_at": _bounded_text(payload.get("pre_captured_at")),
        "captured_at": _bounded_text(payload.get("captured_at")),
        "connector_count": _bounded_int(payload.get("connector_count")),
        "source_record_count": _bounded_int(payload.get("source_record_count")),
        "dlq_count": _bounded_int(payload.get("dlq_count")),
        "pending_dlq_count": _bounded_int(payload.get("pending_dlq_count")),
        "unresolved_dlq_count": _bounded_int(payload.get("unresolved_dlq_count")),
        "dlq_status_counts": dlq_status_counts,
        "frontier_backlog": _bounded_int(payload.get("frontier_backlog")),
        "max_lag_seconds": _bounded_int(payload.get("max_lag_seconds")),
        "terminal_connectors": {
            "count": inventory_count,
            "sha256": inventory_sha256,
            "items": retained,
            "truncated": inventory_truncated or len(retained) != inventory_count,
        },
    }


def summarize_desired_state(desired_state: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = dict(desired_state or {})
    return {
        "authority": _bounded_text(payload.get("authority")),
        "transport": _bounded_text(payload.get("transport")),
        "sha256": _bounded_text(payload.get("sha256")),
        "persona_count": _bounded_int(payload.get("persona_count")),
        "requirement_count": _bounded_int(payload.get("requirement_count")),
        "read_at": _bounded_text(payload.get("read_at")),
    }


def summarize_reconcile(reconcile: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = dict(reconcile or {})
    connector_ids: list[str] = []
    stored_inventory = payload.get("connector_inventory")
    if isinstance(payload.get("connector_ids"), list):
        connector_ids = [str(value) for value in payload["connector_ids"]]
    elif isinstance(stored_inventory, Mapping):
        connector_ids = [str(value) for value in (stored_inventory.get("connector_ids") or ())]
    else:
        for result in payload.get("results") or ():
            if not isinstance(result, Mapping):
                continue
            for action in result.get("actions") or ():
                if isinstance(action, Mapping) and action.get("connector_id"):
                    connector_ids.append(str(action["connector_id"]))
    inventory = _identity_inventory(connector_ids)
    if isinstance(stored_inventory, Mapping):
        stored_count = _bounded_int(stored_inventory.get("count"))
        inventory["count"] = max(len(connector_ids), stored_count or 0)
        inventory["sha256"] = _bounded_text(stored_inventory.get("sha256")) or inventory["sha256"]
        inventory["truncated"] = bool(stored_inventory.get("truncated")) or (
            len(inventory["connector_ids"]) != inventory["count"]
        )
    return {
        "summary": _scalar_summary(payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}),
        "connector_inventory": inventory,
    }


def summarize_schedule(schedule: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = dict(schedule or {})
    return {
        "mode": _bounded_text(payload.get("mode")),
        "provider_egress_attempted": bool(payload.get("provider_egress_attempted")),
        "summary": _scalar_summary(payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}),
    }


@dataclass
class ControllerState:
    controller_id: str
    controller_name: str
    environment: str
    tenant_id: str
    deployment: dict[str, Any]
    started_at: str = field(default_factory=utc_now)
    sequence_no: int = 0
    heartbeat_at: str | None = None
    last_tick_at: str | None = None
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_failure_stage: str | None = None
    last_failure_reason: str | None = None
    last_repair_at: str | None = None
    consecutive_failures: int = 0
    total_ticks: int = 0
    total_successes: int = 0
    total_failures: int = 0
    startup_missed_ticks: int = 0
    desired_state: dict[str, Any] = field(default_factory=dict)
    reconcile: dict[str, Any] = field(default_factory=dict)
    schedule: dict[str, Any] = field(default_factory=dict)
    actual_readback: dict[str, Any] = field(default_factory=dict)
    migration: dict[str, Any] = field(default_factory=dict)

    def record_startup_missed(self, *, interval_seconds: int, now: datetime | None = None) -> int:
        anchor = parse_utc(self.last_tick_at or self.heartbeat_at)
        if anchor is None:
            return 0
        now = now or datetime.now(timezone.utc)
        missed = max(0, int((now - anchor).total_seconds() / interval_seconds) - 1)
        self.startup_missed_ticks += missed
        return missed

    def record_tick_started(self) -> None:
        now = utc_now()
        self.sequence_no += 1
        self.total_ticks += 1
        self.heartbeat_at = now
        self.last_tick_at = now

    def record_success(
        self,
        *,
        desired_state: Mapping[str, Any],
        reconcile: Mapping[str, Any],
        schedule: Mapping[str, Any],
        actual_readback: Mapping[str, Any],
    ) -> None:
        now = utc_now()
        recovered = self.consecutive_failures > 0
        self.heartbeat_at = now
        self.last_success_at = now
        self.total_successes += 1
        self.consecutive_failures = 0
        self.last_failure_stage = None
        self.last_failure_reason = None
        if recovered:
            self.last_repair_at = now
        self.desired_state = summarize_desired_state(desired_state)
        self.reconcile = summarize_reconcile(reconcile)
        self.schedule = summarize_schedule(schedule)
        self.actual_readback = summarize_actual_readback(actual_readback)

    def record_failure(
        self,
        *,
        stage: str,
        reason: str,
        desired_state: Mapping[str, Any] | None = None,
        reconcile: Mapping[str, Any] | None = None,
        schedule: Mapping[str, Any] | None = None,
        actual_readback: Mapping[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        self.heartbeat_at = now
        self.last_failure_at = now
        self.last_failure_stage = str(stage or "unknown")
        self.last_failure_reason = str(reason or "controller tick failed")[:2000]
        self.consecutive_failures += 1
        self.total_failures += 1
        if desired_state is not None:
            self.desired_state = summarize_desired_state(desired_state)
        if reconcile is not None:
            self.reconcile = summarize_reconcile(reconcile)
        if schedule is not None:
            self.schedule = summarize_schedule(schedule)
        if actual_readback is not None:
            self.actual_readback = summarize_actual_readback(actual_readback)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "controller_id": self.controller_id,
            "controller_name": self.controller_name,
            "environment": self.environment,
            "tenant_id": self.tenant_id,
            "deployment": dict(self.deployment),
            "started_at": self.started_at,
            "sequence_no": self.sequence_no,
            "heartbeat_at": self.heartbeat_at,
            "last_tick_at": self.last_tick_at,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_failure_stage": self.last_failure_stage,
            "last_failure_reason": self.last_failure_reason,
            "last_repair_at": self.last_repair_at,
            "consecutive_failures": self.consecutive_failures,
            "total_ticks": self.total_ticks,
            "total_successes": self.total_successes,
            "total_failures": self.total_failures,
            "startup_missed_ticks": self.startup_missed_ticks,
            "desired_state": summarize_desired_state(self.desired_state),
            "reconcile": summarize_reconcile(self.reconcile),
            "schedule": summarize_schedule(self.schedule),
            "actual_readback": summarize_actual_readback(self.actual_readback),
            "migration": _scalar_summary(self.migration),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ControllerState":
        schema_version = payload.get("schema_version")
        if schema_version not in {SCHEMA_VERSION, LEGACY_SCHEMA_VERSION}:
            raise ControllerStateError("unsupported source-ingest controller state schema")
        migration = _scalar_summary(payload.get("migration") if isinstance(payload.get("migration"), Mapping) else {})
        if schema_version == LEGACY_SCHEMA_VERSION:
            migration = {
                "migrated_from_schema": LEGACY_SCHEMA_VERSION,
                "legacy_state_sha256": _checksum(dict(payload)),
                "state_backup_required": True,
            }
        return cls(
            controller_id=str(payload["controller_id"]),
            controller_name=str(payload["controller_name"]),
            environment=str(payload["environment"]),
            tenant_id=str(payload["tenant_id"]),
            deployment=dict(payload.get("deployment") or {}),
            started_at=str(payload.get("started_at") or utc_now()),
            sequence_no=int(payload.get("sequence_no") or 0),
            heartbeat_at=payload.get("heartbeat_at"),
            last_tick_at=payload.get("last_tick_at"),
            last_success_at=payload.get("last_success_at"),
            last_failure_at=payload.get("last_failure_at"),
            last_failure_stage=payload.get("last_failure_stage"),
            last_failure_reason=payload.get("last_failure_reason"),
            last_repair_at=payload.get("last_repair_at"),
            consecutive_failures=int(payload.get("consecutive_failures") or 0),
            total_ticks=int(payload.get("total_ticks") or 0),
            total_successes=int(payload.get("total_successes") or 0),
            total_failures=int(payload.get("total_failures") or 0),
            startup_missed_ticks=int(payload.get("startup_missed_ticks") or 0),
            desired_state=summarize_desired_state(payload.get("desired_state") or {}),
            reconcile=summarize_reconcile(payload.get("reconcile") or {}),
            schedule=summarize_schedule(payload.get("schedule") or {}),
            actual_readback=summarize_actual_readback(payload.get("actual_readback") or {}),
            migration=migration,
        )


class ControllerStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> ControllerState | None:
        if not self.path.exists():
            return None
        try:
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ControllerStateError(f"controller state is unreadable: {self.path}") from exc
        if not isinstance(envelope, Mapping) or not isinstance(envelope.get("state"), Mapping):
            raise ControllerStateError("controller state envelope is invalid")
        state_payload = dict(envelope["state"])
        expected = str(envelope.get("checksum") or "")
        actual = _checksum(state_payload)
        if not expected or expected != actual:
            raise ControllerStateError("controller state checksum mismatch")
        return ControllerState.from_dict(state_payload)

    def _backup_legacy_state(self, state: ControllerState) -> None:
        """Keep a read-only v1 copy before the first atomic v2 replacement."""

        if not state.migration.get("state_backup_required") or not self.path.exists():
            return
        checksum = str(state.migration.get("legacy_state_sha256") or "unknown")[:12]
        backup_path = self.path.with_name(f"{self.path.name}.legacy-v1-{checksum}.json")
        if backup_path.exists():
            return
        temp_path = backup_path.with_name(f".{backup_path.name}.{os.getpid()}.tmp")
        try:
            with self.path.open("rb") as source, temp_path.open("xb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temp_path, backup_path)
            backup_path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def save(self, state: ControllerState) -> None:
        self._backup_legacy_state(state)
        state_payload = state.to_dict()
        envelope = {
            "state": state_payload,
            "checksum_algorithm": "sha256",
            "checksum": _checksum(state_payload),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as handle:
                handle.write(_canonical_json(envelope) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temp_path.exists():
                temp_path.unlink()


def read_controller_state(path: str | Path) -> dict[str, Any] | None:
    state = ControllerStateStore(path).load()
    return state.to_dict() if state is not None else None
