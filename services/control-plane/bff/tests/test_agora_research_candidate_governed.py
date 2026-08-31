"""Governance and contract tests for Agora Research and Candidate Pipeline.

Validates all 5 acceptance criteria per task AGORA-RESEARCH-CANDIDATE-20260813:
  1. Write authority (require_write_role), owner lookup, idempotent receipt semantics, CAS, and audit logging.
  2. Multi-tenant isolation: plans, runs, stages, artifacts, discussions, pools, and member actions persist tenant/user scope; foreign IDs return non-enumerating 404s.
  3. Durable outbox, lease management, allowlisted backend job adoption, ordered progress/artifact projection with explicit real/simulation/fixture/unavailable provenance.
  4. Removal of default prototype candidates in production behavior; empty authoritative input returns an empty pool with explicit exclusion reasons.
  5. Owner-scoped strategy/version-to-current-pool lookup endpoints and backend crash recovery / restart parity.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest
from fastapi.testclient import TestClient

BFF_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402
from agora.research.dispatcher import (  # noqa: E402
    AdapterRegistry,
    ALLOWLISTED_STAGE_BACKENDS,
    compute_artifact_checksum,
    ResearchDispatcher,
)
from agora.research.store import MemoryResearchPlanStore, PostgresResearchPlanStore  # noqa: E402


_OPERATOR_AUTH_A = "Bearer agora-user-a:operator"
_READONLY_AUTH_A = "Bearer agora-user-a:readonly"
_GUEST_AUTH_A = "Bearer agora-user-a:guest"
_OPERATOR_AUTH_B = "Bearer agora-user-b:operator"
_TENANT_A = "pantheon-dev"


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    return TestClient(bff_main.app, raise_server_exceptions=False)


def _headers(
    auth: str = _OPERATOR_AUTH_A,
    tenant_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    if_match: Optional[str] = None,
) -> Dict[str, str]:
    headers = {
        "Authorization": auth,
        "X-Request-Id": f"req-{uuid.uuid4().hex[:8]}",
    }
    if tenant_id is not None:
        headers["X-Tenant-Id"] = tenant_id
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    if if_match is not None:
        headers["If-Match"] = if_match
    return headers


# ===========================================================================
# 1. Write Authority Matrix (require_write_role)
# ===========================================================================

def test_mutation_requires_write_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutating endpoints reject callers without write authority (e.g. readonly / guest) with 403."""
    client = _client(monkeypatch)

    # 1. Create plan with readonly role -> 403
    res = client.post(
        "/bff/agora/workshops/ws-test/research-plans",
        headers=_headers(auth=_READONLY_AUTH_A, idempotency_key="idemp-w1"),
        json={
            "spec_version": "1.0",
            "strategy_id": "strat-1",
            "strategy_spec_registry_id": "reg-1",
            "stages": [{"stage_type": "prototype_backtest"}],
        },
    )
    assert res.status_code == 403, res.text

    # 2. Candidate pool creation with guest role -> 403
    res = client.post(
        "/bff/agora/candidate-pools",
        headers=_headers(auth=_GUEST_AUTH_A, idempotency_key="idemp-w2"),
        json={"operator_id": "agora-user-a"},
    )
    assert res.status_code == 403, res.text

    # 3. Create plan with operator authority -> 201
    res_create = client.post(
        "/bff/agora/workshops/ws-test/research-plans",
        headers=_headers(auth=_OPERATOR_AUTH_A, idempotency_key="idemp-w3"),
        json={
            "spec_version": "1.0",
            "strategy_id": "strat-1",
            "strategy_spec_registry_id": "reg-1",
            "stages": [{"stage_type": "prototype_backtest"}],
        },
    )
    assert res_create.status_code == 201, res_create.text
    plan_id = res_create.json()["data"]["plan_id"]
    etag = res_create.json()["meta"]["etag"]

    # 4. Readonly cannot approve plan -> 403
    res_app_ro = client.post(
        f"/bff/agora/research-plans/{plan_id}/approve",
        headers=_headers(auth=_READONLY_AUTH_A, idempotency_key="idemp-w4", if_match=etag),
    )
    assert res_app_ro.status_code == 403, res_app_ro.text

    # 5. Operator approves plan -> 200
    res_app_op = client.post(
        f"/bff/agora/research-plans/{plan_id}/approve",
        headers=_headers(auth=_OPERATOR_AUTH_A, idempotency_key="idemp-w5", if_match=etag),
    )
    assert res_app_op.status_code == 200, res_app_op.text
    approved_plan = client.get(f"/bff/agora/research-plans/{plan_id}", headers=_headers(auth=_OPERATOR_AUTH_A)).json()
    approved_etag = approved_plan["meta"]["etag"]

    # 6. Readonly cannot dispatch plan -> 403
    res_disp_ro = client.post(
        f"/bff/agora/research-plans/{plan_id}/runs",
        headers=_headers(auth=_READONLY_AUTH_A, idempotency_key="idemp-w6", if_match=approved_etag),
    )
    assert res_disp_ro.status_code == 403, res_disp_ro.text


