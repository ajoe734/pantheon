"""EP5-002-V2: Promotion readiness validator.

Consumes a PromotionReadinessPacket and derives typed BlockingReason entries
covering: missing evidence, missing approvals, expired/revoked approvals, and
conflicting fail-closed flags.  The caller receives a plain list so it can
decide whether to update the packet or branch on the result.

Usage::

    from services.governance.promotion_readiness.validator import validate_readiness

    reasons = validate_readiness(packet)
    if not reasons:
        # safe to promote
"""
from __future__ import annotations

from typing import Any

from services.governance.promotion_readiness.packet_model import (
    BLOCKING_STATUSES,
    PASSING_APPROVAL_STATUSES,
    PASSING_STATUSES,
    UNSAFE_TRUE_FLAGS,
    BlockingReason,
    PromotionReadinessPacket,
)

# ---------------------------------------------------------------------------
# Blocking-reason codes (stable string constants)
# ---------------------------------------------------------------------------

CODE_MISSING_EVIDENCE = "missing_evidence"
CODE_EVIDENCE_NOT_PASSING = "evidence_not_passing"
CODE_GATE_NOT_PASSING = "gate_not_passing"
CODE_MISSING_REQUIRED_APPROVAL = "missing_required_approval"
CODE_APPROVAL_NOT_RECORDED = "approval_not_recorded"
CODE_APPROVAL_EXPIRED = "approval_expired"
CODE_APPROVAL_REVOKED = "approval_revoked"
CODE_APPROVAL_STATUS_BLOCKING = "approval_status_blocking"
CODE_UNSAFE_FLAG_ENABLED = "unsafe_flag_enabled"
CODE_CONFLICTING_CAN_PROCEED = "conflicting_can_proceed"


def _reason(code: str, message: str, *, source_ref: str | None = None, details: dict[str, Any] | None = None) -> BlockingReason:
    return BlockingReason(
        code=code,
        message=message,
        severity="blocking",
        source_ref=source_ref,
        details=details or {},
    )


# ---------------------------------------------------------------------------
# Per-check helpers
# ---------------------------------------------------------------------------

def _check_evidence(packet: PromotionReadinessPacket) -> list[BlockingReason]:
    reasons: list[BlockingReason] = []
    ev = packet.evidence

    # Required keys that were never provided at all
    provided_keys = {item.key for item in ev.provided}
    for key in ev.required:
        if key not in provided_keys:
            reasons.append(_reason(
                CODE_MISSING_EVIDENCE,
                f"Required evidence key '{key}' was never provided",
                details={"missing_key": key},
            ))

    # Also honour the pre-computed missing list (covers A2.1 packets)
    for key in ev.missing:
        if key not in provided_keys:
            # Already emitted above; skip duplicates
            continue
        reasons.append(_reason(
            CODE_MISSING_EVIDENCE,
            f"Evidence key '{key}' is listed as missing",
            details={"missing_key": key},
        ))

    # Provided items whose status is not passing
    for item in ev.provided:
        if item.status in BLOCKING_STATUSES:
            reasons.append(_reason(
                CODE_EVIDENCE_NOT_PASSING,
                f"Evidence '{item.key}' has non-passing status: {item.status}",
                source_ref=item.path,
                details={"evidence_key": item.key, "status": item.status},
            ))

    # Gate results that are not passing
    for gate in ev.gate_results:
        if gate.status not in PASSING_STATUSES:
            reasons.append(_reason(
                CODE_GATE_NOT_PASSING,
                f"Gate '{gate.gate}' has non-passing status: {gate.status}",
                source_ref=gate.source_ref,
                details={"gate": gate.gate, "status": gate.status},
            ))

    return reasons


def _check_approvals(packet: PromotionReadinessPacket) -> list[BlockingReason]:
    reasons: list[BlockingReason] = []
    appr = packet.approval

    for req in (appr.risk_owner, appr.operator):
        if not req.required:
            continue

        state = req.state or ""

        if not req.recorded and state not in PASSING_APPROVAL_STATUSES:
            reasons.append(_reason(
                CODE_APPROVAL_NOT_RECORDED,
                f"Required approval '{req.role}' has not been recorded",
                source_ref=req.source_ref,
                details={"role": req.role, "state": state},
            ))
            continue

        if state == "expired" or req.revoked_at is not None:
            # revoked_at takes precedence
            if req.revoked_at is not None:
                reasons.append(_reason(
                    CODE_APPROVAL_REVOKED,
                    f"Approval for '{req.role}' was revoked at {req.revoked_at}",
                    source_ref=req.source_ref,
                    details={"role": req.role, "revoked_at": req.revoked_at},
                ))
            else:
                reasons.append(_reason(
                    CODE_APPROVAL_EXPIRED,
                    f"Approval for '{req.role}' has expired (expires_at={req.expires_at})",
                    source_ref=req.source_ref,
                    details={"role": req.role, "expires_at": req.expires_at},
                ))
            continue

        if state in {"pending", "rejected"} or (req.required and state not in PASSING_APPROVAL_STATUSES):
            reasons.append(_reason(
                CODE_MISSING_REQUIRED_APPROVAL,
                f"Required approval '{req.role}' is in non-passing state: {state}",
                source_ref=req.source_ref,
                details={"role": req.role, "state": state},
            ))

    # Whole-subtree approval status
    status = appr.status
    if status and status not in PASSING_APPROVAL_STATUSES:
        reasons.append(_reason(
            CODE_APPROVAL_STATUS_BLOCKING,
            f"Approval subtree status is blocking: {status}",
            details={"approval_status": status},
        ))

    return reasons


def _check_flags(packet: PromotionReadinessPacket) -> list[BlockingReason]:
    reasons: list[BlockingReason] = []
    unsafe = packet.flags.unsafe_true_flags()
    for flag in unsafe:
        reasons.append(_reason(
            CODE_UNSAFE_FLAG_ENABLED,
            f"Fail-closed flag '{flag}' is enabled (must stay false for promotion)",
            details={"flag": flag},
        ))
    return reasons


def _check_consistency(packet: PromotionReadinessPacket, derived: list[BlockingReason]) -> list[BlockingReason]:
    """Detect conflicting packet state: can_proceed=true but blockers exist."""
    reasons: list[BlockingReason] = []
    if packet.can_proceed and (packet.blocking_reasons or derived):
        reasons.append(_reason(
            CODE_CONFLICTING_CAN_PROCEED,
            "Packet claims can_proceed=true but blocking conditions exist",
            details={
                "existing_blocking_reasons_count": len(packet.blocking_reasons),
                "derived_blocking_reasons_count": len(derived),
            },
        ))
    return reasons


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_readiness(packet: PromotionReadinessPacket) -> list[BlockingReason]:
    """Return a list of typed BlockingReason items for the given packet.

    An empty list means the packet has no derived blocking conditions.
    The caller is responsible for deciding whether to honour pre-existing
    packet.blocking_reasons in addition to the returned list.
    """
    evidence_reasons = _check_evidence(packet)
    approval_reasons = _check_approvals(packet)
    flag_reasons = _check_flags(packet)
    derived = evidence_reasons + approval_reasons + flag_reasons
    consistency_reasons = _check_consistency(packet, derived)
    return derived + consistency_reasons


def is_ready(packet: PromotionReadinessPacket) -> bool:
    """Return True iff validate_readiness produces no blocking reasons."""
    return len(validate_readiness(packet)) == 0
