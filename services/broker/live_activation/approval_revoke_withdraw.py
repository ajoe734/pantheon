"""Broker live-activation approval revoke / withdraw model.

This module wraps the HumanGateDecision lifecycle helpers for the broker live
activation dual gate. It does not persist decisions, dispatch runtime work, or
enable broker live flags; callers receive an explicit applied/blocked result.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Mapping

from services.governance.human_gate.decision_model import (
    HumanGateDecision,
    HumanGateDecisionError,
    HumanGateSignature,
)
from services.governance.human_gate.signature_lifecycle import (
    LifecycleError,
    revoke_signature,
    withdraw_decision,
)


MODEL_VERSION = "1.0"
MODEL_SOURCE = "2026-05-19 broker live activation approval revoke/withdraw"

ACTION_REVOKE = "revoke"
ACTION_WITHDRAW = "withdraw"
APPLIED = "applied"
BLOCKED = "blocked"

AUTHORIZED_APPROVAL_ROLES = ("risk_owner", "operator")
BROKER_LIVE_ENVIRONMENTS = ("live", "production")


class ApprovalLifecycleModelError(ValueError):
    """Raised by strict callers when a revoke/withdraw request is blocked."""


@dataclass(frozen=True)
class ApprovalLifecycleIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class ApprovalLifecycleRequest:
    action: str
    actor_role: str
    actor_id: str
    reason: str
    target_role: str | None = None
    signature_id: str | None = None
    occurred_at: str | None = None
    source_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ApprovalLifecycleRequest":
        if not isinstance(data, Mapping):
            raise ApprovalLifecycleModelError("approval lifecycle request must be an object")
        known = {
            "action",
            "actor_role",
            "actor_id",
            "reason",
            "target_role",
            "signature_id",
            "occurred_at",
            "source_ref",
        }
        return cls(
            action=_text(data.get("action"), "action"),
            actor_role=_text(data.get("actor_role"), "actor_role"),
            actor_id=_text(data.get("actor_id"), "actor_id"),
            reason=_text(data.get("reason"), "reason"),
            target_role=_optional_text(data.get("target_role")),
            signature_id=_optional_text(data.get("signature_id")),
            occurred_at=_optional_text(data.get("occurred_at")),
            source_ref=_optional_text(data.get("source_ref")),
            metadata={key: copy.deepcopy(value) for key, value in data.items() if key not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "action": self.action,
            "actor_role": self.actor_role,
            "actor_id": self.actor_id,
            "reason": self.reason,
        }
        if self.target_role:
            data["target_role"] = self.target_role
        if self.signature_id:
            data["signature_id"] = self.signature_id
        if self.occurred_at:
            data["occurred_at"] = self.occurred_at
        if self.source_ref:
            data["source_ref"] = self.source_ref
        data.update(copy.deepcopy(self.metadata))
        return data


@dataclass(frozen=True)
class ApprovalLifecycleResult:
    version: str
    source: str
    action: str
    status: str
    applied: bool
    decision_id: str | None
    actor_role: str | None
    actor_id: str | None
    target_role: str | None
    reason: str | None
    before_status: str | None
    after_status: str | None
    before_can_proceed: bool | None
    after_can_proceed: bool | None
    updated_decision: HumanGateDecision | None = None
    signature_id: str | None = None
    source_ref: str | None = None
    blocking_reasons: tuple[str, ...] = ()
    issues: tuple[ApprovalLifecycleIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source": self.source,
            "action": self.action,
            "status": self.status,
            "applied": self.applied,
            "decision_id": self.decision_id,
            "actor_role": self.actor_role,
            "actor_id": self.actor_id,
            "target_role": self.target_role,
            "signature_id": self.signature_id,
            "reason": self.reason,
            "source_ref": self.source_ref,
            "before_status": self.before_status,
            "after_status": self.after_status,
            "before_can_proceed": self.before_can_proceed,
            "after_can_proceed": self.after_can_proceed,
            "blocking_reasons": list(self.blocking_reasons),
            "issues": [issue.to_dict() for issue in self.issues],
            "updated_decision": (
                self.updated_decision.to_dict()
                if self.updated_decision is not None
                else None
            ),
        }


def apply_approval_lifecycle_request(
    decision: HumanGateDecision | Mapping[str, Any],
    request: ApprovalLifecycleRequest | Mapping[str, Any],
) -> ApprovalLifecycleResult:
    """Apply a broker-live approval revoke/withdraw request fail-closed."""

    try:
        normalized_decision = _decision_from(decision)
    except HumanGateDecisionError as exc:
        issue = ApprovalLifecycleIssue(
            "invalid_human_gate_decision",
            "decision",
            str(exc),
        )
        return _blocked_result(None, None, (issue,))

    try:
        normalized_request = _request_from(request)
    except ApprovalLifecycleModelError as exc:
        issue = ApprovalLifecycleIssue(
            "invalid_lifecycle_request",
            "request",
            str(exc),
        )
        return _blocked_result(normalized_decision, None, (issue,))

    issues = _validate_common(normalized_decision, normalized_request)
    if issues:
        return _blocked_result(normalized_decision, normalized_request, issues)

    if normalized_request.action == ACTION_REVOKE:
        return _apply_revoke(normalized_decision, normalized_request)
    if normalized_request.action == ACTION_WITHDRAW:
        return _apply_withdraw(normalized_decision, normalized_request)

    issue = ApprovalLifecycleIssue(
        "unsupported_action",
        "request.action",
        "action must be revoke or withdraw",
    )
    return _blocked_result(normalized_decision, normalized_request, (issue,))


def apply_approval_lifecycle_request_or_raise(
    decision: HumanGateDecision | Mapping[str, Any],
    request: ApprovalLifecycleRequest | Mapping[str, Any],
) -> ApprovalLifecycleResult:
    result = apply_approval_lifecycle_request(decision, request)
    if not result.applied:
        raise ApprovalLifecycleModelError("; ".join(result.blocking_reasons))
    return result


def revoke_approval(
    decision: HumanGateDecision | Mapping[str, Any],
    *,
    actor_role: str,
    actor_id: str,
    reason: str,
    target_role: str | None = None,
    signature_id: str | None = None,
    revoked_at: str | None = None,
    source_ref: str | None = None,
) -> ApprovalLifecycleResult:
    request = ApprovalLifecycleRequest(
        action=ACTION_REVOKE,
        actor_role=actor_role,
        actor_id=actor_id,
        reason=reason,
        target_role=target_role,
        signature_id=signature_id,
        occurred_at=revoked_at,
        source_ref=source_ref,
    )
    return apply_approval_lifecycle_request(decision, request)


def withdraw_approval(
    decision: HumanGateDecision | Mapping[str, Any],
    *,
    actor_role: str,
    actor_id: str,
    reason: str,
    withdrawn_at: str | None = None,
    source_ref: str | None = None,
) -> ApprovalLifecycleResult:
    request = ApprovalLifecycleRequest(
        action=ACTION_WITHDRAW,
        actor_role=actor_role,
        actor_id=actor_id,
        reason=reason,
        occurred_at=withdrawn_at,
        source_ref=source_ref,
    )
    return apply_approval_lifecycle_request(decision, request)


def _apply_revoke(
    decision: HumanGateDecision,
    request: ApprovalLifecycleRequest,
) -> ApprovalLifecycleResult:
    target_role = request.target_role or request.actor_role
    if target_role != request.actor_role:
        issue = ApprovalLifecycleIssue(
            "unauthorized_role_scope",
            "request.target_role",
            "risk-owner/operator approvals may only be revoked by the same role",
        )
        return _blocked_result(decision, request, (issue,), target_role=target_role)

    signature = _select_active_signature(decision, target_role, request.signature_id)
    if isinstance(signature, ApprovalLifecycleIssue):
        return _blocked_result(decision, request, (signature,), target_role=target_role)

    try:
        updated = revoke_signature(
            decision,
            signature.signature_id,
            reason=request.reason,
            revoked_at=request.occurred_at,
        )
    except LifecycleError as exc:
        issue = ApprovalLifecycleIssue(
            "lifecycle_transition_blocked",
            "decision.status",
            str(exc),
        )
        return _blocked_result(decision, request, (issue,), target_role=target_role)

    return _applied_result(
        before=decision,
        after=updated,
        request=request,
        target_role=target_role,
        signature_id=signature.signature_id,
    )


def _apply_withdraw(
    decision: HumanGateDecision,
    request: ApprovalLifecycleRequest,
) -> ApprovalLifecycleResult:
    if decision.status == "approved":
        issue = ApprovalLifecycleIssue(
            "approved_decision_requires_revoke",
            "decision.status",
            "approved broker-live decisions must be revoked, not withdrawn",
        )
        return _blocked_result(decision, request, (issue,), target_role=request.actor_role)

    try:
        updated = withdraw_decision(
            decision,
            reason=request.reason,
            withdrawn_at=request.occurred_at,
        )
    except LifecycleError as exc:
        issue = ApprovalLifecycleIssue(
            "lifecycle_transition_blocked",
            "decision.status",
            str(exc),
        )
        return _blocked_result(decision, request, (issue,), target_role=request.actor_role)

    return _applied_result(
        before=decision,
        after=updated,
        request=request,
        target_role=request.actor_role,
        signature_id=None,
    )


def _validate_common(
    decision: HumanGateDecision,
    request: ApprovalLifecycleRequest,
) -> tuple[ApprovalLifecycleIssue, ...]:
    issues: list[ApprovalLifecycleIssue] = []
    if request.action not in {ACTION_REVOKE, ACTION_WITHDRAW}:
        issues.append(
            ApprovalLifecycleIssue(
                "unsupported_action",
                "request.action",
                "action must be revoke or withdraw",
            )
        )
    if request.actor_role not in AUTHORIZED_APPROVAL_ROLES:
        issues.append(
            ApprovalLifecycleIssue(
                "unauthorized_actor_role",
                "request.actor_role",
                "actor_role must be risk_owner or operator",
            )
        )
    if request.target_role and request.target_role not in AUTHORIZED_APPROVAL_ROLES:
        issues.append(
            ApprovalLifecycleIssue(
                "unauthorized_target_role",
                "request.target_role",
                "target_role must be risk_owner or operator",
            )
        )
    if decision.target_environment not in BROKER_LIVE_ENVIRONMENTS:
        issues.append(
            ApprovalLifecycleIssue(
                "non_live_human_gate_decision",
                "decision.target_environment",
                "broker live activation approval lifecycle requires live or production target_environment",
            )
        )
    return tuple(issues)


def _select_active_signature(
    decision: HumanGateDecision,
    target_role: str,
    signature_id: str | None,
) -> HumanGateSignature | ApprovalLifecycleIssue:
    signatures = tuple(
        signature
        for signature in decision.signatures
        if signature.role == target_role and signature.active_approval()
    )
    if signature_id:
        for signature in signatures:
            if signature.signature_id == signature_id:
                return signature
        return ApprovalLifecycleIssue(
            "active_signature_not_found",
            "request.signature_id",
            f"active {target_role} approval signature not found: {signature_id}",
        )
    if len(signatures) == 1:
        return signatures[0]
    if not signatures:
        return ApprovalLifecycleIssue(
            "active_signature_not_found",
            "request.target_role",
            f"active {target_role} approval signature not found",
        )
    return ApprovalLifecycleIssue(
        "ambiguous_active_signature",
        "request.signature_id",
        f"signature_id is required when multiple active {target_role} signatures exist",
    )


def _applied_result(
    *,
    before: HumanGateDecision,
    after: HumanGateDecision,
    request: ApprovalLifecycleRequest,
    target_role: str,
    signature_id: str | None,
) -> ApprovalLifecycleResult:
    return ApprovalLifecycleResult(
        version=MODEL_VERSION,
        source=MODEL_SOURCE,
        action=request.action,
        status=APPLIED,
        applied=True,
        decision_id=before.decision_id,
        actor_role=request.actor_role,
        actor_id=request.actor_id,
        target_role=target_role,
        reason=request.reason,
        before_status=before.status,
        after_status=after.status,
        before_can_proceed=before.can_proceed,
        after_can_proceed=after.can_proceed,
        updated_decision=after,
        signature_id=signature_id,
        source_ref=request.source_ref,
    )


def _blocked_result(
    decision: HumanGateDecision | None,
    request: ApprovalLifecycleRequest | None,
    issues: tuple[ApprovalLifecycleIssue, ...],
    *,
    target_role: str | None = None,
) -> ApprovalLifecycleResult:
    action = request.action if request is not None else ""
    blocking_reasons = tuple(issue.message for issue in issues)
    return ApprovalLifecycleResult(
        version=MODEL_VERSION,
        source=MODEL_SOURCE,
        action=action,
        status=BLOCKED,
        applied=False,
        decision_id=decision.decision_id if decision is not None else None,
        actor_role=request.actor_role if request is not None else None,
        actor_id=request.actor_id if request is not None else None,
        target_role=target_role or (request.target_role if request is not None else None),
        reason=request.reason if request is not None else None,
        before_status=decision.status if decision is not None else None,
        after_status=decision.status if decision is not None else None,
        before_can_proceed=decision.can_proceed if decision is not None else None,
        after_can_proceed=decision.can_proceed if decision is not None else None,
        updated_decision=decision,
        signature_id=request.signature_id if request is not None else None,
        source_ref=request.source_ref if request is not None else None,
        blocking_reasons=blocking_reasons,
        issues=issues,
    )


def _decision_from(decision: HumanGateDecision | Mapping[str, Any]) -> HumanGateDecision:
    if isinstance(decision, HumanGateDecision):
        return decision
    if not isinstance(decision, Mapping):
        raise HumanGateDecisionError("decision must be a HumanGateDecision or mapping")
    return HumanGateDecision.from_dict(decision)


def _request_from(
    request: ApprovalLifecycleRequest | Mapping[str, Any],
) -> ApprovalLifecycleRequest:
    if isinstance(request, ApprovalLifecycleRequest):
        return request
    return ApprovalLifecycleRequest.from_dict(request)


def _text(value: Any, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ApprovalLifecycleModelError(f"{field_name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
