"""PAPER-RISK-POLICY-APPROVAL-RENEWAL-20261124.

A gate-approved paper pool risk policy lives 24 h.  These tests drive the deployed construction (real Governance
app with issued principals and grant, real Capital app, real coordinator and owner transport; only the socket and the
clock are replaced) through T+0, the renewal at T+17 h, T+25 h, the one-decision-per-12 h bound and the recovery of a
pool whose approval already expired and which carries an earlier missing-draft rejection.
"""
from __future__ import annotations

import datetime as _stdlib_datetime
import gc
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.capital.risk_policy import capital_pool_approval_digest, risk_policy_digest
from services.control_plane.bff.persona_provisioning import (
    MemoryPersonaProvisioningStore,
    ProvisioningRecord,
)
from services.control_plane.bff.persona_provisioning_coordinator import (
    DraftResult,
    PersonaProvisioningCoordinator,
    deterministic_provisioning_ids,
)
from services.control_plane.bff.personas import service

from .test_paper_risk_policy_gate_real_chain import (  # noqa: F401  (fixture + helpers)
    DRAFT,
    PERSONA,
    POOL,
    TENANT,
    _decisions,
    _proposals,
    _repair,
    world,
)

_REAL_DATETIME = datetime
_REPO = str(__import__("pathlib").Path(__file__).resolve().parents[4])


@pytest.fixture(autouse=True)
def _long_lived_principals(monkeypatch):
    # The issued principals are refreshed in a deployment; the test runs 25-40 simulated hours on one issue.
    from scripts import issue_dev_paper_principals as issuer

    monkeypatch.setattr(issuer, "TTL_SECONDS", 72 * 60 * 60)


class _Clock:
    def __init__(self) -> None:
        self.offset = timedelta(0)
        self.base = _REAL_DATETIME.now(timezone.utc)

    @property
    def now(self) -> datetime:
        return self.base + self.offset

    def advance(self, **kwargs) -> None:
        self.offset += timedelta(**kwargs)


_ACTIVE: list[_Clock] = []


class _Meta(type):
    def __instancecheck__(cls, obj):
        return isinstance(obj, _REAL_DATETIME)


class _Fake(_REAL_DATETIME, metaclass=_Meta):
    """``datetime`` that follows the active test clock and is the real clock when none is active."""

    @classmethod
    def now(cls, tz=None):
        current = _ACTIVE[-1].now if _ACTIVE else _REAL_DATETIME.now(timezone.utc)
        return current.astimezone(tz) if tz else current.replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return cls.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def clock(world, monkeypatch):
    clk = _Clock()
    _ACTIVE.append(clk)
    # Modules imported lazily during a request bind ``datetime`` from the stdlib module at that moment.
    monkeypatch.setattr(_stdlib_datetime, "datetime", _Fake)
    # Module globals, not only ``sys.modules``: Governance keeps a module it loaded under a path alias.
    for namespace in [o for o in gc.get_objects() if isinstance(o, dict) and o.get("datetime") is _REAL_DATETIME]:
        if str(namespace.get("__file__") or "").startswith(_REPO):
            monkeypatch.setitem(namespace, "datetime", _Fake)
    yield clk
    _ACTIVE.pop()


def _coordinator():
    coordinator = service._chain_paper_capital_coordinator(TENANT)
    return coordinator


def _pool_meta(world, pool_id=POOL):
    return dict(world.cap_main.pool_store.get(pool_id).metadata)


def _resolved(world):
    after = _repair()["after_resolution"]
    return after["resolved"] is True and after["allow_new_or_increased_risk"] is True


def _approved(world):
    return [d for d in _proposals(world) if d["decision_state"] == "decided" and d["decision"] == "approved"]


