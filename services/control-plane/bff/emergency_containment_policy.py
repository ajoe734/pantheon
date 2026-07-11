"""Risk-decreasing-only policy for emergency persona containment commands."""

from __future__ import annotations

from typing import Any, Dict


ALLOWED_TRIGGERS = frozenset({
    "drawdown_breach",
    "daily_loss_breach",
    "forced_kill",
    "binding_mismatch",
    "reconciliation_mismatch",
    "unresolved_incident",
    "hard_risk_breach",
    "stale_live_telemetry",
})

ALLOWED_ACTIONS = frozenset({
    "freeze",
    "reduce_capital",
    "suspend",
    "risk_off",
    "flatten",
    "rollback_allocation",
    "retire",
})

FORBIDDEN_ACTIONS = frozenset({
    "promote",
    "promote_to_canary",
    "promote_to_live",
    "increase_allocation",
    "create_canary",
    "create_live",
})


def validate_emergency_containment(params: Dict[str, Any]) -> None:
    """Reject incomplete commands and every known risk-increasing shape."""
    action = str(params.get("action") or params.get("containment_action") or "").strip().lower()
    trigger = str(params.get("trigger") or params.get("trigger_type") or "").strip().lower()

    if action in FORBIDDEN_ACTIONS or params.get("promote") or params.get("allocation_increase"):
        raise ValueError("emergency containment cannot promote or increase allocation")
    if action not in ALLOWED_ACTIONS:
        raise ValueError("unsupported emergency containment action")
    if trigger not in ALLOWED_TRIGGERS:
        raise ValueError("unsupported emergency containment trigger")

    evidence_refs = params.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not any(str(ref).strip() for ref in evidence_refs):
        raise ValueError("emergency containment requires evidence_refs")

    if action == "reduce_capital":
        current = params.get("current_weight")
        target = params.get("target_weight")
        if current is None or target is None or float(target) >= float(current):
            raise ValueError("emergency capital reduction must lower target_weight")

    if action == "rollback_allocation" and not str(params.get("rollback_ref") or "").strip():
        raise ValueError("allocation rollback requires rollback_ref")

    target_stage = str(params.get("target_stage") or "").strip().lower()
    if target_stage in {"canary_candidate", "canary_running", "live_candidate", "live_running"}:
        raise ValueError("emergency containment cannot promote a persona")


def containment_receipt_fields(params: Dict[str, Any]) -> Dict[str, Any]:
    """Project the evidence needed by downstream audit/review consumers."""
    return {
        "containment": True,
        "containment_action": params.get("action") or params.get("containment_action"),
        "trigger_type": params.get("trigger") or params.get("trigger_type"),
        "evidence_refs": list(params.get("evidence_refs") or []),
        "rollback_ref": params.get("rollback_ref"),
        "risk_direction": "decrease_only",
        "live_capital_side_effects": False,
    }
