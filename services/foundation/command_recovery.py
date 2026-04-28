"""Crash-recovery helpers for foundation-backed command execution."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from .exceptions import FoundationValidationError
from .idempotency import IdempotencyRecord, IdempotencyStatus
from .serialization import drop_none, ensure_utc, foundation_id

COMMAND_RECOVERY_AUDIT_SCHEMA_VERSION = "command_recovery_audit.v1"


class CommandRecoveryAction(str, Enum):
    QUARANTINED = "foundation.command_recovery.quarantined"
    REPLAY_RESUMED = "foundation.command_recovery.replay_resumed"


@dataclass(frozen=True)
class CommandRecoveryAudit:
    """Service-neutral audit event for crash recovery and partial-state quarantine."""

    audit_id: str
    owner_service: str
    action_type: CommandRecoveryAction | str
    reason: str
    idempotency_key: str | None = None
    trace_id: str | None = None
    occurred_at: datetime | str = field(default_factory=lambda: ensure_utc(None))
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = COMMAND_RECOVERY_AUDIT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("audit_id", "owner_service", "reason", "schema_version"):
            if not str(getattr(self, field_name)).strip():
                raise FoundationValidationError(f"command_recovery_audit.{field_name} is required")
        try:
            action_type = (
                self.action_type
                if isinstance(self.action_type, CommandRecoveryAction)
                else CommandRecoveryAction(str(self.action_type))
            )
        except ValueError as exc:
            allowed = ", ".join(item.value for item in CommandRecoveryAction)
            raise FoundationValidationError(f"command_recovery_audit.action_type must be one of: {allowed}") from exc
        object.__setattr__(self, "action_type", action_type)
        object.__setattr__(self, "occurred_at", ensure_utc(self.occurred_at))
        object.__setattr__(self, "metadata", dict(self.metadata))

    @classmethod
    def record(
        cls,
        *,
        owner_service: str,
        action_type: CommandRecoveryAction | str,
        reason: str,
        idempotency_key: str | None = None,
        trace_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "CommandRecoveryAudit":
        return cls(
            audit_id=foundation_id("audit"),
            owner_service=owner_service,
            action_type=action_type,
            reason=reason,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return drop_none(
            {
                "schema_version": self.schema_version,
                "audit_id": self.audit_id,
                "owner_service": self.owner_service,
                "action_type": self.action_type.value,
                "reason": self.reason,
                "idempotency_key": self.idempotency_key,
                "trace_id": self.trace_id,
                "occurred_at": ensure_utc(self.occurred_at).isoformat().replace("+00:00", "Z"),
                "metadata": dict(self.metadata),
            }
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CommandRecoveryAudit":
        return cls(
            audit_id=str(data["audit_id"]),
            owner_service=str(data["owner_service"]),
            action_type=str(data["action_type"]),
            reason=str(data["reason"]),
            idempotency_key=data.get("idempotency_key"),
            trace_id=data.get("trace_id"),
            occurred_at=str(data["occurred_at"]),
            metadata=dict(data.get("metadata", {})),
            schema_version=str(data.get("schema_version", COMMAND_RECOVERY_AUDIT_SCHEMA_VERSION)),
        )


def command_recovery_entry(
    record: IdempotencyRecord,
    *,
    result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialize a durable command-recovery ledger entry."""
    entry = {"idempotency_record": record.to_dict()}
    if result is not None:
        entry["result"] = json.loads(json.dumps(dict(result)))
    return entry


def load_command_recovery_entries(
    raw_entries: Mapping[str, Any],
    *,
    owner_service: str,
    operation_type: str,
) -> tuple[dict[str, dict[str, Any]], list[CommandRecoveryAudit]]:
    """Validate durable ledger entries and quarantine malformed partial state.

    Services keep ownership of their storage layout, while this helper provides
    shared validation semantics for foundation idempotency records.
    """
    loaded: dict[str, dict[str, Any]] = {}
    audits: list[CommandRecoveryAudit] = []
    for key, raw_entry in raw_entries.items():
        try:
            if not isinstance(raw_entry, Mapping):
                raise FoundationValidationError("ledger entry must be a mapping")
            raw_record = raw_entry.get("idempotency_record")
            if raw_record is None:
                raw_record = raw_entry
            if not isinstance(raw_record, Mapping):
                raise FoundationValidationError("ledger entry idempotency_record must be a mapping")
            record = IdempotencyRecord.from_dict(raw_record)
            if record.operation_type != operation_type:
                raise FoundationValidationError(
                    f"unexpected operation_type={record.operation_type!r}"
                )
            result = raw_entry.get("result")
            if result is not None and not isinstance(result, Mapping):
                raise FoundationValidationError("ledger entry result must be a mapping")
            loaded[record.idempotency_key] = command_recovery_entry(
                record,
                result=dict(result) if isinstance(result, Mapping) else None,
            )
        except Exception as exc:
            audits.append(
                CommandRecoveryAudit.record(
                    owner_service=owner_service,
                    action_type=CommandRecoveryAction.QUARANTINED,
                    reason=f"quarantined corrupt command recovery entry: {exc}",
                    idempotency_key=str(key),
                    metadata={"entry_key": str(key), "error_type": type(exc).__name__},
                )
            )
    return loaded, audits


def idempotency_record_from_entry(entry: Mapping[str, Any]) -> IdempotencyRecord:
    raw_record = entry.get("idempotency_record")
    if not isinstance(raw_record, Mapping):
        raise FoundationValidationError("ledger entry idempotency_record must be a mapping")
    return IdempotencyRecord.from_dict(raw_record)


def is_recoverable_inflight_status(status: IdempotencyStatus | str) -> bool:
    value = status.value if isinstance(status, IdempotencyStatus) else str(status)
    return value in {IdempotencyStatus.RESERVED.value, IdempotencyStatus.EXECUTING.value}