def test_t0_renewal_t17_resolved_t25_one_decision_per_12h(world, clock):
    _repair()
    assert _resolved(world)
    first = _pool_meta(world)["profile_mapping_approval_id"]
    coordinator = _coordinator()
    kwargs = dict(tenant_id=TENANT, persona_id=PERSONA, pool_id=POOL)

    clock.advance(hours=1)
    assert coordinator.renew_paper_risk_policy_approval(**kwargs, now=clock.now)["status"] == "not_due"
    assert len(_approved(world)) == 1

    clock.advance(hours=16)  # T+17h: fewer than 8 h remain
    renewed = coordinator.renew_paper_risk_policy_approval(**kwargs, now=clock.now)
    assert renewed["status"] == "renewed", (renewed.get("gate_error"), renewed)
    second = _pool_meta(world)["profile_mapping_approval_id"]
    assert second != first and second == renewed["renewed_approval_id"]
    assert len(_approved(world)) == 2
    by_id = {d["decision_id"]: d for d in _approved(world)}
    old, new = by_id[first], by_id[second]
    assert any(v.get("actor_id") == "pantheon-dev-paper-provisioner" for v in new["metadata"]["approvals"])
    assert new["metadata"]["subject"]["renewal_of"] == first
    for key in ("risk_policy_id", "risk_policy_version", "risk_policy_digest", "risk_officer_draft"):
        assert new["metadata"]["subject"][key] == old["metadata"]["subject"][key], key
    assert new["expires_at"] > old["expires_at"]
    assert old["expires_at"] > clock.now.isoformat()
    overlap_owner = coordinator._owner_get("capital", f"/api/capital-pools/{POOL}/risk-policy?stage=paper")
    assert overlap_owner["resolved"] is True and overlap_owner["allow_new_or_increased_risk"] is True
    assert overlap_owner["profile_mapping_approval_id"] == second
    assert _resolved(world)

    # Same instant, later the same hour, and a replica restart: one decision only.
    for minutes in (0, 5, 40):
        clock.advance(minutes=minutes)
        again = _coordinator().renew_paper_risk_policy_approval(**kwargs, now=clock.now)
        assert again["status"] == "not_due"
    assert len(_approved(world)) == 2

    clock.advance(hours=8)  # T+25h: the first approval has expired, the renewed one has not
    assert _resolved(world)
    assert len(_approved(world)) == 2
    readback = _repair()["approval_renewal"]
    assert readback["status"] == "not_due" and readback["approval_id"] == second
    assert readback["approval_expires_at"] == new["expires_at"]


def test_expired_approval_with_earlier_missing_draft_rejection_recovers(world, clock):
    _repair()
    assert _resolved(world)
    first = _pool_meta(world)["profile_mapping_approval_id"]
    coordinator = _coordinator()
    pool = world.cap_main.pool_store.get(POOL)
    digest = risk_policy_digest(DRAFT)
    pool_digest = capital_pool_approval_digest(
        coordinator._pool_with_authoritative_approval_digest(
            coordinator._owner_get("capital", f"/api/capital-pools/{POOL}"), tenant_id=TENANT))
    resolution = SimpleNamespace(
        status="unresolved_profile_mapping", reason_code="risk_policy_profile_mapping_unapproved", reason="x",
        owner_decision_request={
            "candidate_risk_policy_id": DRAFT["risk_policy_id"], "candidate_risk_policy_version": DRAFT["version"],
            "candidate_risk_policy_digest": digest, "candidate_pool_approval_digest": pool_digest},
        policy=None, risk_policy_id=DRAFT["risk_policy_id"], risk_policy_version=DRAFT["version"],
        risk_policy_digest=digest)
    # The old approval lapses; a repair that proposes without the draft is refused by the gate.
    clock.advance(hours=30)
    # (the carried draft is found among the earlier approved decisions, so force a draft-less refusal first)
    monkey = PersonaProvisioningCoordinator._carried_risk_officer_draft
    PersonaProvisioningCoordinator._carried_risk_officer_draft = lambda self, **kw: None
    try:
        req = coordinator._route_or_reuse_risk_policy_owner_decision_request(
            tenant_id=TENANT, persona_id=PERSONA, pool_id=POOL, requested_profile="low", resolution=resolution)
    finally:
        PersonaProvisioningCoordinator._carried_risk_officer_draft = monkey
    assert req["paper_gate_status"] == "decided_rejected_by_automated_gate", req
    assert any(d["decision"] == "rejected" and "risk_officer_draft absent" in d["rationale"] for d in _proposals(world))

    assert pool.metadata is not None
    result = _repair()
    assert result["approval_renewal"]["status"] == "renewed", result["approval_renewal"]
    assert result["after_resolution"]["resolved"] is True
    assert result["after_resolution"]["allow_new_or_increased_risk"] is True
    assert _pool_meta(world)["profile_mapping_approval_id"] != first
    assert _resolved(world)


def _draftless_resolution(world):
    coordinator = _coordinator()
    digest = risk_policy_digest(DRAFT)
    pool_digest = coordinator._pool_with_authoritative_approval_digest(
        coordinator._owner_get("capital", f"/api/capital-pools/{POOL}"), tenant_id=TENANT)["approval_digest"]
    return coordinator, SimpleNamespace(
        status="unresolved_profile_mapping", reason_code="risk_policy_profile_mapping_unapproved", reason="x",
        owner_decision_request={
            "candidate_risk_policy_id": DRAFT["risk_policy_id"], "candidate_risk_policy_version": DRAFT["version"],
            "candidate_risk_policy_digest": digest, "candidate_pool_approval_digest": pool_digest},
        policy=None, risk_policy_id=DRAFT["risk_policy_id"], risk_policy_version=DRAFT["version"],
        risk_policy_digest=digest)


