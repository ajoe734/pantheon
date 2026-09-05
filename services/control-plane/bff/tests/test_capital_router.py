"""Contract tests for the standalone Capital Allocation router."""
from __future__ import annotations

import os
import sys
from copy import deepcopy
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient


from services.control_plane.bff.capital.router import create_capital_router


TASK_REVIEW_MANIFEST = {
    "task_id": "OPGAP-BE-CAPITAL-ROUTER-V2-20260830",
    "owned_layer": "standalone Capital Allocation router and service",
    "not_changing": "main.py composition and existing persona-capital port behavior",
    "review_scope": {
        "route_count": 25,
        "durable_readback": "Capital pool id, normalized risk limits, and allocation digest",
        "write_boundary": "Capital Allocation Manager only; missing owner mutation methods return 503",
    },
    "verification": [
        "pytest -q services/control-plane/bff/tests/test_capital_router.py",
        "python3 -m py_compile services/control-plane/bff/capital/service.py services/control-plane/bff/capital/router.py",
    ],
}


class _CapitalStore:
    """Small Capital Allocation Manager fake with durable-looking readbacks."""

    def __init__(self) -> None:
        self.pools: Dict[str, Dict[str, Any]] = {
            "pool-paper": {
                "id": "pool-paper",
                "name": "Paper Allocation",
                "status": "active",
                "risk_policy_ref": "risk-paper-v1",
                "risk_limits": {"max_gross_exposure": 0.35, "max_drawdown": 0.05},
            },
            "pool-paused": {
                "id": "pool-paused",
                "name": "Paused Allocation",
                "status": "paused",
                "risk_policy_ref": "risk-paused-v1",
                "risk_limit": {"max_gross_exposure": 0.10},
            },
        }
        self.rebalances: Dict[str, Dict[str, Any]] = {
            "rebalance-1": {
                "id": "rebalance-1",
                "capital_pool_id": "pool-paper",
                "status": "proposed",
                "direction": "increase",
                "lines": [{"strategy_id": "alpha", "target_weight": 0.20}],
            }
        }
        self.allocation_rows: List[Dict[str, Any]] = [
            {"capital_pool_id": "pool-paper", "strategy_id": "alpha", "target_weight": 0.20, "commission": 12.5},
            {"capital_pool_id": "pool-paper", "strategy_id": "beta", "target_weight": 0.15, "commission": 7.5},
        ]

    def list_capital_pools(self, **_: Any) -> List[Dict[str, Any]]:
        return list(self.pools.values())

    def get_capital_pool(self, pool_id: str) -> Optional[Dict[str, Any]]:
        return self.pools.get(pool_id)

    def list_capital_allocations(self, capital_pool_id: Optional[str] = None, **_: Any) -> List[Dict[str, Any]]:
        return [
            row for row in self.allocation_rows
            if not capital_pool_id or row["capital_pool_id"] == capital_pool_id
        ]

    def list_rebalances(self, **_: Any) -> List[Dict[str, Any]]:
        return list(self.rebalances.values())

    def get_rebalance(self, requested_id: str) -> Optional[Dict[str, Any]]:
        return self.rebalances.get(requested_id)

    def create_capital_pool(self, payload: Dict[str, Any], **_: Any) -> Dict[str, Any]:
        pool_id = str(payload.get("id") or "pool-created")
        item = {"id": pool_id, "status": "active", **deepcopy(payload)}
        self.pools[pool_id] = item
        return item

    def patch_capital_pool(self, payload: Dict[str, Any], pool_id: str, **_: Any) -> Dict[str, Any]:
        self.pools[pool_id].update(deepcopy(payload))
        return self.pools[pool_id]

    def capital_pool_action(self, payload: Dict[str, Any], pool_id: str, **_: Any) -> Dict[str, Any]:
        return {"pool_id": pool_id, "action_id": payload["action_id"], "status": "accepted"}

    def create_rebalance(self, payload: Dict[str, Any], **_: Any) -> Dict[str, Any]:
        item = {"id": str(payload.get("id") or "rebalance-created"), "status": "proposed", **deepcopy(payload)}
        self.rebalances[item["id"]] = item
        return item

    def approve_rebalance(self, payload: Dict[str, Any], rebalance_id: str, **_: Any) -> Dict[str, Any]:
        self.rebalances[rebalance_id]["status"] = "approved"
        return {"rebalance_id": rebalance_id, "decision": "approved", **deepcopy(payload)}

    def sign_rebalance(self, payload: Dict[str, Any], rebalance_id: str, **_: Any) -> Dict[str, Any]:
        return {"rebalance_id": rebalance_id, "signature": "recorded", **deepcopy(payload)}

    def apply_rebalance(self, payload: Dict[str, Any], rebalance_id: str, **_: Any) -> Dict[str, Any]:
        self.rebalances[rebalance_id]["status"] = "applied"
        return {"rebalance_id": rebalance_id, "state": "applied", **deepcopy(payload)}

    def rebalance_action(self, payload: Dict[str, Any], rebalance_id: str, **_: Any) -> Dict[str, Any]:
        return {"rebalance_id": rebalance_id, "action_id": payload["action_id"], "status": "accepted"}

    def patch_rebalance(self, payload: Dict[str, Any], rebalance_id: str, **_: Any) -> Dict[str, Any]:
        self.rebalances[rebalance_id].update(deepcopy(payload))
        return self.rebalances[rebalance_id]