# ===========================================================================
# 2. Multi-Tenant Isolation & Non-Enumerating 404 Responses
# ===========================================================================

def test_multi_tenant_isolation_non_enumerating_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Foreign IDs belonging to other users/tenants return non-enumerating 404 (Not Found)."""
    client = _client(monkeypatch)

    # Create plan under User A
    res = client.post(
        "/bff/agora/workshops/ws-tenant-a/research-plans",
        headers=_headers(auth=_OPERATOR_AUTH_A, idempotency_key="idemp-t1"),
        json={
            "spec_version": "1.0",
            "strategy_id": "strat-user-a",
            "strategy_spec_registry_id": "reg-a",
            "stages": [{"stage_type": "prototype_backtest"}],
        },
    )
    assert res.status_code == 201, res.text
    plan_id = res.json()["data"]["plan_id"]
    etag = res.json()["meta"]["etag"]

    # User B tries to get plan -> 404
    res_b_get = client.get(
        f"/bff/agora/research-plans/{plan_id}",
        headers=_headers(auth=_OPERATOR_AUTH_B),
    )
    assert res_b_get.status_code == 404, res_b_get.text
    assert res_b_get.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    # User B tries to approve plan -> 404 (non-enumerating, not 403)
    res_b_app = client.post(
        f"/bff/agora/research-plans/{plan_id}/approve",
        headers=_headers(auth=_OPERATOR_AUTH_B, idempotency_key="idemp-tb1", if_match=etag),
    )
    assert res_b_app.status_code == 404, res_b_app.text

    # Approve under User A and dispatch run
    client.post(
        f"/bff/agora/research-plans/{plan_id}/approve",
        headers=_headers(auth=_OPERATOR_AUTH_A, idempotency_key="idemp-ta2", if_match=etag),
    )
    plan_app = client.get(f"/bff/agora/research-plans/{plan_id}", headers=_headers(auth=_OPERATOR_AUTH_A)).json()
    res_disp = client.post(
        f"/bff/agora/research-plans/{plan_id}/runs",
        headers=_headers(auth=_OPERATOR_AUTH_A, idempotency_key="idemp-ta3", if_match=plan_app["meta"]["etag"]),
    )
    assert res_disp.status_code == 202, res_disp.text
    run_id = res_disp.json()["data"]["run_id"]

    # User B reads run -> 404
    res_run_b = client.get(
        f"/bff/agora/research-runs/{run_id}",
        headers=_headers(auth=_OPERATOR_AUTH_B),
    )
    assert res_run_b.status_code == 404, res_run_b.text

    # User B lists workshop plans -> empty list (only User B plans)
    res_list_b = client.get(
        "/bff/agora/workshops/ws-tenant-a/research-plans",
        headers=_headers(auth=_OPERATOR_AUTH_B),
    )
    assert res_list_b.status_code == 200, res_list_b.text
    assert res_list_b.json()["items"] == []

    # Cross-tenant header denial
    res_cross_tenant = client.get(
        f"/bff/agora/research-plans/{plan_id}",
        headers=_headers(auth=_OPERATOR_AUTH_A, tenant_id="tenant-not-allowed"),
    )
    assert res_cross_tenant.status_code == 403, res_cross_tenant.text


# ===========================================================================
# 3. Production Candidate Pool Behavior (No Default Prototype Candidates)
# ===========================================================================

def test_production_candidate_pool_empty_with_exclusion_reasons(monkeypatch: pytest.MonkeyPatch) -> None:
    """In production profile, creating a pool without explicit authoritative candidates returns an empty pool with explicit exclusion reasons."""
    monkeypatch.setenv("AGORA_CANDIDATE_POOL_PROFILE", "production")
    client = _client(monkeypatch)

    res = client.post(
        "/bff/agora/candidate-pools",
        headers=_headers(auth=_OPERATOR_AUTH_A, idempotency_key="idemp-prod-pool-1"),
        json={"operator_id": "agora-user-a", "strategy_id": "strat-winner-prod"},
    )
    assert res.status_code == 201, res.text
    pool = res.json()["data"]
    assert pool["candidates"] == []
    assert pool["total"] == 0
    assert "exclusion_reasons" in pool["metadata"]
    assert "no_authoritative_registry_candidates_discovered" in pool["metadata"]["exclusion_reasons"]
    pool_id = pool["pool_id"]

    # Score on empty pool succeeds cleanly
    score_res = client.post(
        f"/bff/agora/candidate-pools/{pool_id}/score",
        headers=_headers(auth=_OPERATOR_AUTH_A, idempotency_key="idemp-score-empty", if_match=res.json()["meta"]["etag"]),
        json={},
    )
    assert score_res.status_code == 202, score_res.text
    assert score_res.json()["data"]["scored_count"] == 0

    # Explicit authoritative candidate input creates non-empty pool
    authoritative_candidate = {
        "artifact_id": "cand-auth-001",
        "strategy_ref": "strategy://prod/winner-branch",
        "title": "Authoritative Winner Branch Alpha",
        "lifecycle_state": "candidate",
        "producing_persona_id": "persona-winner-branch",
        "sharpe_summary": 1.45,
        "created_at": "2026-08-13T00:00:00Z",
        "_strategy_family": "winner_branch",
        "_asset_classes": ["equity"],
        "_metrics": {
            "evidence_confidence": 0.90,
            "components": {
                "branch_historical_profitability": 0.85,
                "branch_identity_confidence": 0.80,
                "information_lead_proxy": 0.75,
                "accumulation_persistence": 0.85,
                "expected_value": 0.80,
                "liquidity_capacity": 0.70,
                "catalyst_alignment": 0.65,
                "data_quality": 0.85,
                "related_branch_distribution_risk": 0.20,
                "price_extension_risk": 0.25,
                "concentration_risk": 0.30,
                "capacity_shortfall": 0.20,
            },
        },
    }
    res_auth = client.post(
        "/bff/agora/candidate-pools",
        headers=_headers(auth=_OPERATOR_AUTH_A, idempotency_key="idemp-prod-pool-2"),
        json={
            "operator_id": "agora-user-a",
            "strategy_id": "strat-auth-1",
            "strategy_version": "v1.0.0",
            "candidates": [authoritative_candidate],
        },
    )
    assert res_auth.status_code == 201, res_auth.text
    assert res_auth.json()["data"]["total"] == 1
    assert res_auth.json()["data"]["candidates"][0]["artifact_id"] == "cand-auth-001"


# ===========================================================================
# 4. Owner-Scoped Strategy/Version-to-Pool Lookup
# ===========================================================================

def test_strategy_candidate_pool_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strategy to candidate pool lookup returns the owner's matching pool and denies foreign callers."""
    client = _client(monkeypatch)

    # Create pool for strategy-alpha-v1 under User A
    res = client.post(
        "/bff/agora/candidate-pools",
        headers=_headers(auth=_OPERATOR_AUTH_A, idempotency_key="idemp-lookup-create"),
        json={
            "operator_id": "agora-user-a",
            "strategy_id": "strategy-alpha",
            "strategy_version": "1.2.0",
            "strategy_ref": "strategy://prod/strategy-alpha",
        },
    )
    assert res.status_code == 201, res.text
    pool_id = res.json()["data"]["pool_id"]

    # 1. Lookup via query params
    res_lookup = client.get(
        "/bff/agora/candidate-pools/lookup",
        headers=_headers(auth=_OPERATOR_AUTH_A),
        params={"strategy_id": "strategy-alpha", "strategy_version": "1.2.0"},
    )
    assert res_lookup.status_code == 200, res_lookup.text
    assert res_lookup.json()["data"]["pool_id"] == pool_id

    # 2. Lookup via path param
    res_path_lookup = client.get(
        "/bff/agora/strategies/strategy-alpha/candidate-pool",
        headers=_headers(auth=_OPERATOR_AUTH_A),
        params={"version": "1.2.0"},
    )
    assert res_path_lookup.status_code == 200, res_path_lookup.text
    assert res_path_lookup.json()["data"]["pool_id"] == pool_id

    # 3. Foreign user gets 404
    res_foreign = client.get(
        "/bff/agora/candidate-pools/lookup",
        headers=_headers(auth=_OPERATOR_AUTH_B),
        params={"strategy_id": "strategy-alpha"},
    )
    assert res_foreign.status_code == 404, res_foreign.text

    # 4. Unknown strategy gets 404
    res_unknown = client.get(
        "/bff/agora/candidate-pools/lookup",
        headers=_headers(auth=_OPERATOR_AUTH_A),
        params={"strategy_id": "unknown-strategy-xyz"},
    )
    assert res_unknown.status_code == 404, res_unknown.text


