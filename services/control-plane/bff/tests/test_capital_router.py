"""Contract tests for the standalone Capital Allocation router."""
from __future__ import annotations

import json
import os
import sys
from copy import deepcopy
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient


from capital.router import create_capital_router


TASK_REVIEW_MANIFEST = {
    "task_id": "OPGAP-BE-CAPITAL-ROUTER-V2-20260830",
    "owned_layer": "standalone Capital Allocation router and service",
    "not_changing": "main.py composition and existing persona-capital port behavior",
    "review_scope": {
        "route_count": 23,
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
        self.calls: List[Any] = []
        self.allocation_rows: List[Dict[str, Any]] = [
            {"capital_pool_id": "pool-paper", "strategy_id": "alpha", "target_weight": 0.20, "commission": 12.5},
            {"capital_pool_id": "pool-paper", "strategy_id": "beta", "target_weight": 0.15, "commission": 7.5},
        ]
        self.bindings: List[Dict[str, Any]] = [
            {"id": "binding-alpha", "capital_pool_id": "pool-paper", "strategy_id": "alpha"},
            {"id": "binding-beta", "capital_pool_id": "pool-paper", "strategy_id": "beta"},
        ]
        self.deployment_plans: List[Dict[str, Any]] = [
            {"id": "plan-alpha", "capital_pool_id": "pool-paper", "strategy_id": "alpha", "binding_ids": ["binding-alpha"]},
            {"id": "plan-beta", "capital_pool_id": "pool-paper", "strategy_id": "beta", "binding_ids": ["binding-beta"]},
        ]
        self.runtime_bindings: List[Dict[str, Any]] = [
            {"id": "rb-alpha", "runtime_id": "runtime-alpha", "capital_pool_id": "pool-paper", "strategy_id": "alpha", "plan_id": "plan-alpha"},
            {"id": "rb-beta", "runtime_id": "runtime-beta", "capital_pool_id": "pool-paper", "strategy_id": "beta", "plan_id": "plan-beta"},
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

    def list_bindings(self, **_: Any) -> List[Dict[str, Any]]:
        return list(self.bindings)

    def list_deployment_plans(self, **_: Any) -> List[Dict[str, Any]]:
        return list(self.deployment_plans)

    def list_runtime_bindings(self, **_: Any) -> List[Dict[str, Any]]:
        return list(self.runtime_bindings)

    def list_rebalances(self, **_: Any) -> List[Dict[str, Any]]:
        return list(self.rebalances.values())

    def get_rebalance(self, requested_id: str) -> Optional[Dict[str, Any]]:
        return self.rebalances.get(requested_id)

    # Owner writer interface (CapitalOwnerWriter): every call receives the caller context.
    def create_pool(self, payload: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        self.calls.append(("create_pool", ctx))
        pool_id = str(payload.get("id") or "pool-created")
        item = {"id": pool_id, "status": "active", **deepcopy(payload)}
        self.pools[pool_id] = item
        return item

    def pool_action(self, payload: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        self.calls.append(("pool_action", ctx))
        return {"pool_id": ctx["target_id"], "action_id": payload["action_id"], "status": "paused"}

    def evaluate_allocation(self, payload: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        # Operator decision 2026-10-07: the Capital owner (not the BFF) evaluates against the ranking snapshot.
        self.calls.append(("evaluate_allocation", ctx))
        lines = [{**row, "allocation_line_digest": f"digest-{row['strategy_id']}"} for row in self.allocation_rows]
        return {"allocation_evaluation_id": "allocation-evaluation-owner", "lines": lines,
                "allocation_policy_version": payload["allocation_policy_version"]}

    def create_rebalance(self, payload: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        self.calls.append(("create_rebalance", ctx))
        item = {"id": str(payload.get("id") or "rebalance-created"), "status": "proposed", **deepcopy(payload)}
        self.rebalances[item["id"]] = item
        return item

    def apply_rebalance(self, payload: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
        self.calls.append(("apply_rebalance", ctx))
        self.rebalances[ctx["target_id"]]["status"] = "applied"
        return {"rebalance_id": ctx["target_id"], "state": "applied", **deepcopy(payload)}


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


def _client_with_auth(store: _CapitalStore, extract_identity: Any) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_capital_router(
            get_read_store=lambda: store,
            get_capital_authority=lambda: store,
            extract_identity=extract_identity,
            utc_now=lambda: "2026-08-30T21:00:00Z",
        )
    )
    return TestClient(app)


def test_capital_router_registers_the_23_owner_backed_routes() -> None:
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
    assert len(router.routes) == 23
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
    exposure_payload = exposure.json()
    assert set(exposure_payload) == {"data", "page_info", "meta"}
    assert set(exposure_payload["data"]) == {"id", "items", "summary"}
    assert "items" not in exposure_payload
    assert "summary" not in exposure_payload
    summary = exposure_payload["data"]["summary"]
    assert exposure_payload["data"]["id"] == "pm12-portfolio-book-exposure"
    assert summary["exposure_count"] == 2
    assert [item["pool_id"] for item in exposure_payload["data"]["items"]] == ["pool-paper", "pool-paused"]
    paper_exposure = exposure_payload["data"]["items"][0]
    assert paper_exposure["pool_id"] == "pool-paper"
    assert paper_exposure["capital_pool_id"] == "pool-paper"
    assert paper_exposure["name"] == "Paper Allocation"
    assert paper_exposure["status"] == "active"
    assert paper_exposure["current_exposure"] is None
    assert paper_exposure["risk_budget"] is None
    assert paper_exposure["available_budget"] is None
    assert paper_exposure["risk_budget_utilization"] is None
    assert paper_exposure["risk_state"] == "unknown"
    assert "allocation_digest" not in paper_exposure
    assert exposure_payload["meta"]["surfaces"]["portfolio_book_exposure"]["source"] == "bff_composed"
    assert exposure_payload["meta"]["surfaces"]["capital_pools"]["source"] == "canonical"
    assert exposure_payload["meta"]["policy"] == "read_only_portfolio_exposure"

    holdings = client.get("/bff/management/portfolio-book/holdings?capital_pool_id=pool-paper")
    assert holdings.status_code == 200
    holdings_payload = holdings.json()
    assert {row["strategy_id"] for row in holdings_payload["data"]["items"]} == {"alpha", "beta"}

    costs = client.get("/bff/management/cost-attribution")
    assert costs.status_code == 200
    assert sum(row["cost"] for row in costs.json()["items"]) == 20.0

    board_pack = client.get("/bff/management/board-pack")
    assert board_pack.status_code == 200
    assert board_pack.json()["data"]["capital"]["pools"] == 2


def test_capital_and_rebalance_writes_are_owner_delegated_and_idempotent() -> None:
    store = _CapitalStore()
    client = _client(store)
    headers = {"Authorization": "Bearer caller-jwt", "Idempotency-Key": "pool-create-1"}
    body = {"id": "pool-created", "name": "Created Pool", "risk_limits": {"max_gross_exposure": 0.25}}

    created = client.post("/bff/capital-pools", json=body, headers=headers)
    assert created.status_code == 201
    assert created.json()["meta"]["replayed"] is False
    name, ctx = store.calls[-1]
    assert (name, ctx["auth_token"], ctx["actor_role"], ctx["key"]) == ("create_pool", "Bearer caller-jwt", "operator", "pool-create-1")

    replay = client.post("/bff/capital-pools", json=body, headers=headers)
    assert replay.status_code == 201
    assert replay.json()["meta"]["replayed"] is True
    assert len(store.calls) == 1  # one owner effect

    mismatch = client.post("/bff/capital-pools", json={**body, "name": "Changed"}, headers=headers)
    assert mismatch.status_code == 409

    action = client.post(
        "/bff/capital-pools/pool-created/actions/pause", json={}, headers={"Idempotency-Key": "pool-action-1"},
    )
    assert action.status_code == 202
    assert store.calls[-1][1]["target_id"] == "pool-created"

    created_rebalance = client.post(
        "/bff/rebalances",
        json={"id": "rebalance-created", "capital_pool_id": "pool-created", "lines": []},
        headers={"Idempotency-Key": "rebalance-create-1"},
    )
    assert created_rebalance.status_code == 201

    # Missing confirmation token must be rejected with 428 without calling owner
    missing_confirm = client.post(
        "/bff/rebalances/rebalance-created/apply",
        json={},
        headers={"Idempotency-Key": "rebalance-apply-1"},
    )
    assert missing_confirm.status_code == 428
    assert "CONFIRM_TOKEN_MISSING" in missing_confirm.text

    applied = client.post(
        "/bff/rebalances/rebalance-created/apply",
        json={},
        headers={"Idempotency-Key": "rebalance-apply-1", "X-Confirm-Token": "ct-apply-1"},
    )
    assert applied.status_code == 202
    assert store.rebalances["rebalance-created"]["status"] == "applied"


def test_capital_idempotency_binds_target_and_tenant_preserving_conflicts_and_isolation() -> None:
    store = _CapitalStore()
    client = _client(store)

    # 1. Action on pool-paper
    r1 = client.post(
        "/bff/capital-pools/pool-paper/actions/pause",
        json={},
        headers={"Idempotency-Key": "idem-action-key"},
    )
    assert r1.status_code == 202
    assert r1.json().get("meta", {}).get("replayed") is False
    assert r1.json().get("data", {}).get("pool_id") == "pool-paper"

    # Replay on same target and same payload replays cleanly
    r1_replay = client.post(
        "/bff/capital-pools/pool-paper/actions/pause",
        json={},
        headers={"Idempotency-Key": "idem-action-key"},
    )
    assert r1_replay.status_code == 202
    assert r1_replay.json().get("meta", {}).get("replayed") is True

    # 2. Cross-target conflict: Action on pool-paused with the same idempotency key must conflict (409)
    # rather than silently returning the cached readback for pool-paper
    r2_conflict = client.post(
        "/bff/capital-pools/pool-paused/actions/pause",
        json={},
        headers={"Idempotency-Key": "idem-action-key"},
    )
    assert r2_conflict.status_code == 409
    body_conflict = r2_conflict.json()
    err = body_conflict.get("detail", {}).get("error") or body_conflict.get("error") or {}
    assert err.get("code") == "IDEMPOTENCY_CONFLICT"

    # 3. Cross-tenant isolation: different tenant identities with the same idempotency key are isolated
    class _TenantIdentity:
        def __init__(self, tenant_id: str):
            self.operator_id = f"operator-{tenant_id}"
            self.tenant_id = tenant_id
            self.roles = {"admin", "operator"}

    def _extract_tenant_identity(auth: Optional[str] = None):
        t = (auth or "tenant-a").replace("Bearer ", "")
        return _TenantIdentity(t)

    tenant_client = _client_with_auth(store, _extract_tenant_identity)
    res_ta = tenant_client.post(
        "/bff/capital-pools/pool-paper/actions/pause",
        json={},
        headers={"Idempotency-Key": "shared-key", "Authorization": "Bearer tenant-a"},
    )
    assert res_ta.status_code == 202
    assert res_ta.json().get("meta", {}).get("replayed") is False

    res_tb = tenant_client.post(
        "/bff/capital-pools/pool-paper/actions/pause",
        json={},
        headers={"Idempotency-Key": "shared-key", "Authorization": "Bearer tenant-b"},
    )
    assert res_tb.status_code == 202
    assert res_tb.json().get("meta", {}).get("replayed") is False


def test_operations_without_an_owner_endpoint_are_retired_not_simulated() -> None:
    store = _CapitalStore()
    client = _client(store)
    key = {"Idempotency-Key": "retired-1"}
    for method, path in (
        ("patch", "/bff/capital-pools/pool-paper"),
        ("post", "/bff/rebalances/rebalance-1/actions/contain"),
        ("patch", "/bff/rebalances/rebalance-1"),
    ):
        response = getattr(client, method)(path, json={"status": "paused"}, headers=key)
        assert response.status_code == 410, path
    assert store.calls == []
    for path in ("/bff/rebalances/rebalance-1/approve", "/bff/rebalances/rebalance-1/two-man-sign"):
        assert client.post(path, json={}, headers=key).status_code in {404, 405}


def test_owner_http_failures_keep_rejection_conflict_and_unavailability_distinct() -> None:
    import io
    import urllib.error

    class _Failing(_CapitalStore):
        failure: Exception

        def create_pool(self, payload: Dict[str, Any], **ctx: Any) -> Dict[str, Any]:
            raise self.failure

    def post(failure: Exception) -> int:
        store = _Failing()
        store.failure = failure
        response = _client(store).post("/bff/capital-pools", json={"id": "p", "name": "P"}, headers={"Idempotency-Key": "k"})
        return response.status_code

    def http(code: int) -> urllib.error.HTTPError:
        return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b'{"detail": "owner said no"}'))

    assert post(http(403)) == 403
    assert post(http(409)) == 409
    assert post(http(503)) == 503
    assert post(urllib.error.URLError("down")) == 503
    assert post(json.JSONDecodeError("truncated", "{", 1)) == 503
    assert post(RuntimeError("Capital authority returned a pool with mismatched create semantics")) == 502
    assert post(ValueError("pool_id is required")) == 422


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


def test_rest_mounted_tenant_resolution_coverage() -> None:
    store = _CapitalStore()

    class _Identity:
        def __init__(self, allowed_tenants, tenant_id=""):
            self.operator_id = "op-test"
            self.roles = {"operator", "admin"}
            self.claims = {"allowed_tenants": allowed_tenants, "tenant_id": tenant_id}

    # 1. Ambiguous caller with multiple allowed tenants
    ambiguous_client = _client_with_auth(store, lambda _: _Identity(["tenant-a", "tenant-b"]))
    endpoints = [
        ("post", "/bff/capital-pools", {"id": "pool-ambig", "name": "Pool Ambig"}, "pool-ambig-k"),
        ("post", "/bff/capital-pools/pool-paper/actions/pause", {}, "pool-action-ambig-k"),
        ("post", "/bff/rebalances", {"capital_pool_id": "pool-paper", "allocations": []}, "rebalance-ambig-k"),
        ("post", "/bff/rebalances/rebalance-1/apply", {}, "rebalance-apply-ambig-k"),
    ]

    # Without X-Tenant-Id -> 400 for all write endpoints
    for method, path, payload, key in endpoints:
        headers = {"Idempotency-Key": key, "X-Confirm-Token": "confirm-valid"}
        resp = getattr(ambiguous_client, method)(path, json=payload, headers=headers)
        assert resp.status_code == 400, f"Expected 400 without X-Tenant-Id on {path}, got {resp.status_code}"

    # With forbidden X-Tenant-Id -> 403 for all write endpoints
    for method, path, payload, key in endpoints:
        headers = {"Idempotency-Key": f"{key}-forbidden", "X-Confirm-Token": "confirm-valid", "X-Tenant-Id": "tenant-forbidden"}
        resp = getattr(ambiguous_client, method)(path, json=payload, headers=headers)
        assert resp.status_code == 403, f"Expected 403 on {path}, got {resp.status_code}"

    # With allowed explicit X-Tenant-Id: tenant-b -> succeeds and passes tenant_id to store
    for method, path, payload, key in endpoints:
        headers = {"Idempotency-Key": f"{key}-b", "X-Confirm-Token": "confirm-valid", "X-Tenant-Id": "tenant-b"}
        resp = getattr(ambiguous_client, method)(path, json=payload, headers=headers)
        assert resp.status_code in {201, 202}, f"Expected success on {path}, got {resp.status_code}: {resp.text}"
        _, ctx = store.calls[-1]
        assert ctx.get("tenant_id") == "tenant-b"

    # 2. Wildcard caller
    wildcard_client = _client_with_auth(store, lambda _: _Identity(["*"]))
    # Without X-Tenant-Id -> 400
    resp = wildcard_client.post("/bff/capital-pools", json={"id": "p-wild", "name": "Wild"}, headers={"Idempotency-Key": "w-1"})
    assert resp.status_code == 400
    # With X-Tenant-Id: * -> 400
    resp = wildcard_client.post("/bff/capital-pools", json={"id": "p-wild", "name": "Wild"}, headers={"Idempotency-Key": "w-2", "X-Tenant-Id": "*"})
    assert resp.status_code == 400
    # With concrete X-Tenant-Id: any-tenant -> 201
    resp = wildcard_client.post("/bff/capital-pools", json={"id": "p-wild", "name": "Wild"}, headers={"Idempotency-Key": "w-3", "X-Tenant-Id": "any-tenant"})
    assert resp.status_code == 201
    assert store.calls[-1][1].get("tenant_id") == "any-tenant"

    # 3. Unambiguous single tenant caller
    single_client = _client_with_auth(store, lambda _: _Identity(["tenant-single"]))
    resp = single_client.post("/bff/capital-pools", json={"id": "p-single", "name": "Single"}, headers={"Idempotency-Key": "s-1"})
    assert resp.status_code == 201
    assert store.calls[-1][1].get("tenant_id") == "tenant-single"



class _SourceReportingStore(_CapitalStore):
    """Capital store whose dataset_source is the only availability signal it owns."""

    def __init__(self, source: str, *, empty: bool = False) -> None:
        super().__init__()
        self.source = source
        if empty:
            self.pools, self.rebalances, self.allocation_rows = {}, {}, []

    def dataset_source(self, dataset: str) -> str:
        return self.source


class _DelegatingStore:
    """Wrapper that overrides dataset_source and delegates everything else to an inner store."""

    def __init__(self, inner: Any, source: str) -> None:
        self._inner = inner
        self._source = source

    def dataset_source(self, dataset: str) -> str:
        return self._source

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _DisagreeingInner(_CapitalStore):
    def dataset_source(self, dataset: str) -> str:
        return "missing"

    def dataset_surface_status(self, dataset: str, **_: Any) -> Dict[str, Any]:
        return {"status": "unavailable", "source": "missing", "message": f"{dataset} inner says missing"}


def _unknown_id_statuses(store: Any) -> List[int]:
    client = _client(store)
    return [
        client.get("/bff/capital-pools/nope").status_code,
        client.get("/bff/rebalances/nope").status_code,
    ]


def test_unknown_id_status_follows_the_store_dataset_source() -> None:
    assert _unknown_id_statuses(_SourceReportingStore("local_snapshot")) == [404, 404]
    for source in ("missing", "unavailable"):
        assert _unknown_id_statuses(_SourceReportingStore(source)) == [503, 503]


def test_delegating_store_gets_the_answer_of_its_own_dataset_source() -> None:
    assert _unknown_id_statuses(_DelegatingStore(_DisagreeingInner(), "local_snapshot")) == [404, 404]
    assert _unknown_id_statuses(_DelegatingStore(_CapitalStore(), "missing")) == [503, 503]


def test_healthy_empty_capital_source_is_not_unavailable() -> None:
    client = _client(_SourceReportingStore("local_snapshot", empty=True))

    response = client.get("/bff/capital-pools")
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["meta"]["surfaces"]["capital_pools"]["status"] == "ok"
    assert _unknown_id_statuses(_SourceReportingStore("local_snapshot", empty=True)) == [404, 404]


def test_default_read_surface_ports_availability_matches_its_dataset_source() -> None:
    from ports.read_surface_ports import ReadSurfacePorts

    ports = ReadSurfacePorts()
    datasets = ("capital_pools", "rebalances", "persona_bindings", "capital_allocations")
    assert {ports.dataset_source(d) for d in datasets} == {"missing"}
    client = _client(ports)  # type: ignore[arg-type]
    listing = client.get("/bff/capital-pools")
    assert listing.json()["meta"]["surfaces"]["capital_pools"]["status"] == "unavailable"
    assert _unknown_id_statuses(ports) == [503, 503]
