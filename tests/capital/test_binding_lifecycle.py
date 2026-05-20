from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.capital.binding_live.lifecycle import (
    SCHEMA_VERSION,
    BindingLifecycleError,
    BindingLifecycleState,
    BindingRevocationPolicy,
    BindingTTL,
    evaluate_binding_lifecycle,
)


NOW = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)


def _lifecycle(**overrides) -> BindingLifecycleState:
    defaults = {
        "binding_id": "binding-live-001",
        "status": "active",
        "ttl": {
            "issued_at": "2026-05-20T00:00:00Z",
            "ttl_hours": 24,
        },
        "revocation_policy": {
            "revocation_allowed": True,
            "allowed_revoker_roles": ["risk_owner", "operator"],
            "requires_reason": True,
        },
    }
    defaults.update(overrides)
    return BindingLifecycleState.from_dict(defaults)


def test_active_binding_with_valid_ttl_is_admissible() -> None:
    evaluation = _lifecycle().evaluate(at=NOW)

    assert evaluation.status == "active"
    assert evaluation.admissible is True
    assert evaluation.blocking_reasons == ()
    assert evaluation.expires_at == "2026-05-21T00:00:00Z"
    assert evaluation.to_dict()["schema_version"] == SCHEMA_VERSION


def test_expired_ttl_returns_expired_and_blocks_admissibility() -> None:
    evaluation = _lifecycle().evaluate(at="2026-05-21T00:00:00Z")

    assert evaluation.status == "expired"
    assert evaluation.admissible is False
    assert evaluation.blocking_reasons == ("binding_ttl_expired",)


def test_explicit_ttl_expiry_must_match_issued_at_plus_ttl() -> None:
    with pytest.raises(BindingLifecycleError, match="ttl.expires_at"):
        BindingTTL.from_dict(
            {
                "issued_at": "2026-05-20T00:00:00Z",
                "ttl_hours": 24,
                "expires_at": "2026-05-22T00:00:00Z",
            }
        )


def test_suspend_blocks_admissibility_without_revoking() -> None:
    suspended = _lifecycle().suspend(
        actor_id="risk-owner-1",
        reason="risk budget review",
        at=NOW,
    )
    evaluation = suspended.evaluate(at=NOW)

    assert suspended.status == "suspended"
    assert suspended.revoked_at is None
    assert evaluation.admissible is False
    assert evaluation.blocking_reasons == ("binding_suspended",)


def test_suspended_binding_can_reactivate_before_ttl_expiry() -> None:
    suspended = _lifecycle().suspend(
        actor_id="risk-owner-1",
        reason="manual hold",
        at=NOW,
    )
    reactivated = suspended.reactivate(actor_id="operator-1", at="2026-05-20T13:00:00Z")

    assert reactivated.status == "active"
    assert reactivated.suspended_at is None
    assert reactivated.evaluate(at="2026-05-20T13:00:00Z").admissible is True


def test_suspended_binding_cannot_reactivate_after_ttl_expiry() -> None:
    suspended = _lifecycle().suspend(
        actor_id="risk-owner-1",
        reason="manual hold",
        at=NOW,
    )

    with pytest.raises(BindingLifecycleError, match="expired binding cannot be reactivated"):
        suspended.reactivate(actor_id="operator-1", at="2026-05-21T00:00:00Z")


def test_revoke_requires_allowed_role_and_reason() -> None:
    lifecycle = _lifecycle()

    with pytest.raises(BindingLifecycleError, match="cannot revoke"):
        lifecycle.revoke(
            actor_id="viewer-1",
            actor_role="viewer",
            reason="bad actor role",
            at=NOW,
        )

    with pytest.raises(BindingLifecycleError, match="reason is required"):
        lifecycle.revoke(
            actor_id="risk-owner-1",
            actor_role="risk_owner",
            reason="",
            at=NOW,
        )


def test_revoke_transitions_to_terminal_revoked() -> None:
    revoked = _lifecycle().revoke(
        actor_id="risk-owner-1",
        actor_role="risk_owner",
        reason="mandate withdrawn",
        at=NOW,
    )
    evaluation = evaluate_binding_lifecycle(revoked, at=NOW)

    assert revoked.status == "revoked"
    assert revoked.revocation_reason == "mandate withdrawn"
    assert evaluation.status == "revoked"
    assert evaluation.admissible is False
    assert evaluation.blocking_reasons == ("binding_revoked",)

    with pytest.raises(BindingLifecycleError, match="terminal binding"):
        revoked.revoke(
            actor_id="operator-1",
            actor_role="operator",
            reason="second revoke",
            at=NOW,
        )


def test_revocation_policy_can_fail_closed() -> None:
    policy = BindingRevocationPolicy.from_dict(
        {
            "revocation_allowed": False,
            "allowed_revoker_roles": ["risk_owner"],
            "requires_reason": True,
        }
    )
    lifecycle = _lifecycle(revocation_policy=policy.to_dict())

    with pytest.raises(BindingLifecycleError, match="revocation_allowed"):
        lifecycle.revoke(
            actor_id="risk-owner-1",
            actor_role="risk_owner",
            reason="policy denied",
            at=NOW,
        )