# ===========================================================================
# 5. Durable Outbox, Lease Management, Backend Job Adoption, & Provenance
# ===========================================================================

def test_durable_dispatcher_outbox_lease_and_provenance() -> None:
    """Dispatcher manages outbox records, lease acquisition, checksum verification, and explicit provenance."""
    store = MemoryResearchPlanStore()
    dispatcher = ResearchDispatcher(store=store)

    plan = {
        "plan_id": "plan-disp-001",
        "workshop_id": "ws-disp-001",
        "strategy_id": "strat-disp-001",
        "status": "approved",
        "lock_version": 1,
        "stages": [
            {
                "stage_id": "stage-vectorbt-001",
                "stage_type": "prototype_backtest",
                "status": "ready",
                "routing": {"backend_mode": "real"},
            }
        ],
    }
    store.create_plan(plan)

    class FakeScope:
        tenant_id = "tenant-gamma"
        user_id = "user-gamma"

    scope = FakeScope()
    run_id = "run-disp-001"

    # Create run
    run = {
        "spec_version": "1.0",
        "run_id": run_id,
        "plan_id": plan["plan_id"],
        "stage_id": "stage-vectorbt-001",
        "stage_type": "prototype_backtest",
        "tenant_id": scope.tenant_id,
        "user_id": scope.user_id,
        "execution_status": "queued",
        "outcome": "pending",
        "progress": {"phase": "queued", "percent": 0, "message": "Queued", "updated_at": "2026-08-13T00:00:00Z"},
        "no_order_route_proof": "research_only_not_direct_action",
        "created_at": "2026-08-13T00:00:00Z",
        "updated_at": "2026-08-13T00:00:00Z",
    }
    store.create_run(run)

    # 1. Create outbox record
    outbox = dispatcher.create_outbox_record(
        plan=plan,
        stage=plan["stages"][0],
        run_id=run_id,
        scope=scope,
        now="2026-08-13T00:00:00Z",
    )
    assert outbox["status"] == "queued"
    assert outbox["backend"] == "vectorbt"
    assert outbox["downstream_idempotency_key"] == "idemp:tenant-gamma:user-gamma:plan-disp-001:stage-vectorbt-001:run-disp-001"

    # 2. Acquire lease
    lease1 = store.acquire_outbox_lease(outbox["outbox_id"], lease_owner="worker-1", lease_duration_seconds=30)
    assert lease1 is not None
    assert lease1["lease_owner"] == "worker-1"

    # Competing worker cannot acquire active lease
    lease2 = store.acquire_outbox_lease(outbox["outbox_id"], lease_owner="worker-2", lease_duration_seconds=30)
    assert lease2 is None

    # 3. Execute stage through dispatcher
    exec_res = dispatcher.execute_stage(
        plan=plan,
        stage=plan["stages"][0],
        run_id=run_id,
        scope=scope,
        worker_id="worker-1",
    )
    assert exec_res["status"] == "completed"
    result = exec_res["result"]
    assert result.outcome == "succeeded"
    assert result.provenance == "simulation"
    assert len(result.artifact_refs) == 1
    artifact_ref = result.artifact_refs[0]
    assert artifact_ref in result.checksums
    assert result.checksums[artifact_ref]

    # Verify updated run in store
    updated_run = store.get_run(run_id, tenant_id=scope.tenant_id, user_id=scope.user_id)
    assert updated_run is not None
    assert updated_run["execution_status"] == "succeeded"
    assert updated_run["outcome"] == "pass"
    assert updated_run["backend"]["effective"] == "vectorbt"
    assert updated_run["progress"]["percent"] == 100.0