def _route(coordinator, resolution, draft=None):
    return coordinator._route_or_reuse_risk_policy_owner_decision_request(
        tenant_id=TENANT, persona_id=PERSONA, pool_id=POOL, requested_profile="low", resolution=resolution,
        draft=draft)


def test_missing_draft_rejection_is_not_permanent_next_repair_reproposes_with_the_draft(world):
    coordinator, resolution = _draftless_resolution(world)
    refused = _route(coordinator, resolution)
    assert refused["paper_gate_status"] == "decided_rejected_by_automated_gate"
    retry = _route(coordinator, resolution)
    assert retry["governance_proposal_status"] == "missing_draft_rejection_retryable"
    assert retry.get("absence_source") != "policy_approval_rejected"
    assert len(_proposals(world)) == 1  # nothing was re-proposed without a draft
    draft = DraftResult(True, policy=dict(DRAFT), provenance={"source": "risk_officer_draft", "input_digest": "carried"})
    final = _route(coordinator, resolution, draft)
    assert final["paper_gate_status"] == "decided_approved_by_automated_gate", final
    assert final["retried_after_missing_draft_rejection"] == refused["governance_proposal_id"]
    assert len(_proposals(world)) == 2


def test_carried_draft_is_used_when_a_later_repair_has_none(world):
    coordinator, resolution = _draftless_resolution(world)
    draft = DraftResult(True, policy=dict(DRAFT), provenance={"source": "risk_officer_draft", "input_digest": "carried"})
    approved = _route(coordinator, resolution, draft)
    assert approved["paper_gate_status"] == "decided_approved_by_automated_gate"
    again = _route(coordinator, resolution)  # no draft supplied: carried from the earlier proposal
    assert again["governance_proposal_id"] == approved["governance_proposal_id"]
    assert len(_proposals(world)) == 1


def test_rejection_naming_a_limit_stays_final(world):
    coordinator, resolution = _draftless_resolution(world)
    too_big = {**DRAFT, "gross_limit": 50.0, "net_limit": 50.0, "max_leverage": 50.0}
    resolution.owner_decision_request["candidate_risk_policy_digest"] = risk_policy_digest(too_big)
    resolution.risk_policy_digest = risk_policy_digest(too_big)
    draft = DraftResult(True, policy=too_big, provenance={"source": "risk_officer_draft", "input_digest": "big"})
    first = _route(coordinator, resolution, draft)
    assert first["paper_gate_status"] == "decided_rejected_by_automated_gate"
    assert "risk_officer_draft absent" not in first["paper_gate_reason"]
    second = _route(coordinator, resolution, draft)
    assert second["governance_proposal_status"] == "explicit_non_approval_recorded"
    assert second["absence_source"] == "policy_approval_rejected"
    assert len(_proposals(world)) == 1


@pytest.mark.parametrize("actor_role,rationale,draft,expected", [
    ("automated_gate", "Refused by paper gate: risk_officer_draft absent; the low profile requires x", None, True),
    ("human", "Refused by paper gate: risk_officer_draft absent", None, False),
    ("automated_gate", "Refused by paper gate: gross_limit 50 above outer bound", None, False),
    ("automated_gate", "Refused by paper gate: risk_officer_draft absent", {"policy": {"gross_limit": 1}}, False),
])
def test_only_a_gate_refusal_for_the_missing_draft_is_retryable(actor_role, rationale, draft, expected):
    item = {"decision_state": "decided", "decision": "rejected", "actor_role": actor_role, "rationale": rationale,
            "metadata": {"subject": {"risk_officer_draft": draft} if draft else {}}}
    assert PersonaProvisioningCoordinator._is_missing_draft_rejection(item) is expected


def test_renewal_of_a_policy_that_no_longer_validates_requests_a_redraft(world, clock, monkeypatch):
    _repair()
    assert _resolved(world)
    first = _pool_meta(world)["profile_mapping_approval_id"]
    from services.control_plane.bff import persona_provisioning_coordinator as module
    monkeypatch.setattr(module, "validate_drafted_pool_risk_policy",
                        lambda policy, profile: (None, "gross_limit 0.1 above the tightened outer bound"))
    clock.advance(hours=17)
    outcome = _coordinator().renew_paper_risk_policy_approval(
        tenant_id=TENANT, persona_id=PERSONA, pool_id=POOL, now=clock.now)
    assert outcome["status"] == "redraft_requested" and "tightened outer bound" in outcome["reason"]
    assert _pool_meta(world)["profile_mapping_approval_id"] == first
    assert len(_approved(world)) == 1  # no decision was spent on it


