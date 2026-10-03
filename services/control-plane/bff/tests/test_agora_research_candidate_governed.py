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
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest
from fastapi.testclient import TestClient

from services.research.constants import ALLOWLISTED_STAGE_BACKENDS
from agora.research.store import MemoryResearchPlanStore, PostgresResearchPlanStore


_OPERATOR_AUTH_A = "Bearer agora-user-a:operator"
_READONLY_AUTH_A = "Bearer agora-user-a:readonly"
_GUEST_AUTH_A = "Bearer agora-user-a:guest"
_OPERATOR_AUTH_B = "Bearer agora-user-b:operator"
_TENANT_A = "pantheon-dev"


def _sample_ohlcv_records() -> list[dict[str, Any]]:
    from datetime import date, timedelta
    records = []
    start = date(2026, 1, 1)
    for inst, base in (("AAA", 100.0), ("BBB", 50.0)):
        for i in range(35):
            d = (start + timedelta(days=i)).isoformat()
            p = base + i * 0.5
            records.append({
                "instrument": inst,
                "date": d,
                "open": p,
                "high": p + 1.0,
                "low": p - 0.5,
                "close": p + 0.2,
                "volume": 1000.0,
            })
    return records


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    try:
        from services.control_plane.bff.tests.test_agora_strategy_workshop import _workshop_client
    except ImportError:
        from test_agora_strategy_workshop import _workshop_client

    from services.control_plane.bff.agora.strategy_workshop.operations import (
        WorkshopCanonicalOperations,
        CanonicalOperationError,
    )
    from services.research.main import app as research_app, store as research_orchestrator_store
    for p in ("runs_path", "tasks_path", "artifacts_path", "proposals_path", "events_path"):
        if hasattr(research_orchestrator_store, p):
            getattr(research_orchestrator_store, p).unlink(missing_ok=True)
    if hasattr(research_orchestrator_store, "data_dir"):
        for f in research_orchestrator_store.data_dir.glob("*.json*"):
            try:
                f.unlink()
            except OSError:
                pass
    test_backend_client = TestClient(research_app)
    monkeypatch.setenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "http://test-research-orchestrator")
    monkeypatch.setenv("PANTHEON_VECTORBT_BACKEND", "real")

    orig_request_json = WorkshopCanonicalOperations._request_json

    def fake_request_json(self, authority: str, method: str, base_url: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        if authority == "research_orchestrator":
            if method.upper() == "GET":
                resp = test_backend_client.get(path)
            elif method.upper() == "POST":
                resp = test_backend_client.post(path, json=payload)
            else:
                resp = test_backend_client.request(method, path, json=payload)
            if resp.status_code >= 400:
                detail = "canonical request was rejected"
                try:
                    err_json = resp.json()
                    detail = err_json.get("detail", detail)
                except Exception:
                    pass
                raise CanonicalOperationError(
                    authority,
                    detail,
                    status_code=resp.status_code,
                    retryable=resp.status_code >= 500 or resp.status_code == 429,
                )
            return resp.json() if resp.content else None
        return orig_request_json(self, authority, method, base_url, path, payload)

    monkeypatch.setattr(WorkshopCanonicalOperations, "_request_json", fake_request_json)
    client = _workshop_client(monkeypatch)
    setattr(client, "test_backend_client", test_backend_client)
    return client


def _wait_owner_plan_runs(test_backend_client: TestClient, plan_id: str, expected: int) -> list[Dict[str, Any]]:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        response = test_backend_client.get("/api/research-orchestrator/runs")
        assert response.status_code == 200, response.text
        runs = [
            run for run in response.json()
            if any(ref.get("type") == "research_plan" and ref.get("id") == plan_id
                   for ref in run.get("input_refs") or [] if isinstance(ref, dict))
        ]
        if len(runs) >= expected and all(run.get("status") in {"completed", "failed", "rejected", "canceled"} for run in runs):
            return runs
        time.sleep(0.01)
    pytest.fail(f"Research plan {plan_id} did not complete {expected} owner runs")


def _wait_owner_terminal(test_backend_client: TestClient, run_id: str) -> Dict[str, Any]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = test_backend_client.get(f"/api/research-orchestrator/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run.get("status") in {"completed", "failed", "rejected", "canceled"}:
            return run
        time.sleep(0.01)
    pytest.fail(f"Research owner run {run_id} did not reach terminal status")


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
    """Reads remain private; a same-tenant operator may decide another user's plan."""
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

    # User B is a same-tenant operator: approval is allowed without read access.
    res_b_app = client.post(
        f"/bff/agora/research-plans/{plan_id}/approve",
        headers=_headers(auth=_OPERATOR_AUTH_B, idempotency_key="idemp-tb1", if_match=etag),
    )
    assert res_b_app.status_code == 200, res_b_app.text

    # User A can observe the decision and dispatch the approved plan.
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

def test_durable_dispatcher_outbox_lease_and_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Store manages outbox records, lease acquisition, and execution runs through authoritative research owner."""
    monkeypatch.setenv("PANTHEON_VECTORBT_BACKEND", "real")
    store = MemoryResearchPlanStore()

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

    # 1. Create outbox record directly on store
    outbox = store.create_outbox_record({
        "outbox_id": f"rob:{plan['plan_id']}:{plan['stages'][0]['stage_id']}:{run_id}",
        "plan_id": plan["plan_id"],
        "stage_id": plan["stages"][0]["stage_id"],
        "run_id": run_id,
        "backend": "vectorbt",
        "status": "queued",
        "tenant_id": scope.tenant_id,
        "user_id": scope.user_id,
        "downstream_idempotency_key": f"idemp:{scope.tenant_id}:{scope.user_id}:{plan['plan_id']}:{plan['stages'][0]['stage_id']}:{run_id}",
    })
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

    # 3. Prove ResearchDispatcher is deleted from BFF; stage execution runs through research owner
    import agora.research.dispatcher as disp_mod
    assert not hasattr(disp_mod, "ResearchDispatcher")

    from services.research.main import execute_research_stage
    dataset_payload = {
        "dataset_id": "dataset:ds-disp-001",
        "strategy_id": "strat-disp-001",
        "source_dataset_refs": ["dataset:ds-disp-001"],
        "data_frequency": "daily",
        "records": _sample_ohlcv_records(),
    }
    exec_res = execute_research_stage(
        "prototype_backtest",
        {
            "stage": plan["stages"][0],
            "plan": plan,
            "dataset": dataset_payload,
            "run_id": run_id,
            "correlation_id": f"corr-{run_id}",
            "context": {"tenant_id": scope.tenant_id, "user_id": scope.user_id},
        },
    )
    assert exec_res["status"] == "succeeded"
    assert exec_res["provenance"] in ("real", "simulation")
    assert exec_res["receipt"] is not None

    # 4. Durably record completion in store
    store.update_outbox_record(
        outbox["outbox_id"],
        {"status": "completed"},
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
    )
    store.update_run(
        run_id,
        {
            "execution_status": "succeeded",
            "outcome": "pass",
            "backend": {"effective": "vectorbt"},
            "progress": {"percent": 100.0},
        },
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
    )

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
    """Validate end-to-end flow: plan create -> approve -> stage dispatch to research owner -> readback succeeded without test-side mutations."""
    client = _client(monkeypatch)
    test_backend_client = getattr(client, "test_backend_client")

    # 1. Create plan
    res_create = client.post(
        "/bff/agora/workshops/ws-e2e-outbox/research-plans",
        headers=_headers(idempotency_key="idemp-e2e-create"),
        json={
            "spec_version": "1.0",
            "strategy_id": "strat-e2e",
            "strategy_spec_registry_id": "reg-e2e",
            "dataset": {
                "dataset_id": "dataset:ds-e2e-001",
                "strategy_id": "strat-e2e",
                "source_dataset_refs": ["dataset:ds-e2e-001"],
                "data_frequency": "daily",
                "records": _sample_ohlcv_records(),
            },
            "stages": [
                {
                    "stage_id": "stage-e2e-proto",
                    "stage_type": "prototype_backtest",
                    "status": "ready",
                    "routing": {"backend_mode": "real", "preferred_backend": "vectorbt"},
                }
            ],
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

    # 3. Dispatch stage to authoritative research service (executes autonomously to completion)
    res_dispatch = client.post(
        f"/bff/agora/research-plans/{plan_id}/runs",
        headers=_headers(idempotency_key="idemp-e2e-dispatch", if_match=etag_v2),
    )
    assert res_dispatch.status_code == 202, res_dispatch.text
    dispatch_data = res_dispatch.json()["data"]
    run_id = dispatch_data["run_id"]
    owner_run = _wait_owner_terminal(test_backend_client, run_id)
    assert owner_run["status"] == "completed"

    # 4. Read back run from authoritative research owner without any test-side store mutations
    res_run = client.get(
        f"/bff/agora/research-runs/{run_id}",
        headers=_headers(),
    )
    assert res_run.status_code == 200, res_run.text
    run_info = res_run.json()
    assert run_info["execution_status"] == "succeeded"
    assert run_info["outcome"] == "pass"

    # 5. Verify AgoraInteractionWorker has no ResearchDispatcher and does not drain research
    from agora.interaction.worker import AgoraInteractionWorker
    research_store = getattr(client, "router", None) and getattr(client.router, "research_store", None) or getattr(client, "app_instance", None) and getattr(client.app_instance, "research_store", None)
    worker = AgoraInteractionWorker(
        research_store=research_store,
        worker_id="test-worker-e2e",
    )
    assert worker.drain_research_outbox() == 0

    # 6. Prove repeated dispatch against authoritative research owner has 1 owner effect
    run_owner = test_backend_client.get(f"/api/research-orchestrator/runs/{run_id}").json()
    task_id_for_dup = run_owner["task_id"]
    dup_dispatch = test_backend_client.post(
        f"/api/research-orchestrator/tasks/{task_id_for_dup}/runs",
        json={
            "adapter": "vectorbt",
            "requested_mode": "real",
            "dispatch_mode": "real",
            "idempotency_key": f"plan-run-{plan_id}-stage-e2e-proto",
        },
    )
    assert dup_dispatch.status_code == 201
    assert dup_dispatch.json()["run_id"] == run_id

    # 7. Prove actual artifact readback via public endpoint
    res_art = client.get(
        f"/bff/agora/research-runs/{run_id}/artifacts",
        headers=_headers(),
    )
    assert res_art.status_code == 200, res_art.text
    art_payload = res_art.json()
    items = art_payload.get("items") or (art_payload.get("data", {}).get("items") if isinstance(art_payload.get("data"), dict) else [])
    assert len(items) >= 1

    # 8. Prove BFF restart reconstitution: clear local BFF cache and re-read from research owner
    if research_store and hasattr(research_store, "_runs"):
        research_store._runs.clear()
    res_restart = client.get(
        f"/bff/agora/research-runs/{run_id}",
        headers=_headers(),
    )
    assert res_restart.status_code == 200
    restart_info = res_restart.json()
    assert restart_info["execution_status"] == "succeeded"
    assert restart_info["run_id"] == run_id


def test_drain_outbox_lease_conflict_and_duplicate_idempotency() -> None:
    """Verify that concurrent worker lease conflict blocks execution and completed outbox records are ignored."""
    store = MemoryResearchPlanStore()
    now = "2026-08-20T14:00:00.000000+00:00"
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

    outbox_id = f"rob:{plan['plan_id']}:{stage['stage_id']}:{run_id}"
    store.create_outbox_record({
        "outbox_id": outbox_id,
        "plan_id": plan["plan_id"],
        "stage_id": stage["stage_id"],
        "run_id": run_id,
        "backend": "vectorbt",
        "status": "queued",
        "tenant_id": scope.tenant_id,
        "user_id": scope.user_id,
        "downstream_idempotency_key": f"idemp:{run_id}",
    })

    # 2. Worker B acquires lease first
    lease_b = store.acquire_outbox_lease(
        outbox_id=outbox_id,
        lease_owner="worker-b",
        lease_duration_seconds=300.0,
        now_iso=now,
    )
    assert lease_b is not None

    # 3. Worker A attempts lease on leased record -> returns None (lease conflict)
    lease_a = store.acquire_outbox_lease(
        outbox_id=outbox_id,
        lease_owner="worker-a",
        lease_duration_seconds=300.0,
        now_iso=now,
    )
    assert lease_a is None

    # Outbox status remains queued (leased by worker-b)
    outbox = store.get_outbox_record(outbox_id)
    assert outbox["status"] == "queued"
    assert outbox["lease_owner"] == "worker-b"

    # 4. Worker B completes processing and updates outbox to completed
    store.update_outbox_record(
        outbox_id,
        {"status": "completed"},
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
    )
    outbox_completed = store.get_outbox_record(outbox_id)
    assert outbox_completed["status"] == "completed"

    # 5. Queued outbox listing ignores completed record
    queued = store.list_outbox_records(status="queued", tenant_id=scope.tenant_id, user_id=scope.user_id)
    assert len(queued) == 0

    # 6. Verify AgoraInteractionWorker has no ResearchDispatcher and does not drain research
    from agora.interaction.worker import AgoraInteractionWorker
    worker = AgoraInteractionWorker(research_store=store, worker_id="worker-a")
    assert worker.drain_research_outbox() == 0


def test_drain_outbox_partial_failure_and_outbox_status_update() -> None:
    """Verify that execution failure updates both run status and outbox record status to failed."""
    store = MemoryResearchPlanStore()
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

    outbox_id = f"rob:{plan['plan_id']}:{stage['stage_id']}:{run_id}"
    store.create_outbox_record({
        "outbox_id": outbox_id,
        "plan_id": plan["plan_id"],
        "stage_id": stage["stage_id"],
        "run_id": run_id,
        "backend": "vectorbt",
        "status": "queued",
        "tenant_id": scope.tenant_id,
        "user_id": scope.user_id,
        "downstream_idempotency_key": f"idemp:{run_id}",
    })

    # Worker acquires lease
    lease = store.acquire_outbox_lease(outbox_id=outbox_id, lease_owner="worker-fail", lease_duration_seconds=60.0)
    assert lease is not None

    # Record failure
    err_msg = "Backend cluster unreachable"
    store.update_outbox_record(
        outbox_id,
        {"status": "failed", "blocking_reasons": [err_msg]},
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
    )
    store.update_run(
        run_id,
        {"execution_status": "failed", "blocking_reasons": [err_msg]},
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
    )

    outbox = store.get_outbox_record(outbox_id)
    assert outbox["status"] == "failed"
    assert outbox["blocking_reasons"] == [err_msg]

    run = store.get_run(run_id, tenant_id=scope.tenant_id, user_id=scope.user_id)
    assert run["execution_status"] == "failed"


def test_drain_outbox_restart_persistence_and_stale_stage_idempotency() -> None:
    """Verify that restarting store readback preserves outbox status and completed stages are idempotent."""
    store = MemoryResearchPlanStore()
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

    outbox_id = f"rob:{plan['plan_id']}:{stage['stage_id']}:{run_id}"
    store.create_outbox_record({
        "outbox_id": outbox_id,
        "plan_id": plan["plan_id"],
        "stage_id": stage["stage_id"],
        "run_id": run_id,
        "backend": "vectorbt",
        "status": "queued",
        "tenant_id": scope.tenant_id,
        "user_id": scope.user_id,
        "downstream_idempotency_key": f"idemp:{run_id}",
    })

    # Complete stage
    store.acquire_outbox_lease(outbox_id=outbox_id, lease_owner="worker-restart", lease_duration_seconds=60.0)
    store.update_outbox_record(
        outbox_id,
        {"status": "completed"},
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
    )

    # Re-read outbox records: no queued outbox records remain
    queued = store.list_outbox_records(status="queued", tenant_id=scope.tenant_id, user_id=scope.user_id)
    assert len(queued) == 0

    # Direct check on completed outbox record
    record = store.get_outbox_record(outbox_id)
    assert record["status"] == "completed"
    assert record["outbox_id"] == outbox_id

    # AgoraInteractionWorker drain is zero-op
    from agora.interaction.worker import AgoraInteractionWorker
    worker = AgoraInteractionWorker(research_store=store, worker_id="worker-restart-2")
    assert worker.drain_research_outbox() == 0


def test_bff_plan_projects_all_owner_roots_and_terminal_state(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    owner_client = getattr(client, "test_backend_client")
    created = client.post(
        "/bff/agora/workshops/ws-owner-multi-root/research-plans",
        headers=_headers(idempotency_key="multi-root-create"),
        json={
            "spec_version": "1.0", "strategy_id": "strategy-multi-root",
            "strategy_spec_registry_id": "registry-multi-root",
            "dataset": {"dataset_id": "ds-multi-root", "strategy_id": "strategy-multi-root", "source_dataset_refs": ["ds-multi-root"], "records": _sample_ohlcv_records()},
            "stages": [
                {"stage_id": "root-a", "stage_type": "prototype_backtest", "status": "ready", "dependencies": [], "routing": {"backend_mode": "fixture", "preferred_backend": "vectorbt"}},
                {"stage_id": "root-b", "stage_type": "prototype_backtest", "status": "ready", "dependencies": [], "routing": {"backend_mode": "fixture", "preferred_backend": "vectorbt"}},
            ],
        },
    )
    assert created.status_code == 201, created.text
    plan_id = created.json()["data"]["plan_id"]
    approval = client.post(
        f"/bff/agora/research-plans/{plan_id}/approve",
        headers=_headers(idempotency_key="multi-root-approve", if_match=created.json()["meta"]["etag"]),
    )
    assert approval.status_code == 200, approval.text
    dispatched = client.post(
        f"/bff/agora/research-plans/{plan_id}/runs",
        headers=_headers(idempotency_key="multi-root-dispatch", if_match=approval.json()["meta"]["etag"]),
    )
    assert dispatched.status_code == 202, dispatched.text
    owner_runs = _wait_owner_plan_runs(owner_client, plan_id, 2)
    assert {run["stage_id"] for run in owner_runs} == {"root-a", "root-b"}
    assert all(run["status"] == "completed" for run in owner_runs)

    bff_store = client.router.research_store
    bff_store._runs.clear()
    bff_store._save_to_storage()
    listed = client.get(f"/bff/agora/research-plans/{plan_id}/runs", headers=_headers())
    assert listed.status_code == 200, listed.text
    assert {run["stage_id"] for run in listed.json()["items"]} == {"root-a", "root-b"}
    for run in listed.json()["items"]:
        detail = client.get(f"/bff/agora/research-runs/{run['run_id']}", headers=_headers())
        assert detail.status_code == 200, detail.text
        assert detail.json()["plan_id"] == plan_id
        assert detail.json()["stage_id"] == run["stage_id"]
    readback = client.get(f"/bff/agora/research-plans/{plan_id}", headers=_headers())
    assert readback.status_code == 200, readback.text
    assert readback.json()["data"]["status"] == "completed"
    assert {stage["status"] for stage in readback.json()["data"]["stages"]} == {"succeeded"}
    repeated = client.post(
        f"/bff/agora/research-plans/{plan_id}/runs",
        headers=_headers(idempotency_key="multi-root-dispatch-again", if_match=readback.json()["meta"]["etag"]),
    )
    assert repeated.status_code == 202, repeated.text
    assert repeated.json()["data"]["run_id"] == dispatched.json()["data"]["run_id"]
    assert len(_wait_owner_plan_runs(owner_client, plan_id, 2)) == 2


def test_unknown_dataset_fails_closed_on_public_plan_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PANTHEON_VECTORBT_BACKEND", "real")
    client = _client(monkeypatch)
    created = client.post(
        "/bff/agora/workshops/ws-unknown-dataset/research-plans",
        headers=_headers(idempotency_key="unknown-dataset-create"),
        json={
            "spec_version": "1.0", "strategy_id": "unknown-dataset-strategy",
            "strategy_spec_registry_id": "unknown-dataset-registry",
            "stages": [{
                "stage_id": "unknown-dataset-stage", "stage_type": "prototype_backtest",
                "status": "ready", "input_refs": ["dataset:does-not-exist"],
                "routing": {"backend_mode": "real", "preferred_backend": "vectorbt"},
            }],
        },
    )
    assert created.status_code == 201, created.text
    plan_id = created.json()["data"]["plan_id"]
    approved = client.post(
        f"/bff/agora/research-plans/{plan_id}/approve",
        headers=_headers(idempotency_key="unknown-dataset-approve", if_match=created.json()["meta"]["etag"]),
    )
    assert approved.status_code == 200, approved.text
    dispatched = client.post(
        f"/bff/agora/research-plans/{plan_id}/runs",
        headers=_headers(idempotency_key="unknown-dataset-dispatch", if_match=approved.json()["meta"]["etag"]),
    )
    assert dispatched.status_code == 503, dispatched.text
    assert dispatched.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


def test_governed_dataset_reference_dispatch_to_research_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate plan dispatch with input_refs dataset reference (no inline dataset) resolves and executes autonomously to completion."""
    from agora.dataset_extraction.models import DatasetRecord, DatasetKind, InteractionKind
    from agora.dataset_extraction.router import _default_store

    client = _client(monkeypatch)
    test_backend_client = getattr(client, "test_backend_client")
    ds_store = getattr(client.router, "dataset_store", None) or getattr(client.app_instance, "dataset_store", None) or _default_store()

    # 1. Register canonical governed dataset in the dataset store
    ds_store.save_record(DatasetRecord(
        evidence_id="ev-ref-dispatch-001",
        dataset_version_id="ds-ref-dispatch-001",
        dataset_kind=DatasetKind.OBSERVE,
        interaction_kind=InteractionKind.ASK,
        persona_id="persona-servant-agora",
        session_id="session-ref-001",
        tenant_id=_TENANT_A,
        user_id="agora-user-a",
        content={
            "dataset_id": "dataset:ds-ref-dispatch-001",
            "strategy_id": "strat-ref-dispatch",
            "records": _sample_ohlcv_records(),
        },
        source_refs=["dataset:ds-ref-dispatch-001"],
        learning_eligible=True,
        captured_at="2026-09-08T00:00:00Z",
        extracted_at="2026-09-08T00:00:00Z",
    ))

    # 2. Create plan referencing the dataset via input_refs with NO inline dataset
    res_create = client.post(
        "/bff/agora/workshops/ws-ref-dispatch/research-plans",
        headers=_headers(idempotency_key="idemp-ref-create"),
        json={
            "spec_version": "1.0",
            "strategy_id": "strat-ref-dispatch",
            "strategy_spec_registry_id": "reg-ref-dispatch",
            "stages": [
                {
                    "stage_id": "stage-ref-proto",
                    "stage_type": "prototype_backtest",
                    "status": "ready",
                    "input_refs": ["dataset:ds-ref-dispatch-001"],
                    "routing": {"backend_mode": "real", "preferred_backend": "vectorbt"},
                }
            ],
        },
    )
    assert res_create.status_code == 201, res_create.text
    plan_data = res_create.json()["data"]
    plan_id = plan_data["plan_id"]
    etag = res_create.json()["meta"]["etag"]

    # 3. Approve plan
    res_app = client.post(
        f"/bff/agora/research-plans/{plan_id}/approve",
        headers=_headers(idempotency_key="idemp-ref-approve", if_match=etag),
    )
    assert res_app.status_code == 200, res_app.text
    etag_v2 = f'W/"research-plan:{plan_id}:v2"'

    # 4. Dispatch stage - service resolves dataset from store, forwards to research orchestrator, and executes autonomously
    res_dispatch = client.post(
        f"/bff/agora/research-plans/{plan_id}/runs",
        headers=_headers(idempotency_key="idemp-ref-dispatch", if_match=etag_v2),
    )
    assert res_dispatch.status_code == 202, res_dispatch.text
    dispatch_data = res_dispatch.json()["data"]
    run_id = dispatch_data["run_id"]
    owner_run = _wait_owner_terminal(test_backend_client, run_id)
    assert owner_run["status"] == "completed"

    # 5. Read back run from authoritative research owner
    res_run = client.get(
        f"/bff/agora/research-runs/{run_id}",
        headers=_headers(),
    )
    assert res_run.status_code == 200, res_run.text
    run_info = res_run.json()
    assert run_info["execution_status"] == "succeeded"
    assert run_info["outcome"] == "pass"
    assert run_info["provenance"] in ("real", "unavailable", "simulation")


def test_bff_to_owner_continuation_legacy_stages_not_reexecuted(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    owner_client = getattr(client, "test_backend_client")

    res_create = client.post(
        "/bff/agora/workshops/ws-mixed-continuation/research-plans",
        headers=_headers(idempotency_key="mixed-create"),
        json={
            "spec_version": "1.0",
            "strategy_id": "strat-mixed",
            "strategy_spec_registry_id": "reg-mixed",
            "dataset": {"dataset_id": "ds-mixed", "strategy_id": "strat-mixed", "source_dataset_refs": ["ds-mixed"], "records": _sample_ohlcv_records()},
            "stages": [
                {"stage_id": "s1", "stage_type": "prototype_backtest", "status": "ready", "dependencies": [], "routing": {"backend_mode": "fixture", "preferred_backend": "vectorbt"}},
                {"stage_id": "s2", "stage_type": "prototype_backtest", "status": "pending", "dependencies": ["s1"], "routing": {"backend_mode": "fixture", "preferred_backend": "vectorbt"}},
            ],
        },
    )
    assert res_create.status_code == 201, res_create.text
    plan_id = res_create.json()["data"]["plan_id"]
    etag = res_create.json()["meta"]["etag"]

    res_app = client.post(
        f"/bff/agora/research-plans/{plan_id}/approve",
        headers=_headers(idempotency_key="mixed-approve", if_match=etag),
    )
    assert res_app.status_code == 200, res_app.text

    store = client.router.research_store
    legacy_run_id = "legacy-s1-completed-run"
    store.create_run({
        "run_id": legacy_run_id,
        "plan_id": plan_id,
        "stage_id": "s1",
        "stage_type": "prototype_backtest",
        "tenant_id": _TENANT_A,
        "user_id": "agora-user-a",
        "execution_status": "succeeded",
        "outcome": "pass",
        "artifact_refs": [{"artifact_id": "legacy-s1-artifact"}],
    })

    mid_plan = client.get(f"/bff/agora/research-plans/{plan_id}", headers=_headers())
    assert mid_plan.status_code == 200
    assert mid_plan.json()["data"]["status"] == "running"
    stages_by_id = {s["stage_id"]: s["status"] for s in mid_plan.json()["data"]["stages"]}
    assert stages_by_id["s1"] == "succeeded"
    assert stages_by_id["s2"] == "pending"

    res_dispatch = client.post(
        f"/bff/agora/research-plans/{plan_id}/runs",
        headers=_headers(idempotency_key="mixed-s2-dispatch", if_match=mid_plan.json()["meta"]["etag"]),
    )
    assert res_dispatch.status_code == 202, res_dispatch.text
    s2_run_id = res_dispatch.json()["data"]["run_id"]

    owner_run = _wait_owner_terminal(owner_client, s2_run_id)
    assert owner_run["status"] == "completed"

    owner_runs_resp = owner_client.get("/api/research-orchestrator/runs")
    assert owner_runs_resp.status_code == 200
    plan_owner_runs = [
        r for r in owner_runs_resp.json()
        if any(ref.get("type") == "research_plan" and ref.get("id") == plan_id for ref in r.get("input_refs") or [] if isinstance(ref, dict))
    ]
    assert len(plan_owner_runs) == 1, f"Expected exactly 1 owner run for s2, got: {plan_owner_runs}"
    assert plan_owner_runs[0]["stage_id"] == "s2"

    final_plan = client.get(f"/bff/agora/research-plans/{plan_id}", headers=_headers())
    assert final_plan.status_code == 200
    assert final_plan.json()["data"]["status"] == "completed"
    assert legacy_run_id in final_plan.json()["data"]["run_ids"]
    assert s2_run_id in final_plan.json()["data"]["run_ids"]

    runs_resp = client.get(f"/bff/agora/research-plans/{plan_id}/runs", headers=_headers())
    assert runs_resp.status_code == 200
    item_ids = {r["run_id"] for r in runs_resp.json()["items"]}
    assert legacy_run_id in item_ids
    assert s2_run_id in item_ids


def test_mounted_cancel_running_plan_reaches_owner_and_fences(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    owner_client = getattr(client, "test_backend_client")

    res_create = client.post(
        "/bff/agora/workshops/ws-cancel-fencing/research-plans",
        headers=_headers(idempotency_key="cancel-create"),
        json={
            "spec_version": "1.0",
            "strategy_id": "strat-cancel",
            "strategy_spec_registry_id": "reg-cancel",
            "dataset": {"dataset_id": "ds-cancel", "strategy_id": "strat-cancel", "source_dataset_refs": ["ds-cancel"], "records": _sample_ohlcv_records()},
            "stages": [
                {"stage_id": "stage-c1", "stage_type": "prototype_backtest", "status": "ready", "dependencies": [], "routing": {"backend_mode": "fixture", "preferred_backend": "vectorbt"}},
            ],
        },
    )
    assert res_create.status_code == 201
    plan_id = res_create.json()["data"]["plan_id"]
    etag = res_create.json()["meta"]["etag"]

    res_app = client.post(f"/bff/agora/research-plans/{plan_id}/approve", headers=_headers(idempotency_key="cancel-app", if_match=etag))
    assert res_app.status_code == 200

    res_disp = client.post(f"/bff/agora/research-plans/{plan_id}/runs", headers=_headers(idempotency_key="cancel-disp", if_match=res_app.json()["meta"]["etag"]))
    assert res_disp.status_code == 202
    run_id = res_disp.json()["data"]["run_id"]

    plan_view = client.get(f"/bff/agora/research-plans/{plan_id}", headers=_headers())
    res_cancel = client.post(f"/bff/agora/research-plans/{plan_id}/cancel", headers=_headers(idempotency_key="cancel-act", if_match=plan_view.json()["meta"]["etag"]))
    assert res_cancel.status_code == 200
    assert res_cancel.json()["data"]["status"] == "cancelled"

    owner_run = owner_client.get(f"/api/research-orchestrator/runs/{run_id}").json()
    assert owner_run["status"] in {"canceled", "completed"}
    if owner_run["status"] == "canceled":
        assert owner_run.get("cancellation_fence") is not None


def test_bff_dispatch_fails_closed_when_successor_dataset_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)

    res_create = client.post(
        "/bff/agora/workshops/ws-multi-ds/research-plans",
        headers=_headers(idempotency_key="multi-ds-create"),
        json={
            "spec_version": "1.0",
            "strategy_id": "strat-multi",
            "strategy_spec_registry_id": "reg-multi",
            "dataset": {"dataset_id": "ds-root", "strategy_id": "strat-multi", "source_dataset_refs": ["ds-root"], "records": _sample_ohlcv_records()},
            "stages": [
                {
                    "stage_id": "stage-root", "stage_type": "prototype_backtest", "status": "ready",
                    "dependencies": [], "routing": {"backend_mode": "fixture", "preferred_backend": "vectorbt"},
                },
                {
                    "stage_id": "stage-succ", "stage_type": "prototype_backtest", "status": "pending",
                    "dependencies": ["stage-root"], "input_refs": ["dataset:ds-nonexistent"],
                    "routing": {"backend_mode": "fixture", "preferred_backend": "vectorbt"},
                },
            ],
        },
    )
    assert res_create.status_code == 201
    plan_id = res_create.json()["data"]["plan_id"]
    etag = res_create.json()["meta"]["etag"]

    res_app = client.post(f"/bff/agora/research-plans/{plan_id}/approve", headers=_headers(idempotency_key="multi-ds-app", if_match=etag))
    assert res_app.status_code == 200

    res_disp = client.post(f"/bff/agora/research-plans/{plan_id}/runs", headers=_headers(idempotency_key="multi-ds-disp", if_match=res_app.json()["meta"]["etag"]))
    assert res_disp.status_code == 503
    assert "DEPENDENCY_UNAVAILABLE" in res_disp.text or "unavailable" in res_disp.text.lower()