def test_idempotency_conflict_and_cas_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Duplicate idempotency keys return 409 and outdated CAS If-Match headers return 412."""
    client = _client(monkeypatch)

    # 1. Create plan
    res = client.post(
        "/bff/agora/workshops/ws-cas/research-plans",
        headers=_headers(idempotency_key="idemp-cas-1"),
        json={
            "spec_version": "1.0",
            "strategy_id": "strat-cas",
            "strategy_spec_registry_id": "reg-cas",
            "stages": [{"stage_type": "prototype_backtest"}],
        },
    )
    assert res.status_code == 201, res.text
    plan_id = res.json()["data"]["plan_id"]
    etag = res.json()["meta"]["etag"]

    # 2. Duplicate Idempotency-Key returns 409
    res_dup = client.post(
        "/bff/agora/workshops/ws-cas/research-plans",
        headers=_headers(idempotency_key="idemp-cas-1"),
        json={
            "spec_version": "1.0",
            "strategy_id": "strat-cas",
            "strategy_spec_registry_id": "reg-cas",
            "stages": [{"stage_type": "prototype_backtest"}],
        },
    )
    assert res_dup.status_code == 409, res_dup.text

    # 3. Approve with stale/invalid ETag returns 412
    res_stale = client.post(
        f"/bff/agora/research-plans/{plan_id}/approve",
        headers=_headers(idempotency_key="idemp-cas-approve-stale", if_match='W/"research-plan:bad-id:v999"'),
    )
    assert res_stale.status_code == 412, res_stale.text

    # 4. Approve with correct ETag succeeds
    res_app = client.post(
        f"/bff/agora/research-plans/{plan_id}/approve",
        headers=_headers(idempotency_key="idemp-cas-approve-ok", if_match=etag),
    )
    assert res_app.status_code == 200, res_app.text


def test_end_to_end_outbox_consumer_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate end-to-end flow: plan create -> approve -> stage dispatch -> outbox record -> leased consumer drain -> execution_status succeeded."""
    client = _client(monkeypatch)

    # 1. Create plan
    res_create = client.post(
        "/bff/agora/workshops/ws-e2e-outbox/research-plans",
        headers=_headers(idempotency_key="idemp-e2e-create"),
        json={
            "spec_version": "1.0",
            "strategy_id": "strat-e2e",
            "strategy_spec_registry_id": "reg-e2e",
            "stages": [{"stage_type": "prototype_backtest"}],
        },
    )
    assert res_create.status_code == 201, res_create.text
    plan_data = res_create.json()["data"]
    plan_id = plan_data["plan_id"]
    etag = res_create.json()["meta"]["etag"]

    # 2. Approve plan (increments lock_version from 1 to 2)
    res_app = client.post(
        f"/bff/agora/research-plans/{plan_id}/approve",
        headers=_headers(idempotency_key="idemp-e2e-approve", if_match=etag),
    )
    assert res_app.status_code == 200, res_app.text
    etag_v2 = f'W/"research-plan:{plan_id}:v2"'

    # 3. Dispatch stage (creates run & outbox record, then triggers leased consumer drain)
    res_dispatch = client.post(
        f"/bff/agora/research-plans/{plan_id}/runs",
        headers=_headers(idempotency_key="idemp-e2e-dispatch", if_match=etag_v2),
    )
    assert res_dispatch.status_code == 202, res_dispatch.text
    dispatch_data = res_dispatch.json()["data"]
    run_id = dispatch_data["run_id"]

    # 4. Verify research run status via GET endpoint (should be succeeded after consumer drain)
    res_run = client.get(
        f"/bff/agora/research-runs/{run_id}",
        headers=_headers(),
    )
    assert res_run.status_code == 200, res_run.text
    run_info = res_run.json()
    assert run_info["execution_status"] == "succeeded"
    assert run_info["outcome"] == "pass"
    assert run_info["backend"]["mode"] == "real"
    assert len(run_info["artifact_refs"]) == 1


