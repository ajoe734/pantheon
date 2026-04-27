"""Idempotency primitives for command and event admission."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from .exceptions import FoundationValidationError
from .serialization import drop_none, ensure_utc, sha256_checksum


class IdempotencyStatus(str, Enum):
    RESERVED = "reserved"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"


def request_hash(payload: Any) -> str:
    """Return the canonical request hash used by idempotency records."""
    return sha256_checksum(payload)


@dataclass(frozen=True)
class IdempotencyRecord:
    idempotency_key: str
    operation_type: str
    target_ref: str
    request_hash: str
    first_seen_at: datetime | str
    last_seen_at: datetime | str
    status: IdempotencyStatus | str
    trace_id: str
    result_ref: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("idempotency_key", "operation_type", "target_ref", "request_hash", "trace_id"):
            if not str(getattr(self, field_name)).strip():
                raise FoundationValidationError(f"idempotency.{field_name} is required")
        if isinstance(self.status, IdempotencyStatus):
            status = self.status
        else:
            try:
                status = IdempotencyStatus(str(self.status))
            except ValueError as exc:
                allowed = ", ".join(item.value for item in IdempotencyStatus)
                raise FoundationValidationError(f"idempotency.status must be one of: {allowed}") from exc
        first_seen_at = ensure_utc(self.first_seen_at)
        last_seen_at = ensure_utc(self.last_seen_at)
        if last_seen_at < first_seen_at:
            raise FoundationValidationError("last_seen_at cannot be before first_seen_at")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "first_seen_at", first_seen_at)
        object.__setattr__(self, "last_seen_at", last_seen_at)

    @classmethod
    def reserve(
        cls,
        *,
        idempotency_key: str,
        operation_type: str,
        target_ref: str,
        request_payload: Any,
        trace_id: str,
        seen_at: datetime | str | None = None,
    ) -> "IdempotencyRecord":
        timestamp = ensure_utc(seen_at)
        return cls(
            idempotency_key=idempotency_key,
            operation_type=operation_type,
            target_ref=target_ref,
            request_hash=request_hash(request_payload),
            first_seen_at=timestamp,
            last_seen_at=timestamp,
            status=IdempotencyStatus.RESERVED,
            trace_id=trace_id,
        )

    def matches_request(self, request_payload: Any) -> bool:
        return self.request_hash == request_hash(request_payload)

    def with_status(
        self,
        status: IdempotencyStatus | str,
        *,
        result_ref: str | None = None,
        seen_at: datetime | str | None = None,
    ) -> "IdempotencyRecord":
        return IdempotencyRecord(
            idempotency_key=self.idempotency_key,
            operation_type=self.operation_type,
            target_ref=self.target_ref,
            request_hash=self.request_hash,
            first_seen_at=self.first_seen_at,
            last_seen_at=ensure_utc(seen_at),
            status=status,
            trace_id=self.trace_id,
            result_ref=result_ref if result_ref is not None else self.result_ref,
        )

    def to_dict(self) -> dict[str, Any]:
        return drop_none(
            {
                "idempotency_key": self.idempotency_key,
                "operation_type": self.operation_type,
                "target_ref": self.target_ref,
                "request_hash": self.request_hash,
                "first_seen_at": ensure_utc(self.first_seen_at).isoformat().replace("+00:00", "Z"),
                "last_seen_at": ensure_utc(self.last_seen_at).isoformat().replace("+00:00", "Z"),
                "status": self.status.value,
                "result_ref": self.result_ref,
                "trace_id": self.trace_id,
            }
        )
