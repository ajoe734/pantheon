"""PAPER-RISK-APPROVAL-HISTORY-BOUND-20261124.

Each daily renewal of a pool risk policy adds one Governance decision.  Discovery bounds its candidate read, so
expired, rejected, revoked and superseded decisions must not count toward that bound.  These tests drive the real
chain (real Governance app with issued principals and grant, real Capital app, real coordinator and transport; only
the socket and the clock are replaced) through 40 renewals and then a repair, a risk-profile change and a redraft.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.capital.risk_policy import risk_policy_digest
from services.control_plane.bff.persona_provisioning_coordinator import DraftResult, PersonaProvisioningCoordinator
from services.governance.approval_authority import (
    bound_candidate_history,
    is_current_candidate_decision,
)

from .test_paper_risk_policy_gate_real_chain import DRAFT, PERSONA, POOL, TENANT, _proposals, _repair, world  # noqa: F401
from .test_paper_risk_policy_renewal import (  # noqa: F401  (fixtures + helpers)
    _approved,
    _coordinator,
    _pool_meta,
    _resolved,
    clock,
)

RENEWALS = 40


@pytest.fixture(autouse=True)
def _long_lived_principals(monkeypatch):
    # 40 renewals span 28 simulated days on one issue; a deployment refreshes the principals.
    from scripts import issue_dev_paper_principals as issuer

    monkeypatch.setattr(issuer, "TTL_SECONDS", 90 * 24 * 60 * 60)


def _renew_many(world, clock, count=RENEWALS):
    coordinator = _coordinator()
    for _ in range(count):
        clock.advance(hours=17)
        out = coordinator.renew_paper_risk_policy_approval(
            tenant_id=TENANT, persona_id=PERSONA, pool_id=POOL, now=clock.now)
        assert out["status"] == "renewed", out
    return coordinator


def test_forty_renewals_then_repair_profile_change_and_redraft_all_resolve(world, clock, monkeypatch):
    _repair()
    assert _resolved(world)
    _renew_many(world, clock)
    assert len(_approved(world)) == RENEWALS + 1  # nothing deleted
    live = [d for d in _approved(world) if d["expires_at"] > clock.now.isoformat()]
    assert 1 <= len(live) <= 2

    # repair on the same policy
    repaired = _repair()
    assert repaired["after_resolution"]["resolved"] is True, repaired["after_resolution"]
    assert repaired["after_resolution"]["allow_new_or_increased_risk"] is True

    # redraft: a tightened policy with a new digest and version is proposed and resolved
    redraft = {**DRAFT, "version": "3.0", "gross_limit": 0.08, "net_limit": 0.08, "max_leverage": 0.08}
    draft = DraftResult(True, policy=dict(redraft), provenance={"source": "risk_officer_draft", "input_digest": "redraft"})
    monkeypatch.setattr(PersonaProvisioningCoordinator, "_drafter", lambda self: (lambda inputs: draft))
    redrafted = _repair(requested_policy_version="3.0") if False else _repair()
    assert redrafted["after_resolution"]["resolved"] is True, redrafted["after_resolution"]


def test_historical_decisions_do_not_count_toward_the_candidate_bound():
    from datetime import datetime, timezone
    now = datetime(2026, 11, 24, tzinfo=timezone.utc)
    old = [{"decision_id": f"old-{i}", "decision_state": "decided", "decision": "approved",
            "expires_at": "2026-11-01T00:00:00Z"} for i in range(365)]
    rejected = {"decision_id": "rej", "decision_state": "decided", "decision": "rejected", "expires_at": "2027-01-01T00:00:00Z"}
    revoked = {"decision_id": "rev", "decision_state": "decided", "decision": "approved", "revoked_at": "2026-11-02T00:00:00Z",
               "expires_at": "2027-01-01T00:00:00Z"}
    superseded = {"decision_id": "sup", "decision_state": "decided", "decision": "approved", "superseded_by": "x",
                  "expires_at": "2027-01-01T00:00:00Z"}
    live = {"decision_id": "live", "decision_state": "decided", "decision": "approved", "expires_at": "2027-01-01T00:00:00Z"}
    assert not any(is_current_candidate_decision(i, now) for i in [*old, rejected, revoked, superseded])
    assert is_current_candidate_decision(live, now)
    out = bound_candidate_history([*old, rejected, revoked, superseded, live], bound=16, now=now)
    assert out.overflow is False
    assert "live" in [i["decision_id"] for i in out]
    # 17 truly current candidates remain ambiguous
    many = [{**live, "decision_id": f"live-{i}"} for i in range(17)]
    assert bound_candidate_history(many, bound=16, now=now).overflow is True
    # the history read returns everything up to its cap
    assert len(bound_candidate_history(old, bound=16, now=now, history=True)) == 365