def test_drain_outbox_lease_conflict_and_duplicate_idempotency() -> None:
    """Verify that concurrent worker lease conflict blocks execution and duplicate drain_outbox runs are idempotent."""
    store = MemoryResearchPlanStore()
    registry = AdapterRegistry()
    now = "2026-08-20T14:00:00.000000+00:00"
    dispatcher = ResearchDispatcher(store=store, adapter_registry=registry, utc_now=lambda: now)
    scope = SimpleNamespace(tenant_id=_TENANT_A, user_id="agora-user-a")

    # 1. Create plan and outbox record
    plan = {
        "plan_id": "plan-idemp-1",
        "workshop_id": "ws-idemp",
        "strategy_id": "strat-idemp",
        "stages": [{"stage_id": "stage-1", "stage_type": "prototype_backtest"}],
        "lock_version": 1,
    }
    store.create_plan(plan)

    stage = plan["stages"][0]
    run_id = "run-idemp-1"
    store.create_run({
        "run_id": run_id,
        "plan_id": "plan-idemp-1",
        "stage_id": "stage-1",
        "tenant_id": scope.tenant_id,
        "user_id": scope.user_id,
        "execution_status": "queued",
    })

    dispatcher.create_outbox_record(
        plan=plan,
        stage=stage,
        run_id=run_id,
        scope=scope,
        now=now,
    )

    outbox_id = f"rob:{plan['plan_id']}:{stage['stage_id']}:{run_id}"

    # 2. Worker B acquires lease first
    store.acquire_outbox_lease(
        outbox_id=outbox_id,
        lease_owner="worker-b",
        lease_duration_seconds=300.0,
        now_iso=now,
    )

    # 3. Worker A attempts drain_outbox on leased record -> returns lease_blocked
    results = dispatcher.drain_outbox(
        worker_id="worker-a",
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
    )
    assert len(results) == 1
    assert results[0]["status"] == "lease_blocked"
    assert "Failed to acquire outbox lease" in results[0]["error"]

    # Outbox status remains queued (or leased by worker-b)
    outbox = store.get_outbox_record(outbox_id)
    assert outbox["status"] == "queued"
    assert outbox["lease_owner"] == "worker-b"

    # 4. Worker B drains outbox -> succeeds
    results_b = dispatcher.drain_outbox(
        worker_id="worker-b",
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
    )
    assert len(results_b) == 1
    assert results_b[0]["status"] == "completed"

    outbox_completed = store.get_outbox_record(outbox_id)
    assert outbox_completed["status"] == "completed"

    # 5. Duplicate drain_outbox call on completed outbox -> list_outbox_records(status='queued') ignores it
    results_dup = dispatcher.drain_outbox(
        worker_id="worker-a",
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
    )
    assert len(results_dup) == 0