def _client(store: _CapitalStore) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_capital_router(
            get_read_store=lambda: store,
            get_capital_authority=lambda: store,
            utc_now=lambda: "2026-08-30T21:00:00Z",
        )
    )
    return TestClient(app)


def test_capital_router_registers_the_25_catalogued_routes() -> None:
    router = create_capital_router()
    routes = {(method, route.path) for route in router.routes for method in route.methods}
    expected = {
        ("GET", "/api/v1/capital-pools"),
        ("GET", "/api/v1/capital-pools/{pool_id}"),
        ("GET", "/bff/capital-pools"),
        ("POST", "/bff/capital-pools"),
        ("GET", "/bff/capital-pools/{pool_id}"),
        ("PATCH", "/bff/capital-pools/{pool_id}"),
        ("POST", "/bff/capital-pools/{pool_id}/actions/{action_id}"),
        ("POST", "/bff/management/allocation-policy/evaluate"),
        ("POST", "/bff/rebalances/{rebalance_id}/approve"),
        ("POST", "/bff/rebalances/{rebalance_id}/two-man-sign"),
        ("GET", "/bff/rebalances"),
        ("POST", "/bff/rebalances"),
        ("POST", "/bff/rebalances/{rebalance_id}/apply"),
        ("GET", "/bff/rebalances/{rebalance_id}"),
        ("POST", "/bff/rebalances/{rebalance_id}/actions/{action_id}"),
        ("GET", "/bff/management/strategy-allocation"),
        ("GET", "/bff/management/capital-flow"),
        ("GET", "/bff/management/portfolio-book"),
        ("GET", "/bff/management/portfolio-book/pools"),
        ("GET", "/bff/management/portfolio-book/exposure"),
        ("GET", "/bff/management/portfolio-book/holdings"),
        ("GET", "/bff/management/portfolio-book/positions"),
        ("GET", "/bff/management/cost-attribution"),
        ("GET", "/bff/management/board-pack"),
        ("PATCH", "/bff/rebalances/{rebalance_id}"),
    }
    assert routes == expected
    assert len(router.routes) == 25
    assert TASK_REVIEW_MANIFEST["review_scope"]["route_count"] == len(router.routes)


def test_pool_read_surfaces_filter_normalize_risk_limits_and_return_404() -> None:
    client = _client(_CapitalStore())

    response = client.get("/bff/capital-pools?status=active&risk_policy_ref=risk-paper-v1")
    assert response.status_code == 200
    body = response.json()
    assert [item["capital_pool_id"] for item in body["items"]] == ["pool-paper"]
    assert body["data"][0]["risk_limits"]["max_gross_exposure"] == 0.35
    assert body["meta"]["surfaces"]["capital_pools"]["status"] == "ok"

    response = client.get("/api/v1/capital-pools/pool-paused")
    assert response.status_code == 200
    assert response.json()["data"]["risk_limits"] == {"max_gross_exposure": 0.10}

    response = client.get("/bff/capital-pools/missing")
    assert response.status_code == 404


