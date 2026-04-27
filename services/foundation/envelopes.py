"""Shared trace, command, and error envelopes."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from .exceptions import FoundationValidationError
from .serialization import drop_none, ensure_utc, foundation_id, sha256_checksum
from .types import ActorRef, AuthorityScope, EnvironmentScope

COMMAND_ENVELOPE_SCHEMA_VERSION = "command_envelope.v1"
ERROR_ENVELOPE_SCHEMA_VERSION = "error_envelope.v1"
TRACE_CONTEXT_SCHEMA_VERSION = "trace_context.v1"


class ErrorKind(str, Enum):
    VALIDATION = "validation"
    POLICY_DENIAL = "policy_denial"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    INVARIANT_VIOLATION = "invariant_violation"
    INTERNAL = "internal"


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    correlation_id: str
    environment: EnvironmentScope
    created_at: datetime | str = field(default_factory=lambda: ensure_utc(None))
    causation_id: str | None = None
    parent_span_id: str | None = None
    request_id: str | None = None
    idempotency_key: str | None = None
    actor_ref: ActorRef | None = None
    source_system: str = "pantheon"
    schema_version: str = TRACE_CONTEXT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("trace_id", "correlation_id", "source_system", "schema_version"):
            if not str(getattr(self, field_name)).strip():
                raise FoundationValidationError(f"trace.{field_name} is required")
        if not isinstance(self.environment, EnvironmentScope):
            raise FoundationValidationError("trace.environment must be an EnvironmentScope")
        if self.actor_ref is not None and not isinstance(self.actor_ref, ActorRef):
            raise FoundationValidationError("trace.actor_ref must be an ActorRef when provided")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))

    @classmethod
    def new(
        cls,
        *,
        environment: EnvironmentScope,
        actor_ref: ActorRef | None = None,
        source_system: str = "pantheon",
        correlation_id: str | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> "TraceContext":
        trace_id = foundation_id("trace")
        return cls(
            trace_id=trace_id,
            correlation_id=correlation_id or trace_id,
            environment=environment,
            actor_ref=actor_ref,
            source_system=source_system,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

    def child(
        self,
        *,
        source_system: str | None = None,
        request_id: str | None = None,
        causation_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> "TraceContext":
        return replace(
            self,
            causation_id=causation_id or self.request_id,
            parent_span_id=parent_span_id or self.trace_id,
            request_id=request_id,
            source_system=source_system or self.source_system,
            created_at=ensure_utc(None),
        )

    def with_idempotency_key(self, idempotency_key: str) -> "TraceContext":
        if not str(idempotency_key).strip():
            raise FoundationValidationError("idempotency_key is required")
        return replace(self, idempotency_key=idempotency_key)

    def to_dict(self) -> dict[str, Any]:
        return drop_none(
            {
                "schema_version": self.schema_version,
                "trace_id": self.trace_id,
                "correlation_id": self.correlation_id,
                "causation_id": self.causation_id,
                "parent_span_id": self.parent_span_id,
                "request_id": self.request_id,
                "idempotency_key": self.idempotency_key,
                "actor_ref": self.actor_ref.to_dict() if self.actor_ref else None,
                "environment": self.environment.to_dict(),
                "source_system": self.source_system,
                "created_at": ensure_utc(self.created_at).isoformat().replace("+00:00", "Z"),
            }
        )


@dataclass(frozen=True)
class CommandEnvelope:
    command_id: str
    command_type: str
    actor_ref: ActorRef
    authority_scope: AuthorityScope
    payload: Mapping[str, Any]
    trace: TraceContext
    requested_at: datetime | str = field(default_factory=lambda: ensure_utc(None))
    idempotency_key: str = ""
    expected_version: str | None = None
    approval_token: str | None = None
    schema_version: str = COMMAND_ENVELOPE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("command_id", "command_type", "idempotency_key", "schema_version"):
            if not str(getattr(self, field_name)).strip():
                raise FoundationValidationError(f"command.{field_name} is required")
        if not isinstance(self.actor_ref, ActorRef):
            raise FoundationValidationError("command.actor_ref must be an ActorRef")
        if not isinstance(self.authority_scope, AuthorityScope):
            raise FoundationValidationError("command.authority_scope must be an AuthorityScope")
        if not isinstance(self.trace, TraceContext):
            raise FoundationValidationError("command.trace must be a TraceContext")
        if self.trace.idempotency_key and self.trace.idempotency_key != self.idempotency_key:
            raise FoundationValidationError("command idempotency_key must match trace.idempotency_key")
        if self.trace.actor_ref and self.trace.actor_ref.actor_ref != self.actor_ref.actor_ref:
            raise FoundationValidationError("command actor_ref must match trace.actor_ref")
        if not isinstance(self.payload, Mapping):
            raise FoundationValidationError("command.payload must be a mapping")
        object.__setattr__(self, "payload", dict(self.payload))
        object.__setattr__(self, "requested_at", ensure_utc(self.requested_at))

    @classmethod
    def new(
        cls,
        *,
        command_type: str,
        actor_ref: ActorRef,
        authority_scope: AuthorityScope,
        payload: Mapping[str, Any],
        trace: TraceContext | None = None,
        idempotency_key: str | None = None,
        expected_version: str | None = None,
        approval_token: str | None = None,
    ) -> "CommandEnvelope":
        command_id = foundation_id("cmd")
        effective_key = idempotency_key or "idmp-" + sha256_checksum(
            {
                "command_type": command_type,
                "actor_ref": actor_ref,
                "authority_scope": authority_scope,
                "payload": payload,
            }
        )[:32]
        effective_trace = trace or TraceContext.new(
            environment=authority_scope.environment,
            actor_ref=actor_ref,
            idempotency_key=effective_key,
        )
        if effective_trace.idempotency_key is None:
            effective_trace = effective_trace.with_idempotency_key(effective_key)
        return cls(
            command_id=command_id,
            command_type=command_type,
            actor_ref=actor_ref,
            authority_scope=authority_scope,
            payload=payload,
            trace=effective_trace,
            idempotency_key=effective_key,
            expected_version=expected_version,
            approval_token=approval_token,
        )

    def to_dict(self) -> dict[str, Any]:
        return drop_none(
            {
                "schema_version": self.schema_version,
                "command_id": self.command_id,
                "command_type": self.command_type,
                "actor_ref": self.actor_ref.to_dict(),
                "authority_scope": self.authority_scope.to_dict(),
                "payload": dict(self.payload),
                "trace": self.trace.to_dict(),
                "requested_at": ensure_utc(self.requested_at).isoformat().replace("+00:00", "Z"),
                "idempotency_key": self.idempotency_key,
                "expected_version": self.expected_version,
                "approval_token": self.approval_token,
            }
        )


@dataclass(frozen=True)
class ErrorEnvelope:
    error_id: str
    error_code: str
    message: str
    error_kind: ErrorKind | str
    trace: TraceContext
    status_code: int = 400
    retryable: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)
    occurred_at: datetime | str = field(default_factory=lambda: ensure_utc(None))
    schema_version: str = ERROR_ENVELOPE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("error_id", "error_code", "message", "schema_version"):
            if not str(getattr(self, field_name)).strip():
                raise FoundationValidationError(f"error.{field_name} is required")
        if not isinstance(self.trace, TraceContext):
            raise FoundationValidationError("error.trace must be a TraceContext")
        if isinstance(self.error_kind, ErrorKind):
            kind = self.error_kind
        else:
            try:
                kind = ErrorKind(str(self.error_kind))
            except ValueError as exc:
                allowed = ", ".join(item.value for item in ErrorKind)
                raise FoundationValidationError(f"error_kind must be one of: {allowed}") from exc
        if int(self.status_code) < 100 or int(self.status_code) > 599:
            raise FoundationValidationError("status_code must be an HTTP status code")
        object.__setattr__(self, "error_kind", kind)
        object.__setattr__(self, "details", dict(self.details))
        object.__setattr__(self, "occurred_at", ensure_utc(self.occurred_at))

    @classmethod
    def validation(
        cls,
        *,
        message: str,
        trace: TraceContext,
        error_code: str = "VALIDATION_ERROR",
        details: Mapping[str, Any] | None = None,
    ) -> "ErrorEnvelope":
        return cls(
            error_id=foundation_id("err"),
            error_code=error_code,
            message=message,
            error_kind=ErrorKind.VALIDATION,
            trace=trace,
            details=details or {},
            status_code=422,
        )

    @classmethod
    def policy_denial(
        cls,
        *,
        message: str,
        trace: TraceContext,
        policy_decision_ref: str,
        details: Mapping[str, Any] | None = None,
    ) -> "ErrorEnvelope":
        merged_details = dict(details or {})
        merged_details["policy_decision_ref"] = policy_decision_ref
        return cls(
            error_id=foundation_id("err"),
            error_code="POLICY_DENIED",
            message=message,
            error_kind=ErrorKind.POLICY_DENIAL,
            trace=trace,
            details=merged_details,
            status_code=403,
        )

    def to_dict(self) -> dict[str, Any]:
        return drop_none(
            {
                "schema_version": self.schema_version,
                "error_id": self.error_id,
                "error_code": self.error_code,
                "message": self.message,
                "error_kind": self.error_kind.value,
                "trace": self.trace.to_dict(),
                "status_code": self.status_code,
                "retryable": self.retryable,
                "details": dict(self.details),
                "occurred_at": ensure_utc(self.occurred_at).isoformat().replace("+00:00", "Z"),
            }
        )