def test_governance_unavailable_leaves_the_old_approval_and_is_retried(world, clock):
    _repair()
    assert _resolved(world)
    first = _pool_meta(world)["profile_mapping_approval_id"]
    world.faults["/api/governance/approvals"] = (503, "down")
    clock.advance(hours=17)
    down = _coordinator().renew_paper_risk_policy_approval(
        tenant_id=TENANT, persona_id=PERSONA, pool_id=POOL, now=clock.now)
    assert down["status"] == "governance_unavailable", down
    assert _pool_meta(world)["profile_mapping_approval_id"] == first
    world.faults.clear()
    up = _coordinator().renew_paper_risk_policy_approval(
        tenant_id=TENANT, persona_id=PERSONA, pool_id=POOL, now=clock.now)
    assert up["status"] == "renewed", up.get("gate_error")


def test_pool_that_never_had_an_approved_policy_is_not_drafted(world, clock):
    outcome = _coordinator().renew_paper_risk_policy_approval(
        tenant_id=TENANT, persona_id=PERSONA, pool_id=POOL, now=clock.now)
    assert outcome["status"] == "not_applicable"
    assert _proposals(world) == []


def test_reconcile_pass_renews_without_an_operator_call_and_costs_nothing_above_threshold(world, clock):
    store = MemoryPersonaProvisioningStore()
    record, _ = store.reserve(tenant_id=TENANT, idempotency_key="k1", request_hash="h", normalized_name="n",
                              persona_id=PERSONA, request_payload={})
    pool_id = deterministic_provisioning_ids(record).capital_pool_id
    template = world.cap_main.pool_store.get(POOL)
    from services.capital.pg_store import CapitalPool
    world.cap_main.pool_store.create(CapitalPool(**{**template.__dict__, "pool_id": pool_id, "name": pool_id}))
    base = dict(requested_by="operator-1", requested_profile="low", capital_pool_id=pool_id)
    service.repair_paper_pool_risk_policy(TENANT, PERSONA, **base)
    assert service.repair_paper_pool_risk_policy(TENANT, PERSONA, **base)["after_resolution"]["resolved"] is True
    record.state = "succeeded"
    store.backend.records[(TENANT, "k1")].state = "succeeded"
    first = world.cap_main.pool_store.get(pool_id).metadata["profile_mapping_approval_id"]
    coordinator = PersonaProvisioningCoordinator(
        store=store, transport=service._PersonaOwnerHttpTransport(tenant_id=TENANT),
        schedule_registrar=lambda *a, **k: None, lease_owner="reconciler-a", actor_id=service.PERSONA_OWNER_SERVICE_ACTOR_ID,
        governance_actor_id="pantheon-dev-paper-provisioner")

    def run(current):
        return coordinator.reconcile_paper_risk_policy_renewal(store.get(TENANT, "k1"), now=current)

    run(clock.now)
    ref = store.get(TENANT, "k1").references["paper_risk_policy_renewal"]
    assert ref["status"] == "not_due" and ref["approval_id"] == first
    seen_before = len(world.seen)
    clock.advance(hours=2)
    run(clock.now)  # validity above the threshold: no owner call at all
    assert len(world.seen) == seen_before
    clock.advance(hours=14)  # T+16h: next check time reached, fewer than 8 h remain
    run(clock.now)
    ref = store.get(TENANT, "k1").references["paper_risk_policy_renewal"]
    assert ref["status"] == "renewed", (ref, store.get(TENANT, "k1").references)
    second = world.cap_main.pool_store.get(pool_id).metadata["profile_mapping_approval_id"]
    assert second != first and ref["approval_id"] == second and ref["last_decision_at"]
    count = len([d for d in _proposals(world) if d["target_id"] == pool_id])
    clock.advance(minutes=30)
    run(clock.now)
    assert len([d for d in _proposals(world) if d["target_id"] == pool_id]) == count
    # Another replica holding the lease means skip, not a second decision.
    store.acquire(TENANT, "k1", lease_owner="replica-b", lease_seconds=600)
    clock.advance(hours=9)
    run(clock.now)
    assert len([d for d in _proposals(world) if d["target_id"] == pool_id]) == count
