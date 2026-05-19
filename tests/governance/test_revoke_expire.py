"""Tests for HumanGateDecision lifecycle operations.

Covers signature revocation, decision expiry/withdrawal/supersession,
can_proceed recomputation, blocking reason updates, and TTL enforcement
via both the pure lifecycle functions and the store-level wrappers.
"""
from __future__ import annotations

import pytest

from services.governance.human_gate.decision_model import (
    CanProceedInput,
    EvidenceReviewed,
    HumanGateDecision,
    HumanGateSignature,
    compute_evidence_hash,
    validate_decision,
)
from services.governance.human_gate.signature_lifecycle import (
    LifecycleError,
    enforce_ttl,
    expire_decision,
    is_active,
    is_terminal,
    recompute_can_proceed,
    revoke_signature,
    supersede_decision,
    update_blocking_reasons,
    withdraw_decision,
)
from services.governance.promotion_readiness.revoke_expire import (
    RevokeExpireError,
    enforce_ttl_for_all,
    enforce_ttl_in_store,
    expire_decision_in_store,
    recompute_can_proceed_in_store,
    revoke_decision,
    supersede_decision_in_store,
    update_blocking_reasons_in_store,
    withdraw_decision_in_store,
)
from services.governance.promotion_readiness.signoff_api import (
    HumanGateDecisionStore,
    SignoffAPI,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _evidence() -> tuple[EvidenceReviewed, ...]:
    return (
        EvidenceReviewed(
            key="broker_sandbox_smoke",
            evidence_hash="sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
            source_ref="support/evidence/EP5-004/broker_smoke.json",
            status="passed",
        ),
        EvidenceReviewed(
            key="rollback_drill",
            evidence_hash="sha256:fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210",
            source_ref="support/evidence/EP5-004/rollback_drill.json",
            status="passed",
        ),
    )


def _base_decision(
    decision_id: str = "hgd-lifecycle-001",
    *,
    readiness_can_proceed: bool = True,
    blocking_reasons: tuple[str, ...] = (),
    created_at: str = "2026-05-19T10:00:00Z",
) -> HumanGateDecision:
    evidence = _evidence()
    return validate_decision(
        HumanGateDecision(
            decision_id=decision_id,
            target_type="deployment",
            target_id="m7-canary",
            target_environment="canary",
            required_roles=("risk_owner", "operator"),
            evidence_reviewed=evidence,
            evidence_hash=compute_evidence_hash(evidence),
            can_proceed_input=CanProceedInput(
                readiness_packet_ref="prp-lifecycle-test",
                readiness_packet_can_proceed=readiness_can_proceed,
                required_evidence=("broker_sandbox_smoke", "rollback_drill"),
                blocking_reasons=blocking_reasons,
            ),
            created_at=created_at,
        )
    )


def _approved_decision(decision_id: str = "hgd-lifecycle-approved") -> HumanGateDecision:
    """Return a fully signed, approved decision."""
    evidence_hash = compute_evidence_hash(_evidence())
    sig_evidence = ("broker_sandbox_smoke", "rollback_drill")
    return validate_decision(
        HumanGateDecision(
            decision_id=decision_id,
            target_type="deployment",
            target_id="m7-canary",
            target_environment="canary",
            required_roles=("risk_owner", "operator"),
            evidence_reviewed=_evidence(),
            evidence_hash=evidence_hash,
            can_proceed_input=CanProceedInput(
                readiness_packet_ref="prp-lifecycle-test",
                readiness_packet_can_proceed=True,
                required_evidence=("broker_sandbox_smoke", "rollback_drill"),
            ),
            signatures=(
                HumanGateSignature(
                    signature_id="sig-risk-001",
                    role="risk_owner",
                    actor_id="risk-owner-agent",
                    signed_at="2026-05-19T11:00:00Z",
                    evidence_hash=evidence_hash,
                    evidence_reviewed=sig_evidence,
                    meaning="approved",
                ),
                HumanGateSignature(
                    signature_id="sig-op-001",
                    role="operator",
                    actor_id="operator-agent",
                    signed_at="2026-05-19T11:01:00Z",
                    evidence_hash=evidence_hash,
                    evidence_reviewed=sig_evidence,
                    meaning="approved",
                ),
            ),
            created_at="2026-05-19T10:00:00Z",
        )
    )


# ---------------------------------------------------------------------------
# is_terminal / is_active
# ---------------------------------------------------------------------------

def test_is_active_on_pending_decision():
    decision = _base_decision()
    assert decision.status == "pending"
    assert is_active(decision) is True
    assert is_terminal(decision) is False


def test_is_terminal_on_approved_decision():
    decision = _approved_decision()
    assert decision.status == "approved"
    assert is_active(decision) is True  # approved is NOT terminal
    assert is_terminal(decision) is False


# ---------------------------------------------------------------------------
# revoke_signature
# ---------------------------------------------------------------------------

def test_revoke_signature_transitions_approved_to_pending():
    decision = _approved_decision()
    assert decision.status == "approved"
    updated = revoke_signature(decision, "sig-risk-001", reason="Evidence hash disputed.")
    assert updated.status == "pending"
    assert updated.can_proceed is False
    # The revoked signature is marked
    revoked = next(s for s in updated.signatures if s.signature_id == "sig-risk-001")
    assert revoked.revoked_at is not None


def test_revoke_signature_keeps_decision_blocked_when_readiness_not_ready():
    decision = _base_decision(readiness_can_proceed=False, blocking_reasons=("drill_pending",))
    evidence_hash = compute_evidence_hash(_evidence())
    sig_evidence = ("broker_sandbox_smoke", "rollback_drill")
    with_sig = HumanGateDecision.from_dict(
        {
            **decision.to_dict(),
            "signatures": [
                {
                    "signature_id": "sig-risk-002",
                    "role": "risk_owner",
                    "actor_id": "risk-owner-2",
                    "signed_at": "2026-05-19T11:00:00Z",
                    "evidence_hash": evidence_hash,
                    "evidence_reviewed": list(sig_evidence),
                }
            ],
        }
    )
    updated = revoke_signature(with_sig, "sig-risk-002")
    assert updated.status == "blocked"
    assert updated.can_proceed is False


def test_revoke_signature_raises_for_unknown_signature_id():
    decision = _base_decision()
    with pytest.raises(LifecycleError, match="signature not found"):
        revoke_signature(decision, "sig-does-not-exist")


def test_revoke_signature_raises_for_already_revoked_signature():
    decision = _approved_decision()
    once_revoked = revoke_signature(decision, "sig-risk-001")
    with pytest.raises(LifecycleError, match="already revoked"):
        revoke_signature(once_revoked, "sig-risk-001")


def test_revoke_signature_raises_on_terminal_decision():
    decision = _base_decision()
    expired = expire_decision(decision)
    with pytest.raises(LifecycleError, match="terminal"):
        revoke_signature(expired, "sig-any")


# ---------------------------------------------------------------------------
# expire_decision
# ---------------------------------------------------------------------------

def test_expire_decision_sets_terminal_state():
    decision = _base_decision()
    expired = expire_decision(decision, reason="Packet TTL exceeded.")
    assert expired.status == "expired"
    assert is_terminal(expired) is True
    assert expired.can_proceed is False
    assert "TTL" in (expired.reason or "")


def test_expire_approved_decision():
    decision = _approved_decision()
    assert decision.status == "approved"
    expired = expire_decision(decision)
    assert expired.status == "expired"
    assert expired.can_proceed is False


def test_expire_decision_raises_if_already_terminal():
    decision = _base_decision()
    expired = expire_decision(decision)
    with pytest.raises(LifecycleError, match="already-terminal"):
        expire_decision(expired)


# ---------------------------------------------------------------------------
# withdraw_decision
# ---------------------------------------------------------------------------

def test_withdraw_decision_sets_terminal_state():
    decision = _base_decision()
    withdrawn = withdraw_decision(decision, reason="Proposer retracted the gate.")
    assert withdrawn.status == "withdrawn"
    assert is_terminal(withdrawn) is True
    assert withdrawn.can_proceed is False
    assert "retracted" in (withdrawn.reason or "")


def test_withdraw_approved_decision():
    decision = _approved_decision()
    withdrawn = withdraw_decision(decision)
    assert withdrawn.status == "withdrawn"
    assert withdrawn.can_proceed is False


def test_withdraw_decision_raises_if_already_terminal():
    decision = _base_decision()
    withdrawn = withdraw_decision(decision)
    with pytest.raises(LifecycleError, match="already-terminal"):
        withdraw_decision(withdrawn)


# ---------------------------------------------------------------------------
# supersede_decision
# ---------------------------------------------------------------------------

def test_supersede_decision_sets_terminal_state():
    decision = _base_decision()
    superseded = supersede_decision(decision, "hgd-lifecycle-002")
    assert superseded.status == "superseded"
    assert is_terminal(superseded) is True
    assert superseded.can_proceed is False
    assert superseded.metadata.get("superseded_by") == "hgd-lifecycle-002"


def test_supersede_decision_raises_for_empty_successor_id():
    decision = _base_decision()
    with pytest.raises(LifecycleError, match="superseded_by_id"):
        supersede_decision(decision, "")


def test_supersede_decision_raises_if_already_terminal():
    decision = _base_decision()
    superseded = supersede_decision(decision, "hgd-next")
    with pytest.raises(LifecycleError, match="already-terminal"):
        supersede_decision(superseded, "hgd-next-next")


# ---------------------------------------------------------------------------
# recompute_can_proceed
# ---------------------------------------------------------------------------

def test_recompute_can_proceed_reflects_updated_readiness_input():
    decision = _base_decision(readiness_can_proceed=False, blocking_reasons=("drill_pending",))
    assert decision.status == "blocked"
    assert decision.can_proceed is False

    unblocked = update_blocking_reasons(decision, [])
    # still blocked because readiness_packet_can_proceed is False
    recomputed = recompute_can_proceed(unblocked)
    assert recomputed.can_proceed is False

    # Now simulate the readiness packet itself flipping
    from dataclasses import replace as dc_replace
    new_input = dc_replace(
        recomputed.can_proceed_input,
        readiness_packet_can_proceed=True,
        blocking_reasons=(),
    )
    recomputed2 = recompute_can_proceed(dc_replace(recomputed, can_proceed_input=new_input))
    # Still pending because no signatures yet
    assert recomputed2.status == "pending"
    assert recomputed2.can_proceed is False


def test_recompute_can_proceed_is_noop_for_terminal_decision():
    decision = _base_decision()
    expired = expire_decision(decision)
    result = recompute_can_proceed(expired)
    assert result is expired


# ---------------------------------------------------------------------------
# update_blocking_reasons
# ---------------------------------------------------------------------------

def test_update_blocking_reasons_clears_existing_reasons():
    decision = _base_decision(readiness_can_proceed=False, blocking_reasons=("reason_a",))
    assert decision.status == "blocked"

    updated = update_blocking_reasons(decision, [])
    # readiness_packet_can_proceed is still False, so remains blocked
    assert updated.status == "blocked"
    assert updated.can_proceed_input.blocking_reasons == ()


def test_update_blocking_reasons_adds_new_reason():
    decision = _base_decision()
    updated = update_blocking_reasons(decision, ["new_block"])
    assert "blocking_reason:new_block" in updated.blocking_summary()
    assert updated.can_proceed is False


def test_update_blocking_reasons_raises_on_terminal_decision():
    decision = _base_decision()
    withdrawn = withdraw_decision(decision)
    with pytest.raises(LifecycleError, match="terminal"):
        update_blocking_reasons(withdrawn, ["any"])


# ---------------------------------------------------------------------------
# enforce_ttl (pure)
# ---------------------------------------------------------------------------

def test_enforce_ttl_expires_old_decision():
    decision = _base_decision(created_at="2026-05-19T10:00:00Z")
    # TTL of 3600s, check at 2h later
    result = enforce_ttl(decision, ttl_seconds=3600.0, now="2026-05-19T12:00:00Z")
    assert result.status == "expired"
    assert is_terminal(result) is True


def test_enforce_ttl_keeps_young_decision():
    decision = _base_decision(created_at="2026-05-19T10:00:00Z")
    result = enforce_ttl(decision, ttl_seconds=3600.0, now="2026-05-19T10:30:00Z")
    assert result is decision


def test_enforce_ttl_noop_for_terminal_decision():
    decision = _base_decision()
    expired = expire_decision(decision)
    result = enforce_ttl(expired, ttl_seconds=1.0, now="2026-05-19T12:00:00Z")
    assert result is expired


def test_enforce_ttl_noop_when_no_created_at():
    evidence = _evidence()
    decision = validate_decision(
        HumanGateDecision(
            decision_id="hgd-no-ts",
            target_type="deployment",
            target_id="m7-canary",
            target_environment="canary",
            required_roles=("risk_owner",),
            evidence_reviewed=evidence,
            evidence_hash=compute_evidence_hash(evidence),
            can_proceed_input=CanProceedInput(
                readiness_packet_ref="prp-test",
                readiness_packet_can_proceed=True,
                required_evidence=("broker_sandbox_smoke", "rollback_drill"),
            ),
        )
    )
    result = enforce_ttl(decision, ttl_seconds=1.0, now="2099-01-01T00:00:00Z")
    assert result is decision


# ---------------------------------------------------------------------------
# Store-level operations (revoke_expire.py)
# ---------------------------------------------------------------------------

def _make_store(decision: HumanGateDecision | None = None) -> HumanGateDecisionStore:
    store = HumanGateDecisionStore()
    if decision is not None:
        store.put(decision)
    return store


def test_revoke_decision_store_level():
    decision = _approved_decision()
    store = _make_store(decision)
    updated = revoke_decision(
        store,
        decision.decision_id,
        signature_id="sig-risk-001",
        reason="Evidence retracted.",
    )
    assert updated.status == "pending"
    assert store.get(decision.decision_id).status == "pending"


def test_revoke_decision_raises_for_missing_decision():
    store = HumanGateDecisionStore()
    with pytest.raises(RevokeExpireError, match="not found"):
        revoke_decision(store, "hgd-does-not-exist", signature_id="any")


def test_expire_decision_in_store():
    decision = _base_decision()
    store = _make_store(decision)
    updated = expire_decision_in_store(store, decision.decision_id, reason="Compliance deadline passed.")
    assert updated.status == "expired"
    assert store.get(decision.decision_id).status == "expired"


def test_expire_decision_in_store_raises_if_already_terminal():
    decision = _base_decision()
    store = _make_store(decision)
    expire_decision_in_store(store, decision.decision_id)
    with pytest.raises(RevokeExpireError, match="already-terminal"):
        expire_decision_in_store(store, decision.decision_id)


def test_withdraw_decision_in_store():
    decision = _base_decision()
    store = _make_store(decision)
    updated = withdraw_decision_in_store(store, decision.decision_id, reason="Proposer withdrew.")
    assert updated.status == "withdrawn"


def test_supersede_decision_in_store():
    decision = _base_decision("hgd-old")
    store = _make_store(decision)
    updated = supersede_decision_in_store(store, "hgd-old", "hgd-new")
    assert updated.status == "superseded"
    assert updated.metadata.get("superseded_by") == "hgd-new"


def test_recompute_can_proceed_in_store():
    decision = _base_decision()
    store = _make_store(decision)
    updated = recompute_can_proceed_in_store(store, decision.decision_id)
    assert updated.status == decision.status  # no change for pending without sigs


def test_update_blocking_reasons_in_store():
    decision = _base_decision()
    store = _make_store(decision)
    updated = update_blocking_reasons_in_store(store, decision.decision_id, ["blocker_x"])
    assert "blocking_reason:blocker_x" in updated.blocking_summary()


def test_enforce_ttl_in_store_expires_old_decision():
    decision = _base_decision(created_at="2026-05-19T10:00:00Z")
    store = _make_store(decision)
    updated = enforce_ttl_in_store(
        store,
        decision.decision_id,
        ttl_seconds=3600.0,
        now="2026-05-19T12:00:00Z",
    )
    assert updated.status == "expired"


def test_enforce_ttl_in_store_no_change_within_ttl():
    decision = _base_decision(created_at="2026-05-19T10:00:00Z")
    store = _make_store(decision)
    result = enforce_ttl_in_store(
        store,
        decision.decision_id,
        ttl_seconds=7200.0,
        now="2026-05-19T10:30:00Z",
    )
    assert result.status == "pending"


def test_enforce_ttl_for_all_expires_multiple():
    d1 = _base_decision("hgd-a", created_at="2026-05-19T08:00:00Z")
    d2 = _base_decision("hgd-b", created_at="2026-05-19T09:00:00Z")
    d3 = _base_decision("hgd-c", created_at="2026-05-19T10:00:00Z")
    store = HumanGateDecisionStore()
    for d in (d1, d2, d3):
        store.put(d)
    expired_ids = enforce_ttl_for_all(
        store,
        ttl_seconds=3600.0,
        now="2026-05-19T10:30:00Z",
    )
    # hgd-a and hgd-b are > 1h old at 10:30; hgd-c is only 30min old
    assert set(expired_ids) == {"hgd-a", "hgd-b"}
    assert store.get("hgd-a").status == "expired"
    assert store.get("hgd-b").status == "expired"
    assert store.get("hgd-c").status == "pending"


def test_enforce_ttl_for_all_skips_terminal_decisions():
    d1 = _base_decision("hgd-terminal", created_at="2026-05-19T08:00:00Z")
    store = HumanGateDecisionStore()
    store.put(d1)
    # Pre-expire so it's already terminal
    expire_decision_in_store(store, "hgd-terminal")
    expired_ids = enforce_ttl_for_all(
        store,
        ttl_seconds=1.0,
        now="2026-05-19T12:00:00Z",
    )
    assert "hgd-terminal" not in expired_ids
