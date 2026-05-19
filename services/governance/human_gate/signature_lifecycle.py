"""HumanGateDecision lifecycle operations.

Pure functions that transform HumanGateDecision objects without store access.
All functions return a new validated decision; the caller is responsible for
persisting the result via HumanGateDecisionStore.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Sequence

from services.governance.human_gate.decision_model import (
    CanProceedInput,
    HumanGateDecision,
    HumanGateDecisionError,
    TERMINAL_LIFECYCLE_STATUSES,
    validate_decision,
    utc_now,
)


class LifecycleError(HumanGateDecisionError):
    """Raised when a lifecycle transition is invalid."""


def _parse_utc(ts: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse timestamp: {ts!r}")


def is_terminal(decision: HumanGateDecision) -> bool:
    """Return True if the decision is in a terminal lifecycle state."""
    return decision.status in TERMINAL_LIFECYCLE_STATUSES


def is_active(decision: HumanGateDecision) -> bool:
    """Return True if the decision is not yet in a terminal lifecycle state."""
    return not is_terminal(decision)


def revoke_signature(
    decision: HumanGateDecision,
    signature_id: str,
    *,
    reason: str | None = None,
    revoked_at: str | None = None,
) -> HumanGateDecision:
    """Mark one signature as revoked and recompute decision state.

    Raises LifecycleError if the decision is already terminal or the
    signature_id is not found.
    """
    if is_terminal(decision):
        raise LifecycleError(
            f"cannot revoke signature on terminal decision: {decision.status}"
        )
    ts = revoked_at or utc_now()
    found = False
    new_signatures = []
    for sig in decision.signatures:
        if sig.signature_id == signature_id:
            if sig.revoked_at is not None:
                raise LifecycleError(f"signature already revoked: {signature_id}")
            new_signatures.append(replace(sig, revoked_at=ts))
            found = True
        else:
            new_signatures.append(sig)
    if not found:
        raise LifecycleError(f"signature not found: {signature_id}")
    updated = replace(
        decision,
        signatures=tuple(new_signatures),
        updated_at=utc_now(),
        reason=reason or decision.reason,
    )
    return validate_decision(updated)


def expire_decision(
    decision: HumanGateDecision,
    *,
    reason: str | None = None,
    expired_at: str | None = None,
) -> HumanGateDecision:
    """Transition decision to the 'expired' terminal state.

    Raises LifecycleError if the decision is already terminal.
    """
    if is_terminal(decision):
        raise LifecycleError(
            f"cannot expire already-terminal decision: {decision.status}"
        )
    ts = expired_at or utc_now()
    updated = replace(
        decision,
        status="expired",
        can_proceed=False,
        updated_at=ts,
        reason=reason or f"Decision expired at {ts}.",
    )
    return validate_decision(updated)


def withdraw_decision(
    decision: HumanGateDecision,
    *,
    reason: str | None = None,
    withdrawn_at: str | None = None,
) -> HumanGateDecision:
    """Transition decision to the 'withdrawn' terminal state.

    Use this when the proposer retracts the gate before it is approved.
    Raises LifecycleError if already terminal.
    """
    if is_terminal(decision):
        raise LifecycleError(
            f"cannot withdraw already-terminal decision: {decision.status}"
        )
    ts = withdrawn_at or utc_now()
    updated = replace(
        decision,
        status="withdrawn",
        can_proceed=False,
        updated_at=ts,
        reason=reason or f"Decision withdrawn at {ts}.",
    )
    return validate_decision(updated)


def supersede_decision(
    decision: HumanGateDecision,
    superseded_by_id: str,
    *,
    reason: str | None = None,
    superseded_at: str | None = None,
) -> HumanGateDecision:
    """Transition decision to the 'superseded' terminal state.

    Records the id of the new decision in metadata so the audit trail is clear.
    Raises LifecycleError if already terminal.
    """
    if is_terminal(decision):
        raise LifecycleError(
            f"cannot supersede already-terminal decision: {decision.status}"
        )
    if not superseded_by_id or not superseded_by_id.strip():
        raise LifecycleError("superseded_by_id must not be empty")
    ts = superseded_at or utc_now()
    updated_metadata = {**decision.metadata, "superseded_by": superseded_by_id}
    updated = replace(
        decision,
        status="superseded",
        can_proceed=False,
        updated_at=ts,
        reason=reason or f"Decision superseded by {superseded_by_id} at {ts}.",
        metadata=updated_metadata,
    )
    return validate_decision(updated)


def recompute_can_proceed(decision: HumanGateDecision) -> HumanGateDecision:
    """Re-run normalization to recompute can_proceed, status, and reason.

    Use after an out-of-band update to can_proceed_input (e.g., a blocking
    reason was resolved and the packet is now ready).  No-op for terminal
    decisions.
    """
    if is_terminal(decision):
        return decision
    return validate_decision(replace(decision, updated_at=utc_now()))


def update_blocking_reasons(
    decision: HumanGateDecision,
    blocking_reasons: Sequence[str],
) -> HumanGateDecision:
    """Replace blocking_reasons in can_proceed_input and recompute state.

    Pass an empty sequence to clear all blocking reasons.
    Raises LifecycleError for terminal decisions.
    """
    if is_terminal(decision):
        raise LifecycleError(
            f"cannot update blocking reasons on terminal decision: {decision.status}"
        )
    new_input = replace(
        decision.can_proceed_input,
        blocking_reasons=tuple(str(r) for r in blocking_reasons),
    )
    updated = replace(
        decision,
        can_proceed_input=new_input,
        updated_at=utc_now(),
    )
    return validate_decision(updated)


def enforce_ttl(
    decision: HumanGateDecision,
    *,
    ttl_seconds: float,
    now: str | None = None,
) -> HumanGateDecision:
    """Expire the decision if it has exceeded its TTL.

    TTL is measured from decision.created_at.  Returns the decision unchanged
    if it is already terminal, has no created_at, or is still within TTL.
    """
    if is_terminal(decision):
        return decision
    if not decision.created_at:
        return decision
    now_dt = _parse_utc(now) if now else datetime.now(timezone.utc)
    created_dt = _parse_utc(decision.created_at)
    elapsed = (now_dt - created_dt).total_seconds()
    if elapsed <= ttl_seconds:
        return decision
    expired_at = now or utc_now()
    return expire_decision(
        decision,
        reason=f"Decision TTL of {ttl_seconds}s exceeded (elapsed {elapsed:.0f}s).",
        expired_at=expired_at,
    )
