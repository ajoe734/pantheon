"""Audit action value object."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from .envelopes import TraceContext
from .exceptions import FoundationValidationError
from .serialization import drop_none, ensure_utc, foundation_id, sha256_checksum
from .types import ActorRef, EnvironmentScope


@dataclass(frozen=True)
class AuditAction:
    action_id: str
    actor_ref: ActorRef
    action_type: str
    target_ref: str
    environment: EnvironmentScope
    reason: str
    trace_id: str
    correlation_id: str
    timestamp: datetime | str
    payload_checksum: str
    before_state_ref: str | None = None
    after_state_ref: str | None = None
    policy_decision_ref: str | None = None
    approval_token_ref: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "action_id",
            "action_type",
            "target_ref",
            "reason",
            "trace_id",
            "correlation_id",
            "payload_checksum",
        ):
            if not str(getattr(self, field_name)).strip():
                raise FoundationValidationError(f"audit_action.{field_name} is required")
        if not isinstance(self.actor_ref, ActorRef):
            raise FoundationValidationError("audit_action.actor_ref must be an ActorRef")
        if not isinstance(self.environment, EnvironmentScope):
            raise FoundationValidationError("audit_action.environment must be an EnvironmentScope")
        object.__setattr__(self, "timestamp", ensure_utc(self.timestamp))
        object.__setattr__(self, "metadata", dict(self.metadata))

    @classmethod
    def record(
        cls,
        *,
        actor_ref: ActorRef,
        action_type: str,
        target_ref: str,
        environment: EnvironmentScope,
        reason: str,
        trace: TraceContext,
        payload: Any,
        before_state_ref: str | None = None,
        after_state_ref: str | None = None,
        policy_decision_ref: str | None = None,
        approval_token_ref: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "AuditAction":
        return cls(
            action_id=foundation_id("audit"),
            actor_ref=actor_ref,
            action_type=action_type,
            target_ref=target_ref,
            environment=environment,
            reason=reason,
            trace_id=trace.trace_id,
            correlation_id=trace.correlation_id,
            timestamp=ensure_utc(None),
            payload_checksum=sha256_checksum(payload),
            before_state_ref=before_state_ref,
            after_state_ref=after_state_ref,
            policy_decision_ref=policy_decision_ref,
            approval_token_ref=approval_token_ref,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return drop_none(
            {
                "action_id": self.action_id,
                "actor_ref": self.actor_ref.to_dict(),
                "action_type": self.action_type,
                "target_ref": self.target_ref,
                "environment": self.environment.to_dict(),
                "reason": self.reason,
                "before_state_ref": self.before_state_ref,
                "after_state_ref": self.after_state_ref,
                "policy_decision_ref": self.policy_decision_ref,
                "approval_token_ref": self.approval_token_ref,
                "trace_id": self.trace_id,
                "correlation_id": self.correlation_id,
                "timestamp": ensure_utc(self.timestamp).isoformat().replace("+00:00", "Z"),
                "payload_checksum": self.payload_checksum,
                "metadata": dict(self.metadata),
            }
        )
