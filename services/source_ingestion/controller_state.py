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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "source_ingest_controller_state.v1"


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
        self.desired_state = dict(desired_state)
        self.reconcile = dict(reconcile)
        self.schedule = dict(schedule)
        self.actual_readback = dict(actual_readback)

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
            self.desired_state = dict(desired_state)
        if reconcile is not None:
            self.reconcile = dict(reconcile)
        if schedule is not None:
            self.schedule = dict(schedule)
        if actual_readback is not None:
            self.actual_readback = dict(actual_readback)

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
            "desired_state": dict(self.desired_state),
            "reconcile": dict(self.reconcile),
            "schedule": dict(self.schedule),
            "actual_readback": dict(self.actual_readback),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ControllerState":
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ControllerStateError("unsupported source-ingest controller state schema")
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
            desired_state=dict(payload.get("desired_state") or {}),
            reconcile=dict(payload.get("reconcile") or {}),
            schedule=dict(payload.get("schedule") or {}),
            actual_readback=dict(payload.get("actual_readback") or {}),
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

    def save(self, state: ControllerState) -> None:
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
