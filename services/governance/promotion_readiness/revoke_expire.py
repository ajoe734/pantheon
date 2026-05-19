"""Store-level HumanGateDecision lifecycle operations.

Wraps the pure functions in signature_lifecycle.py with HumanGateDecisionStore
access so callers can revoke, expire, withdraw, supersede, or recompute a
stored decision in a single call.

All mutating functions:
  1. Load the current decision from the store.
  2. Apply the requested lifecycle transformation.
  3. Persist the updated decision back to the store.
  4. Return the updated HumanGateDecision.
"""
from __future__ import annotations

from typing import Sequence

from services.governance.human_gate.decision_model import HumanGateDecision
from services.governance.human_gate.signature_lifecycle import (
    LifecycleError,
    enforce_ttl,
    expire_decision,
    is_terminal,
    recompute_can_proceed,
    revoke_signature,
    supersede_decision,
    update_blocking_reasons,
    withdraw_decision,
)
from services.governance.promotion_readiness.signoff_api import (
    HumanGateDecisionStore,
    SignoffApiError,
)


class RevokeExpireError(SignoffApiError):
    """Raised when a revoke/expire/withdraw/supersede operation fails."""


def _load(store: HumanGateDecisionStore, decision_id: str) -> HumanGateDecision:
    decision = store.get(decision_id)
    if decision is None:
        raise RevokeExpireError(f"decision not found: {decision_id}")
    return decision


def revoke_decision(
    store: HumanGateDecisionStore,
    decision_id: str,
    *,
    signature_id: str,
    reason: str | None = None,
    revoked_at: str | None = None,
) -> HumanGateDecision:
    """Revoke one signature on the stored decision and persist the result.

    The decision is recomputed after the signature is revoked — if the
    revoked signature was the last active approval, the decision transitions
    back to pending or blocked depending on readiness input.
    """
    decision = _load(store, decision_id)
    try:
        updated = revoke_signature(decision, signature_id, reason=reason, revoked_at=revoked_at)
    except LifecycleError as exc:
        raise RevokeExpireError(str(exc)) from exc
    return store.put(updated)


def expire_decision_in_store(
    store: HumanGateDecisionStore,
    decision_id: str,
    *,
    reason: str | None = None,
    expired_at: str | None = None,
) -> HumanGateDecision:
    """Transition a stored decision to 'expired' and persist it."""
    decision = _load(store, decision_id)
    try:
        updated = expire_decision(decision, reason=reason, expired_at=expired_at)
    except LifecycleError as exc:
        raise RevokeExpireError(str(exc)) from exc
    return store.put(updated)


def withdraw_decision_in_store(
    store: HumanGateDecisionStore,
    decision_id: str,
    *,
    reason: str | None = None,
    withdrawn_at: str | None = None,
) -> HumanGateDecision:
    """Transition a stored decision to 'withdrawn' and persist it."""
    decision = _load(store, decision_id)
    try:
        updated = withdraw_decision(decision, reason=reason, withdrawn_at=withdrawn_at)
    except LifecycleError as exc:
        raise RevokeExpireError(str(exc)) from exc
    return store.put(updated)


def supersede_decision_in_store(
    store: HumanGateDecisionStore,
    decision_id: str,
    superseded_by_id: str,
    *,
    reason: str | None = None,
    superseded_at: str | None = None,
) -> HumanGateDecision:
    """Transition a stored decision to 'superseded' and persist it."""
    decision = _load(store, decision_id)
    try:
        updated = supersede_decision(
            decision,
            superseded_by_id,
            reason=reason,
            superseded_at=superseded_at,
        )
    except LifecycleError as exc:
        raise RevokeExpireError(str(exc)) from exc
    return store.put(updated)


def recompute_can_proceed_in_store(
    store: HumanGateDecisionStore,
    decision_id: str,
) -> HumanGateDecision:
    """Recompute can_proceed for a stored decision and persist the result."""
    decision = _load(store, decision_id)
    updated = recompute_can_proceed(decision)
    return store.put(updated)


def update_blocking_reasons_in_store(
    store: HumanGateDecisionStore,
    decision_id: str,
    blocking_reasons: Sequence[str],
) -> HumanGateDecision:
    """Replace blocking_reasons on a stored decision and persist."""
    decision = _load(store, decision_id)
    try:
        updated = update_blocking_reasons(decision, blocking_reasons)
    except LifecycleError as exc:
        raise RevokeExpireError(str(exc)) from exc
    return store.put(updated)


def enforce_ttl_in_store(
    store: HumanGateDecisionStore,
    decision_id: str,
    *,
    ttl_seconds: float,
    now: str | None = None,
) -> HumanGateDecision:
    """Expire a stored decision if it has exceeded its TTL and persist."""
    decision = _load(store, decision_id)
    updated = enforce_ttl(decision, ttl_seconds=ttl_seconds, now=now)
    if updated is not decision:
        return store.put(updated)
    return decision


def enforce_ttl_for_all(
    store: HumanGateDecisionStore,
    *,
    ttl_seconds: float,
    now: str | None = None,
) -> list[str]:
    """Expire all non-terminal decisions in the store that exceed TTL.

    Returns the list of decision_ids that were expired.
    """
    expired_ids: list[str] = []
    for decision_id, decision in list(store._items.items()):
        if is_terminal(decision):
            continue
        updated = enforce_ttl(decision, ttl_seconds=ttl_seconds, now=now)
        if updated is not decision:
            store.put(updated)
            expired_ids.append(decision_id)
    return expired_ids
