"""Isolated synthetic policies/approvals; never edits hosted financial data."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json

import pytest

from services.capital.models import ChangePaperRiskProfileRequest
from services.capital.pg_store import CapitalPool, CapitalPoolError, PersistentCapitalPoolStore
from services.capital.profile_change import change_profile, read_change
from services.capital.risk_policy import risk_policy_digest
from services.governance.test_approval_authority import SnapshotApprovalReader, approval_snapshot


def request(profile, key, revision=0):
    return ChangePaperRiskProfileRequest(
        actor_id="fixture-bff", actor_role="capital.admin", expected_persona_id="persona-test",
        requested_by="fixture-operator",
        expected_revision=revision, request_id=hashlib.sha256(key.encode()).hexdigest(),
        request_hash=hashlib.sha256(profile.encode()).hexdigest(), requested_profile=profile,
    )


@pytest.fixture
def change_fixture(tmp_path, monkeypatch):
    tenant = "tenant-profile-test"
    policies, approvals = {}, []
    for tier, limit in (("low", 0.3), ("medium", 0.6), ("high", 0.9)):
        policy = {"risk_policy_id": "fixture-" + tier, "version": "fixture-v2", "status": "active",
                  "allowed_stages": ["paper"], "gross_limit": limit, "net_limit": limit,
                  "max_leverage": 1.0, "max_single_name_weight": limit,
                  "metadata": {"tenant_id": tenant, "requested_profile": tier}}
        policies[tier] = policy
        digest = risk_policy_digest(policy)
        approvals.append(approval_snapshot(
            decision_id="fixture-approved-" + tier, tenant_id=tenant,
            target_type="capital_pool_activation", target_id=policy["risk_policy_id"], target_version=digest,
            owner_user_id="fixture-proposer", risk_level="medium",
            metadata={"subject": {"pool_id": policy["risk_policy_id"], "risk_direction": "increase",
                                   "requested_risk": tier, "risk_policy_id": policy["risk_policy_id"],
                                   "risk_policy_version": policy["version"], "risk_policy_digest": digest},
                      "approvals": [{"actor_id": "fixture-risk-owner", "actor_role": "risk_owner"},
                                    {"actor_id": "fixture-reviewer", "actor_role": "governance_reviewer"}]},
        ))
        (tmp_path / (policy["risk_policy_id"] + ".json")).write_text(json.dumps(policy))
    monkeypatch.setenv("CAPITAL_RISK_POLICY_DIR", str(tmp_path))
    reader = SnapshotApprovalReader(lambda: approvals)
    pool = CapitalPool(pool_id="pool-test", name="Fixture", owner_id=tenant, owner_type="org", status="active",
                       created_at="2026-11-23T00:00:00Z", budget=100000.0,
                       metadata={"tenant_id": tenant, "persona_id": "persona-test", "execution_context": "paper",
                                 "requested_risk_profile": "low", "require_approved_profile_mapping": True,
                                 "risk_policy": policies["low"], "profile_mapping_approval_id": "fixture-approved-low"})
    return pool, reader, policies, approvals


def change(pool, reader, body):
    return change_profile(pool, body, tenant_id="tenant-profile-test", reader=reader, loader=None)


def read(pool, reader):
    return read_change(pool, tenant_id="tenant-profile-test", persona_id="persona-test", reader=reader, loader=None)


def test_approved_later_tier_binds_exact_policy_and_reverses(change_fixture):
    pool, reader, policies, _ = change_fixture
    for revision, tier in enumerate(("medium", "high", "low")):
        pool = change(pool, reader, request(tier, tier, revision))
        receipt = read(pool, reader)
        assert receipt["requestedRisk"] == receipt["effectiveRisk"] == tier
        assert receipt["status"] == "effective"
        assert receipt["policyVersion"] == "fixture-v2"
        assert receipt["policyDigest"] == risk_policy_digest(policies[tier])
        assert pool.metadata["risk_policy"]["gross_limit"] == policies[tier]["gross_limit"]
        assert receipt["approvalId"] == "fixture-approved-" + tier


def test_missing_approval_keeps_verified_effective_then_same_key_reconciles(change_fixture):
    pool, reader, policies, approvals = change_fixture
    medium = approvals.pop(1)
    body = request("medium", "medium")
    pending = change(pool, reader, body)
    receipt = read(pending, reader)
    assert receipt["requestedRisk"] == "medium" and receipt["effectiveRisk"] == "low"
    assert receipt["status"] == "pending_approval"
    assert pending.metadata["risk_policy"] == policies["low"]
    assert receipt["resolution"]["allow_new_or_increased_risk"] is False
    assert receipt["resolution"]["allow_risk_reducing_actions"] is True
    owner_request = receipt["resolution"]["owner_decision_request"]
    assert owner_request["candidate_risk_policy_id"] == policies["medium"]["risk_policy_id"]
    assert owner_request["candidate_risk_policy_digest"] == risk_policy_digest(policies["medium"])
    approvals.append(medium)
    effective = change(pending, reader, body)
    assert read(effective, reader)["status"] == "effective"
    assert change(effective, reader, body) == effective


def test_replay_cannot_revert_newer_intent_and_stale_unseen_edits_conflict(change_fixture):
    pool, reader, _, _ = change_fixture
    first = request("medium", "first")
    medium = change(pool, reader, first)
    high = change(medium, reader, request("high", "second", 1))
    assert change(high, reader, first) == high
    with pytest.raises(CapitalPoolError, match="revision_conflict"):
        change(high, reader, request("low", "out-of-order", 1))
    with pytest.raises(CapitalPoolError, match="idempotency_conflict"):
        change(high, reader, request("low", "first", 2))


@pytest.mark.parametrize("mutation", ["unapproved", "foreign", "live", "digest", "tier", "incomplete", "ambiguous"])
def test_bad_approval_never_unlocks_new_tier(change_fixture, mutation, tmp_path):
    pool, reader, policies, approvals = change_fixture
    approval = approvals[1]
    if mutation == "unapproved":
        approval.update(decision_state="proposed", decision=None)
    elif mutation == "foreign":
        approval["tenant_id"] = "foreign"
    elif mutation == "live":
        policies["medium"]["allowed_stages"] = ["live"]
    elif mutation == "digest":
        policies["medium"]["gross_limit"] = 0.8
    elif mutation == "tier":
        approval["metadata"]["subject"]["requested_risk"] = "high"
    elif mutation == "incomplete":
        approval["metadata"]["approvals"] = []
    else:
        duplicate = deepcopy(policies["medium"])
        duplicate["risk_policy_id"] = "fixture-medium-other"
        digest = risk_policy_digest(duplicate)
        other = deepcopy(approval)
        other.update(decision_id="fixture-other", target_id=duplicate["risk_policy_id"], target_version=digest)
        other["metadata"]["subject"].update(pool_id=duplicate["risk_policy_id"],
                                               risk_policy_id=duplicate["risk_policy_id"], risk_policy_digest=digest)
        approvals.append(other)
        (tmp_path / "fixture-medium-other.json").write_text(json.dumps(duplicate))
    (tmp_path / "fixture-medium.json").write_text(json.dumps(policies["medium"]))
    changed = change(pool, reader, request("medium", "pending"))
    assert read(changed, reader)["status"] == "pending_approval"
    assert changed.metadata["risk_policy"]["risk_policy_id"] == "fixture-low"


@pytest.mark.parametrize("mutation", ["tenant", "persona", "live", "multiple_runtime", "archived"])
def test_cross_scope_and_live_pools_are_never_written(change_fixture, mutation):
    pool, reader, _, _ = change_fixture
    meta = deepcopy(pool.metadata)
    if mutation in ("tenant", "persona"):
        meta[mutation + "_id"] = "foreign"
    elif mutation == "live":
        meta["execution_context"] = "live"
    pool = replace(pool, metadata=meta,
                   single_runtime_enforced=mutation != "multiple_runtime",
                   status="archived" if mutation == "archived" else "active")
    with pytest.raises(CapitalPoolError, match="scope_invalid"):
        change(pool, reader, request("high", "foreign"))


def test_legacy_missing_profile_reports_null_without_guessing_effective(change_fixture):
    pool, reader, _, approvals = change_fixture
    meta = {key: value for key, value in pool.metadata.items()
            if key not in {"requested_risk_profile", "profile_mapping_approval_id"}}
    approvals.clear()
    pending = change(replace(pool, metadata=meta), reader, request("high", "legacy"))
    assert read(pending, reader)["effectiveRisk"] is None
    assert read(pending, reader)["requestedRisk"] == "high"


def test_absent_definition_does_not_propose_installed_old_tier_or_invent_limits(change_fixture, tmp_path):
    pool, reader, _, approvals = change_fixture
    approvals[:] = approvals[:1]
    (tmp_path / "fixture-medium.json").unlink()
    (tmp_path / "fixture-high.json").unlink()
    pending = change(pool, reader, request("medium", "no-definition"))
    decision = read(pending, reader)["resolution"]["owner_decision_request"]
    assert decision["requested_profile"] == "medium"
    assert decision["absence_source"] == "policy_definition_and_approval_absent"
    assert "candidate_pool_approval_digest" not in decision
    assert "candidate_risk_policy_digest" not in decision


def test_suspended_pool_records_request_without_activating_or_claiming_effective(change_fixture):
    pool, reader, policies, _ = change_fixture
    pending = change(replace(pool, status="suspended"), reader, request("medium", "suspended"))
    result = read(pending, reader)
    assert result["requestedRisk"] == "medium" and result["effectiveRisk"] is None
    assert result["status"] == "unavailable"
    assert pending.status == "suspended" and pending.metadata["risk_policy"] == policies["low"]


def test_json_reload_and_lost_response_retry_are_noops(change_fixture, tmp_path):
    pool, reader, _, _ = change_fixture
    path = tmp_path / "pools.json"
    store = PersistentCapitalPoolStore(path)
    store.create(pool)
    body = request("medium", "lost-response")
    first, actual = store.mutate_paper_risk_profile(pool.pool_id, lambda p: change(p, reader, body))
    assert actual
    reloaded = PersistentCapitalPoolStore(path)
    second, actual = reloaded.mutate_paper_risk_profile(pool.pool_id, lambda p: change(p, reader, body))
    assert not actual and second == first
    with pytest.raises(CapitalPoolError, match="ordered"):
        reloaded.patch(pool.pool_id, patch={"metadata": pool.metadata}, updated_at="2026-11-23T01:00:00Z")


def test_receipts_never_evict_old_idempotency_keys_at_bound(change_fixture):
    from services.capital.profile_change import MAX_CHANGE_RECEIPTS

    pool, reader, _, _ = change_fixture
    pool.metadata["risk_profile_change_receipts"] = {str(i): {} for i in range(MAX_CHANGE_RECEIPTS)}
    with pytest.raises(CapitalPoolError, match="receipt_limit"):
        change(pool, reader, request("high", "over-limit"))
    assert len(pool.metadata["risk_profile_change_receipts"]) == MAX_CHANGE_RECEIPTS


def test_pg_independent_writers_order_retries_and_do_not_touch_other_rows(change_fixture):
    import os
    import uuid
    from concurrent.futures import ThreadPoolExecutor
    from services.capital.pg_store import PostgresCapitalPoolStore

    dsn = os.environ.get("CAPITAL_TEST_DSN")
    if not dsn:
        pytest.skip("CAPITAL_TEST_DSN must point to a disposable PostgreSQL instance")
    pool, reader, _, _ = change_fixture
    table = "capital.test_risk_change_" + uuid.uuid4().hex[:10]
    store = PostgresCapitalPoolStore(dsn, table=table)
    try:
        object.__setattr__(pool, "tenant_id", pool.owner_id)
        store.create(pool)
        other = replace(pool, pool_id="unrelated")
        object.__setattr__(other, "tenant_id", pool.owner_id)
        store.create(other)
        def rows():
            with store._records._connect() as conn:
                return conn.execute(f"SELECT record_id,payload,tenant_id,updated_at FROM {store._records.table} ORDER BY record_id").fetchall()
        original = rows()
        stores = [PostgresCapitalPoolStore(dsn, table=table) for _ in range(6)]
        body = request("medium", "concurrent")
        with ThreadPoolExecutor(max_workers=6) as executor:
            outcomes = list(executor.map(
                lambda s: s.mutate_paper_risk_profile(pool.pool_id, lambda p: change(p, reader, body)), stores,
            ))
        assert sum(changed for _, changed in outcomes) == 1
        assert rows()[1] == original[1]
        high, _ = stores[1].mutate_paper_risk_profile(pool.pool_id, lambda p: change(p, reader, request("high", "new", 1)))
        replay, actual = stores[0].mutate_paper_risk_profile(pool.pool_id, lambda p: change(p, reader, body))
        assert not actual and replay == high
        with pytest.raises(CapitalPoolError, match="revision_conflict"):
            stores[2].mutate_paper_risk_profile(pool.pool_id, lambda p: change(p, reader, request("low", "stale", 1)))
        # An old repair already in flight before the latest edit cannot write
        # its stale full metadata snapshot after the ordered owner mutation.
        with pytest.raises(CapitalPoolError, match="ordered"):
            stores[3].patch(pool.pool_id, patch={"metadata": original[0][1]["metadata"]}, updated_at="2026-11-23T01:00:00Z")
        # A status transition and unrelated pool creation cannot overwrite any
        # request receipt from an independent owner facade's stale snapshot.
        stores[4].update_status("unrelated", "archived")
        third = replace(pool, pool_id="third")
        object.__setattr__(third, "tenant_id", pool.owner_id)
        stores[5].create(third)
        assert PostgresCapitalPoolStore(dsn, table=table).require(pool.pool_id) == high
    finally:
        with store._records._connect() as conn:
            conn.execute(f"DROP TABLE {store._records.table}")