def test_allocation_and_management_readbacks_retain_pool_and_risk_lineage() -> None:
    client = _client(_CapitalStore())

    evaluation = client.post(
        "/bff/management/allocation-policy/evaluate",
        json={"allocation_policy_version": "risk-paper-v1", "capital_pool_id": "pool-paper"},
    )
    assert evaluation.status_code == 200
    evaluation_data = evaluation.json()["data"]
    assert evaluation_data["allocation_policy_version"] == "risk-paper-v1"
    assert len(evaluation_data["lines"]) == 2
    assert all(line["allocation_line_digest"] for line in evaluation_data["lines"])

    strategy = client.get("/bff/management/strategy-allocation?capital_pool_id=pool-paper")
    assert strategy.status_code == 200
    assert strategy.json()["items"][0]["risk_limits"]["max_drawdown"] == 0.05

    exposure = client.get("/bff/management/portfolio-book/exposure")
    assert exposure.status_code == 200
    assert exposure.json()["items"][0]["allocation_digest"]

    holdings = client.get("/bff/management/portfolio-book/holdings?capital_pool_id=pool-paper")
    assert holdings.status_code == 200
    assert {row["strategy_id"] for row in holdings.json()["items"]} == {"alpha", "beta"}

    costs = client.get("/bff/management/cost-attribution")
    assert costs.status_code == 200
    assert sum(row["cost"] for row in costs.json()["items"]) == 20.0

    board_pack = client.get("/bff/management/board-pack")
    assert board_pack.status_code == 200
    assert board_pack.json()["data"]["capital"]["pools"] == 2


def test_capital_and_rebalance_writes_are_owner_delegated_and_idempotent() -> None:
    store = _CapitalStore()
    client = _client(store)

    created = client.post(
        "/bff/capital-pools",
        json={"id": "pool-created", "name": "Created Pool", "risk_limits": {"max_gross_exposure": 0.25}},
        headers={"Idempotency-Key": "pool-create-1"},
    )
    assert created.status_code == 201
    assert created.json()["meta"]["replayed"] is False

    replay = client.post(
        "/bff/capital-pools",
        json={"id": "pool-created", "name": "Created Pool", "risk_limits": {"max_gross_exposure": 0.25}},
        headers={"Idempotency-Key": "pool-create-1"},
    )
    assert replay.status_code == 201
    assert replay.json()["meta"]["replayed"] is True

    mismatch = client.post(
        "/bff/capital-pools",
        json={"id": "pool-created", "name": "Changed Request"},
        headers={"Idempotency-Key": "pool-create-1"},
    )
    assert mismatch.status_code == 409

    patched = client.patch(
        "/bff/capital-pools/pool-created",
        json={"risk_limits": {"max_gross_exposure": 0.20}},
        headers={"Idempotency-Key": "pool-patch-1"},
    )
    assert patched.status_code == 200
    assert store.pools["pool-created"]["risk_limits"]["max_gross_exposure"] == 0.20

    pool_action = client.post(
        "/bff/capital-pools/pool-created/actions/ApprovePool",
        json={}, headers={"Idempotency-Key": "pool-action-1"},
    )
    assert pool_action.status_code == 202
    assert pool_action.json()["data"]["action_id"] == "ApprovePool"

    created_rebalance = client.post(
        "/bff/rebalances",
        json={"id": "rebalance-created", "capital_pool_id": "pool-created", "lines": []},
        headers={"Idempotency-Key": "rebalance-create-1"},
    )
    assert created_rebalance.status_code == 201

    approved = client.post(
        "/bff/rebalances/rebalance-created/approve",
        json={"memo": "approved"}, headers={"Idempotency-Key": "rebalance-approve-1"},
    )
    assert approved.status_code == 201
    assert store.rebalances["rebalance-created"]["status"] == "approved"

    signed = client.post(
        "/bff/rebalances/rebalance-created/two-man-sign",
        json={"signature": "reviewer-2"}, headers={"Idempotency-Key": "rebalance-sign-1"},
    )
    assert signed.status_code == 202

    applied = client.post(
        "/bff/rebalances/rebalance-created/apply",
        json={}, headers={"Idempotency-Key": "rebalance-apply-1"},
    )
    assert applied.status_code == 202
    assert store.rebalances["rebalance-created"]["status"] == "applied"

    action = client.post(
        "/bff/rebalances/rebalance-created/actions/contain",
        json={}, headers={"Idempotency-Key": "rebalance-action-1"},
    )
    assert action.status_code == 202

    patched_rebalance = client.patch(
        "/bff/rebalances/rebalance-created",
        json={"status": "paused"}, headers={"Idempotency-Key": "rebalance-patch-1"},
    )
    assert patched_rebalance.status_code == 200
    assert store.rebalances["rebalance-created"]["status"] == "paused"


def test_capital_writes_fail_closed_without_an_owner_mutation_method() -> None:
    store = _CapitalStore()
    app = FastAPI()
    app.include_router(create_capital_router(get_read_store=lambda: store, get_capital_authority=lambda: object()))
    client = TestClient(app)

    response = client.post(
        "/bff/capital-pools",
        json={"name": "No Authority Pool"},
        headers={"Idempotency-Key": "no-owner-1"},
    )
    assert response.status_code == 503