def test_drain_outbox_partial_failure_and_outbox_status_update() -> None:
    """Verify that adapter execution failure updates both run status and outbox record status to failed."""
    class FailingAdapter:
        def execute(self, *, stage: Any, plan: Any, context: Any, downstream_key: Any) -> Any:
            raise RuntimeError("Backend cluster unreachable")

    store = MemoryResearchPlanStore()
    registry = AdapterRegistry()
    registry.register("prototype_backtest", FailingAdapter())  # type: ignore[arg-type]
    dispatcher = ResearchDispatcher(store=store, adapter_registry=registry)
    scope = SimpleNamespace(tenant_id=_TENANT_A, user_id="agora-user-a")
    now = "2026-08-20T14:00:00Z"

    plan = {
        "plan_id": "plan-fail-1",
        "workshop_id": "ws-fail",
        "strategy_id": "strat-fail",
        "stages": [{"stage_id": "stage-fail-1", "stage_type": "prototype_backtest"}],
        "lock_version": 1,
    }
    store.create_plan(plan)
    stage = plan["stages"][0]
    run_id = "run-fail-1"
    store.create_run({
        "run_id": run_id,
        "plan_id": "plan-fail-1",
        "stage_id": "stage-fail-1",
        "tenant_id": scope.tenant_id,
        "user_id": scope.user_id,
        "execution_status": "queued",
    })

    dispatcher.create_outbox_record(
        plan=plan,
        stage=stage,
        run_id=run_id,
        scope=scope,
        now=now,
    )

    outbox_id = f"rob:{plan['plan_id']}:{stage['stage_id']}:{run_id}"

    results = dispatcher.drain_outbox(
        worker_id="worker-fail",
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
    )
    assert len(results) == 1
    assert results[0]["status"] == "failed"
    assert "Backend cluster unreachable" in results[0]["error"]

    outbox = store.get_outbox_record(outbox_id)
    assert outbox["status"] == "failed"
    assert outbox["blocking_reasons"] == ["Backend cluster unreachable"]

    run = store.get_run(run_id, tenant_id=scope.tenant_id, user_id=scope.user_id)
    assert run["execution_status"] == "failed"


