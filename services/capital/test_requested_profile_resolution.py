"""Required profile mappings must be grounded in an actual pool request."""
import json
from dataclasses import asdict

import pytest

from services.capital.pg_store import CapitalPool
from services.capital.risk_policy import (
    discover_approved_profile_risk_policy,
    resolve_pool_risk_policy_binding,
    risk_policy_digest,
)
from services.governance.test_approval_authority import SnapshotApprovalReader, approval_snapshot


@pytest.fixture
def approved_low(tmp_path, monkeypatch):
    tenant = "tenant-profile-test"
    policy = {
        "risk_policy_id": "rp-scoped-test-low", "version": "1.0", "status": "active",
        "allowed_stages": ["paper"], "gross_limit": 0.8, "net_limit": 0.6, "max_leverage": 1.0,
        "metadata": {"tenant_id": tenant, "requested_profile": "low"},
    }
    digest = risk_policy_digest(policy)
    decision = approval_snapshot(
        decision_id="synthetic-catalog-approved-low", tenant_id=tenant,
        target_type="capital_pool_activation", target_id=policy["risk_policy_id"], target_version=digest,
        owner_user_id="fixture-proposer", risk_level="medium",
        metadata={"subject": {
            "pool_id": policy["risk_policy_id"], "risk_direction": "increase", "requested_risk": "low",
            "risk_policy_id": policy["risk_policy_id"], "risk_policy_version": "1.0", "risk_policy_digest": digest,
        }, "approvals": [{"actor_id": "fixture-1", "actor_role": "risk_owner"},
                          {"actor_id": "fixture-2", "actor_role": "governance_reviewer"}]},
    )
    monkeypatch.setenv("CAPITAL_RISK_POLICY_DIR", str(tmp_path))
    (tmp_path / (policy["risk_policy_id"] + ".json")).write_text(json.dumps(policy))
    return tenant, policy, SnapshotApprovalReader([decision])


def requested_pool(tenant, inline, metadata):
    pool_id = "pool-persona-paper-profile-test"
    meta = {"tenant_id": tenant, "persona_id": "persona-test", "execution_context": "paper",
            "require_approved_profile_mapping": True, **metadata}
    if inline:
        meta["risk_policy"] = {
            "risk_policy_id": "rp-" + pool_id, "version": "1.0", "status": "active",
            "allowed_stages": ["paper"], "gross_limit": 2.0, "net_limit": 2.0, "max_leverage": 2.0,
        }
    return CapitalPool(pool_id=pool_id, name=pool_id, owner_id=tenant, owner_type="org",
                       status="active", created_at="2026-11-22T00:00:00Z", metadata=meta)


@pytest.mark.parametrize("typed", [True, False])
@pytest.mark.parametrize("inline", [True, False])
@pytest.mark.parametrize("profile", [None, "low", "medium", "high"])
def test_typed_and_wire_pool_do_not_invent_low(approved_low, typed, inline, profile):
    tenant, policy, reader = approved_low
    pool = requested_pool(tenant, inline, {} if profile is None else {"requested_risk_profile": profile})
    result = resolve_pool_risk_policy_binding(
        pool=pool if typed else asdict(pool), expected_tenant_id=tenant, approval_reader=reader,
    )
    assert result.requested_profile == profile
    assert result.resolved is (profile == "low")
    assert result.allow_new_or_increased_risk is (profile == "low")
    assert result.allow_risk_reducing_actions is True
    if profile == "low":
        assert result.risk_policy_id == policy["risk_policy_id"]
        assert result.profile_mapping_approval_id == "synthetic-catalog-approved-low"
    elif profile is None:
        assert result.owner_decision_request["absence_source"] == "requested_profile_missing"
        assert result.owner_decision_request["requested_profile"] is None


