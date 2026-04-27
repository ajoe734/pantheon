"""Policy decision value object."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from .exceptions import FoundationValidationError
from .serialization import drop_none, ensure_utc, foundation_id
from .types import ActorRef, EnvironmentScope


class PolicyDecisionValue(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    REQUIRE_MFA = "require_mfa"


@dataclass(frozen=True)
class PolicyDecision:
    decision_id: str
    policy_id: str
    policy_version: str
    decision: PolicyDecisionValue | str
    actor_ref: ActorRef
    action: str
    target_ref: str
    environment: EnvironmentScope
    reasons: tuple[str, ...] | list[str]
    evaluated_at: datetime | str
    trace_id: str

    def __post_init__(self) -> None:
        for field_name in ("decision_id", "policy_id", "policy_version", "action", "target_ref", "trace_id"):
            if not str(getattr(self, field_name)).strip():
                raise FoundationValidationError(f"policy_decision.{field_name} is required")
        if isinstance(self.decision, PolicyDecisionValue):
            decision = self.decision
        else:
            try:
                decision = PolicyDecisionValue(str(self.decision))
            except ValueError as exc:
                allowed = ", ".join(item.value for item in PolicyDecisionValue)
                raise FoundationValidationError(f"policy_decision.decision must be one of: {allowed}") from exc
        if not isinstance(self.actor_ref, ActorRef):
            raise FoundationValidationError("policy_decision.actor_ref must be an ActorRef")
        if not isinstance(self.environment, EnvironmentScope):
            raise FoundationValidationError("policy_decision.environment must be an EnvironmentScope")
        reasons = tuple(str(reason).strip() for reason in self.reasons if str(reason).strip())
        if decision != PolicyDecisionValue.ALLOW and not reasons:
            raise FoundationValidationError("non-allow policy decisions require at least one reason")
        object.__setattr__(self, "decision", decision)
        object.__setattr__(self, "reasons", reasons)
        object.__setattr__(self, "evaluated_at", ensure_utc(self.evaluated_at))

    @property
    def is_allowed(self) -> bool:
        return self.decision == PolicyDecisionValue.ALLOW

    @property
    def is_denied(self) -> bool:
        return self.decision == PolicyDecisionValue.DENY

    @classmethod
    def make(
        cls,
        *,
        policy_id: str,
        policy_version: str,
        decision: PolicyDecisionValue | str,
        actor_ref: ActorRef,
        action: str,
        target_ref: str,
        environment: EnvironmentScope,
        trace_id: str,
        reasons: list[str] | tuple[str, ...] = (),
    ) -> "PolicyDecision":
        return cls(
            decision_id=foundation_id("poldec"),
            policy_id=policy_id,
            policy_version=policy_version,
            decision=decision,
            actor_ref=actor_ref,
            action=action,
            target_ref=target_ref,
            environment=environment,
            reasons=reasons,
            evaluated_at=ensure_utc(None),
            trace_id=trace_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return drop_none(
            {
                "decision_id": self.decision_id,
                "policy_id": self.policy_id,
                "policy_version": self.policy_version,
                "decision": self.decision.value,
                "actor_ref": self.actor_ref.to_dict(),
                "action": self.action,
                "target_ref": self.target_ref,
                "environment": self.environment.to_dict(),
                "reasons": list(self.reasons),
                "evaluated_at": ensure_utc(self.evaluated_at).isoformat().replace("+00:00", "Z"),
                "trace_id": self.trace_id,
            }
        )