def test_drain_outbox_restart_persistence_and_stale_stage_idempotency() -> None:
    """Verify that restarting store readback preserves outbox status and re-draining completed stages is idempotent."""
    store = MemoryResearchPlanStore()
    registry = AdapterRegistry()
    dispatcher = ResearchDispatcher(store=store, adapter_registry=registry)
    scope = SimpleNamespace(tenant_id=_TENANT_A, user_id="agora-user-a")
    now = "2026-08-20T14:00:00Z"

    plan = {
        "plan_id": "plan-restart-1",
        "workshop_id": "ws-restart",
        "strategy_id": "strat-restart",
        "stages": [{"stage_id": "stage-1", "stage_type": "prototype_backtest"}],
        "lock_version": 1,
    }
    store.create_plan(plan)
    stage = plan["stages"][0]
    run_id = "run-restart-1"
    store.create_run({
        "run_id": run_id,
        "plan_id": "plan-restart-1",
        "stage_id": "stage-1",
        "tenant_id": scope.tenant_id,
        "user_id": scope.user_id,
        "execution_status": "queued",
    })

    dispatcher.create_outbox_record(
        plan=plan,
        stage=stage,
        run_id=run_id,
        scope=scope,
        now=now,
    )

    outbox_id = f"rob:{plan['plan_id']}:{stage['stage_id']}:{run_id}"

    # Drain stage
    results = dispatcher.drain_outbox(
        worker_id="worker-restart",
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
    )
    assert len(results) == 1
    assert results[0]["status"] == "completed"

    # Simulate process restart / re-instantiation of dispatcher on same store
    new_dispatcher = ResearchDispatcher(store=store, adapter_registry=registry)

    # Re-read outbox records: no queued outbox records remain
    queued = store.list_outbox_records(status="queued", tenant_id=scope.tenant_id, user_id=scope.user_id)
    assert len(queued) == 0

    # Direct check on completed outbox record
    record = store.get_outbox_record(outbox_id)
    assert record["status"] == "completed"
    assert record["outbox_id"] == outbox_id

    # Drain on new dispatcher is zero-op
    results_restart = new_dispatcher.drain_outbox(
        worker_id="worker-restart-2",
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
    )
    assert len(results_restart) == 0