@pytest.mark.parametrize("inline", [True, False])
@pytest.mark.parametrize("other_source", ["alias", "argument", "binding", "binding_pool", "pool_wire", "invalid"])
def test_conflicting_sources_cannot_be_hidden_by_precedence(approved_low, inline, other_source):
    tenant, _, reader = approved_low
    metadata = {"requested_risk_profile": "low"}
    options = {}
    if other_source == "alias":
        metadata["risk"] = "high"
    elif other_source == "argument":
        options["requested_profile"] = "high"
    elif other_source == "binding":
        options["binding"] = {"metadata": {"requested_risk_profile": "high"}}
    elif other_source == "binding_pool":
        options["binding"] = {"capital_pool": {"metadata": {"risk_profile": "high"}}}
    elif other_source == "invalid":
        metadata["requested_risk_profile"] = ["low"]
    pool = requested_pool(tenant, inline, metadata)
    if other_source == "pool_wire":
        pool = {**asdict(pool), "requested_risk_profile": "high"}
    result = resolve_pool_risk_policy_binding(pool=pool, expected_tenant_id=tenant, approval_reader=reader, **options)
    assert not result.resolved and not result.allow_new_or_increased_risk
    assert result.allow_risk_reducing_actions
    assert result.requested_profile is None
    assert result.owner_decision_request["absence_source"] == (
        "requested_profile_invalid" if other_source == "invalid" else "requested_profile_conflict"
    )


def test_discovery_requires_request_and_policy_metadata_cannot_supply_it(approved_low):
    tenant, policy, reader = approved_low
    for profile in (None, "", " ", ["low"]):
        result = discover_approved_profile_risk_policy(
            resolved_pool_id="pool-test", effective_tenant_id=tenant,
            clean_profile=profile, approval_reader=reader,
        )
        assert not result.resolved
        assert result.absence_source in {"requested_profile_missing", "requested_profile_invalid"}
    pool = requested_pool(tenant, False, {})
    pool.metadata["risk_policy"] = policy
    result = resolve_pool_risk_policy_binding(pool=pool, expected_tenant_id=tenant, approval_reader=reader)
    assert not result.resolved and result.requested_profile is None
    assert result.owner_decision_request["absence_source"] == "requested_profile_missing"


def test_legacy_generic_inline_implicitly_requires_request_but_explicit_contract_survives(approved_low):
    tenant, _, reader = approved_low
    pool = requested_pool(tenant, True, {})
    pool.metadata.pop("require_approved_profile_mapping")
    result = resolve_pool_risk_policy_binding(pool=pool, expected_tenant_id=tenant, approval_reader=reader)
    assert not result.resolved
    assert result.owner_decision_request["absence_source"] == "requested_profile_missing"
    for policy_id, require in (("rp-explicit-contract", None), ("rp-" + pool.pool_id, False)):
        pool.metadata["risk_policy"]["risk_policy_id"] = policy_id
        result = resolve_pool_risk_policy_binding(
            pool=pool, expected_tenant_id=tenant, require_approved_profile_mapping=require,
        )
        assert result.resolved and result.allow_new_or_increased_risk
        assert result.requested_profile is None


@pytest.mark.parametrize("conflict", [False, True])
def test_unknown_request_preserves_governed_exit_and_reduction(approved_low, conflict):
    from services.execution.lean_runtime.paper_runtime import PaperExecutionAlgorithm

    tenant, _, _ = approved_low
    metadata = {"requested_risk_profile": "low", "risk": "high"} if conflict else {}
    pool = requested_pool(tenant, True, metadata)
    algo = PaperExecutionAlgorithm(initial_cash=600_000.0, persona_binding_id="pcb-test",
                                  capital_pool_id=pool.pool_id, capital_pool_reader=lambda _: asdict(pool),
                                  in_process_broker=True)
    algo._holding("2330").Quantity = 1000.0
    algo._instrument_positions[("pcb-test", "2330", "eq:2330")] = {
        "persona_binding_id": "pcb-test", "normalised_underlying": "2330", "instrument_id": "eq:2330",
        "symbol": "2330", "kind": "cash_share", "quantity": 1000.0, "multiplier": 1.0,
        "average_entry_price": 600.0, "mark_price": 600.0, "status": "open",
    }
    for side, qty, allowed in (("sell", 1000.0, True), ("sell", 400.0, True),
                               ("sell", 1200.0, False), ("buy", 100.0, False)):
        result = algo.EvaluateOrderRiskGate(symbol="2330", side=side, quantity=qty, price=600.0,
                                          instrument_id="eq:2330", persona_binding_id="pcb-test")
        assert result["accepted"] is allowed, result
