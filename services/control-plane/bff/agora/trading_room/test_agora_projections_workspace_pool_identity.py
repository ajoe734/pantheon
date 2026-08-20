"""Comprehensive tests for Agora strategy/version workspace & candidate pool resolution, and lens view recipe isolation."""
from __future__ import annotations

import os
from typing import Any, Dict
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from .router import create_trading_room_router
from .store import TradingRoomStore
from ..research.router import create_research_router
from ..research.store import MemoryResearchPlanStore as ResearchPlanStore


def _utc_now() -> str:
    return "2026-08-20T12:00:00Z"


def _extract_identity(auth_header: str = None, session_cookie: str = None) -> Any:
    class DummyIdentity:
        user_id = "operator-001"
        tenant_id = "tenant-agora"
        operator_id = "operator-001"
        roles = ["operator", "trader"]
        claims = {
            "tenant_id": "tenant-agora",
            "user_id": "operator-001",
            "operator_id": "operator-001",
            "allowed_tenants": ["tenant-agora", "*"],
        }
        def __getitem__(self, key):
            return getattr(self, key, None)
        def get(self, key, default=None):
            return getattr(self, key, default)
    return DummyIdentity()


def _dummy_check(identity: Any) -> None:
    pass


def _dummy_bff_error(status_code: int, error_code: Any, message: str, detail: Any = None, **kwargs):
    from fastapi import HTTPException
    code_val = getattr(error_code, "value", str(error_code))
    return HTTPException(
        status_code=status_code,
        detail={"error_code": code_val, "code": code_val, "message": message, "detail": detail, **kwargs},
    )


@pytest.fixture
def app_and_stores(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    tr_store = TradingRoomStore()
    res_store = ResearchPlanStore()
    app = FastAPI()

    kw = {
        "extract_identity": _extract_identity,
        "require_read_role": _dummy_check,
        "require_write_role": _dummy_check,
        "bff_error": _dummy_bff_error,
        "utc_now": _utc_now,
    }

    tr_router = create_trading_room_router(trading_room_store=tr_store, **kw)
    res_router = create_research_router(research_plan_store=res_store, **kw)

    app.include_router(tr_router)
    app.include_router(res_router)

    client = TestClient(app)
    return client, tr_store, res_store


def test_strategy_workspace_lookup_by_strategy_and_version(app_and_stores):
    client, tr_store, _ = app_and_stores
    tenant_id = "tenant-agora"
    user_id = "operator-001"
    strategy_id = "strat-winner-001"
    strategy_version = "v1"

    # Initially no workspace exists -> 404
    resp_404 = client.get(f"/bff/agora/trading-room/strategies/{strategy_id}/workspace?version={strategy_version}")
    assert resp_404.status_code == 404
    assert "No workspace found" in resp_404.json()["detail"]["message"]

    # Lookup route also returns 404 when missing
    resp_lookup_404 = client.get(f"/bff/agora/trading-room/workspaces/lookup?strategy_id={strategy_id}&strategy_version={strategy_version}")
    assert resp_lookup_404.status_code == 404

    # Create workspace in store
    workspace_data = {
        "id": "ws-winner-001",
        "userId": user_id,
        "strategyId": strategy_id,
        "strategyVersion": strategy_version,
        "dashboardVersion": 1,
        "activeViewId": "view-overview",
        "views": [{"id": "view-overview", "name": "Overview", "widgets": []}],
        "status": "active",
        "createdAt": "2026-08-20T11:00:00Z",
        "updatedAt": "2026-08-20T11:00:00Z",
    }
    tr_store.upsert_workspace(workspace_data, tenant_id=tenant_id, user_id=user_id)

    # Now get workspace by strategy
    resp = client.get(f"/bff/agora/trading-room/strategies/{strategy_id}/workspace?version={strategy_version}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["id"] == "ws-winner-001"
    assert body["data"]["strategyId"] == strategy_id
    assert body["meta"]["strategy_id"] == strategy_id

    # Test alias route /bff/agora/strategies/{strategy_id}/trading-room/workspace
    alias_resp = client.get(f"/bff/agora/strategies/{strategy_id}/trading-room/workspace?version={strategy_version}")
    assert alias_resp.status_code == 200
    assert alias_resp.json()["data"]["id"] == "ws-winner-001"

    # Test lookup endpoint
    lookup_resp = client.get(f"/bff/agora/trading-room/workspaces/lookup?strategy_id={strategy_id}&strategy_version={strategy_version}")
    assert lookup_resp.status_code == 200
    assert lookup_resp.json()["data"]["id"] == "ws-winner-001"


def test_candidate_pool_lookup_resolves_strategy_pool(app_and_stores):
    client, _, res_store = app_and_stores
    tenant_id = "tenant-agora"
    user_id = "operator-001"
    strategy_id = "strat-momentum-042"
    strategy_version = "v2"

    # Create candidate pool for strategy
    pool = {
        "spec_version": "1.0",
        "pool_id": "cpool-strat-42-snap1",
        "operator_id": user_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "filter": {"strategy_families": ["momentum"]},
        "candidates": [
            {
                "artifact_id": "cand-art-1",
                "strategy_id": strategy_id,
                "strategy_version": strategy_version,
                "strategy_ref": f"strategy:{strategy_id}:{strategy_version}",
                "symbol": "2330.TW",
                "lifecycle_state": "discovered",
            }
        ],
        "total": 1,
        "snapshot_at": "2026-08-20T10:00:00Z",
        "lock_version": 1,
        "metadata": {
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "strategy_ref": f"strategy:{strategy_id}:{strategy_version}",
            "recipe_id": "recipe-winner-branch",
        },
    }
    res_store.create_candidate_pool(pool)

    # Query via lookup endpoint
    lookup_resp = client.get(f"/bff/agora/candidate-pools/lookup?strategy_id={strategy_id}&strategy_version={strategy_version}")
    assert lookup_resp.status_code == 200
    data = lookup_resp.json()["data"]
    assert data["pool_id"] == "cpool-strat-42-snap1"
    assert data["metadata"]["strategy_id"] == strategy_id

    # Query via strategy candidate pool endpoint
    strat_pool_resp = client.get(f"/bff/agora/strategies/{strategy_id}/candidate-pool?version={strategy_version}")
    assert strat_pool_resp.status_code == 200
    assert strat_pool_resp.json()["data"]["pool_id"] == "cpool-strat-42-snap1"


def test_lens_is_view_recipe_not_candidate_pool_id(app_and_stores):
    client, _, _ = app_and_stores

    # Negative test: lens IDs like lens-A, lens-B, lens-overview are view recipes/layout identifiers, NOT pool IDs
    # Calling candidate pool endpoint with lens-A must fail closed (404) rather than fabricating or misinterpreting
    lens_resp = client.get("/bff/agora/candidate-pools/lens-A")
    assert lens_resp.status_code == 404
    assert "not found" in lens_resp.json()["detail"]["message"].lower()

    lens_b_resp = client.get("/bff/agora/candidate-pools/lens-B")
    assert lens_b_resp.status_code == 404
