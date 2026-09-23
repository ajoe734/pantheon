"""Production-composition regression coverage for extracted BFF domain seams.

Verifies that the remaining BFF production seams extracted out of main.py into
declared domain owners (capital, command_adapters, personas, incidents, governance,
performance attribution) behave correctly as mounted domain routers and pure domain
service logic, without requiring any direct or indirect import of main.py.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any, Dict, List, Optional
import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.agora.performance.service import (
    pm12_performance_attribution_response,
)
from services.control_plane.bff.capital.router import create_capital_router
from services.control_plane.bff.capital.service import (
    CapitalAuthorityUnavailable,
    CapitalService,
    _pm12_allocation_line_assertion_hash,
    _pm12_semantic_json_value,
    _pm12_semantic_values_match,
)
from services.control_plane.bff.command_adapters.router import (
    create_command_adapters_router,
)
from services.control_plane.bff.command_adapters.service import (
    CommandAdapterService,
    assert_duplicate_confirm_token_matches,
    stored_command_params,
)
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.governance.service import (
    human_inbox_surface_timeout_seconds,
)
from services.control_plane.bff.incidents.router import create_incident_router
from services.control_plane.bff.incidents.service import (
    IncidentService,
    _bff_incident_matches_filters,
    _project_bff_incident_case,
)
from services.control_plane.bff.management_read_models.ranking_router import (
    create_performance_attribution_router,
)
from services.control_plane.bff.models import (
    AuditContext,
    CommandStatus,
    CommandType,
    ObjectType,
    OperatorCommand,
    OperatorIdentity,
    TargetObject,
)
from services.control_plane.bff.personas.service import (
    _filter_by_common_identifiers,
)


# ---------------------------------------------------------------------------
# Test Fakes
# ---------------------------------------------------------------------------


class _FakeCapitalStore:
    """In-memory fake implementing Capital read and write surfaces."""

    def __init__(self, *, fail_writes: bool = False, tenant_id: Optional[str] = None) -> None:
        self.fail_writes = fail_writes
        self.tenant_id = tenant_id
        self.apply_rebalance_calls = 0
        self.pools: Dict[str, Dict[str, Any]] = {
            "pool-alpha": {
                "id": "pool-alpha",
                "name": "Alpha Fund",
                "tenant_id": "tenant-prime",
                "status": "active",
                "risk_limits": {"max_gross_exposure": 0.50},
            },
            "pool-beta": {
                "id": "pool-beta",
                "name": "Beta Fund",
                "tenant_id": "tenant-sec",
                "status": "active",
                "risk_limits": {"max_gross_exposure": 0.25},
            },
        }
        self.rebalances: Dict[str, Dict[str, Any]] = {
            "reb-1": {
                "id": "reb-1",
                "capital_pool_id": "pool-alpha",
                "status": "proposed",
                "lines": [{"strategy_id": "s-1", "target_weight": 0.50}],
            }
        }
        self.allocations: List[Dict[str, Any]] = [
            {"capital_pool_id": "pool-alpha", "strategy_id": "s-1", "target_weight": 0.50},
        ]

    def list_capital_pools(self, **_: Any) -> List[Dict[str, Any]]:
        pools = list(self.pools.values())
        if self.tenant_id:
            pools = [p for p in pools if p.get("tenant_id") == self.tenant_id]
        return pools

    def get_capital_pool(self, pool_id: str) -> Optional[Dict[str, Any]]:
        pool = self.pools.get(pool_id)
        if pool and self.tenant_id and pool.get("tenant_id") != self.tenant_id:
            return None
        return pool

    def list_capital_allocations(self, capital_pool_id: Optional[str] = None, **_: Any) -> List[Dict[str, Any]]:
        return [
            row for row in self.allocations
            if not capital_pool_id or row.get("capital_pool_id") == capital_pool_id
        ]

    def list_rebalances(self, **_: Any) -> List[Dict[str, Any]]:
        return list(self.rebalances.values())

    def get_rebalance(self, requested_id: str) -> Optional[Dict[str, Any]]:
        return self.rebalances.get(requested_id)

    def create_capital_pool(self, payload: Dict[str, Any], **_: Any) -> Dict[str, Any]:
        if self.fail_writes:
            raise RuntimeError("Underlying pool store write failure")
        item = {"id": str(payload.get("id") or "pool-new"), "status": "active", **deepcopy(payload)}
        self.pools[item["id"]] = item
        return item

    def patch_capital_pool(self, payload: Dict[str, Any], pool_id: str, **_: Any) -> Dict[str, Any]:
        if self.fail_writes:
            raise RuntimeError("Underlying pool patch failure")
        self.pools[pool_id].update(deepcopy(payload))
        return self.pools[pool_id]

    def apply_rebalance(self, payload: Dict[str, Any], rebalance_id: str, **_: Any) -> Dict[str, Any]:
        if self.fail_writes:
            raise RuntimeError("Underlying rebalance apply failure")
        self.apply_rebalance_calls += 1
        self.rebalances[rebalance_id]["status"] = "applied"
        return {"rebalance_id": rebalance_id, "state": "applied", **deepcopy(payload)}


class _FakePerformanceStore:
    """In-memory store providing performance attribution sources across tenants."""

    def __init__(self, *, status: str = "fresh") -> None:
        self.status = status
        self.runtime_bindings = [
            {
                "binding_id": "rb-1",
                "runtime_id": "rt-1",
                "tenant_id": "tenant-alpha",
                "capital_pool_id": "pool-1",
                "persona_id": "p-1",
                "strategy_id": "s-1",
                "deployment_stage": "paper",
                "status": "running",
            },
            {
                "binding_id": "rb-2",
                "runtime_id": "rt-2",
                "tenant_id": "tenant-beta",
                "capital_pool_id": "pool-2",
                "persona_id": "p-2",
                "strategy_id": "s-2",
                "deployment_stage": "paper",
                "status": "running",
            },
        ]
        self.telemetry = [
            {
                "runtime_id": "rt-1",
                "pnl": 500.0,
                "fill_rate": 0.95,
                "total_trades": 10,
                "notional": 10000.0,
                "worst_drawdown": 0.05,
                "slippage_bps": 1.2,
                "metrics": {
                    "total_pnl": 500.0,
                    "runtime_count": 1,
                    "holding_count": 2,
                    "total_notional": 10000.0,
                    "total_exposure": 10000.0,
                    "worst_drawdown": 0.05,
                    "average_fill_rate": 0.95,
                    "average_slippage_bps": 1.2,
                    "total_trades": 10,
                },
                "collected_at": "2026-09-23T00:00:00Z",
            },
            {
                "runtime_id": "rt-2",
                "pnl": 250.0,
                "fill_rate": 0.90,
                "total_trades": 5,
                "notional": 5000.0,
                "worst_drawdown": 0.02,
                "slippage_bps": 0.8,
                "metrics": {
                    "total_pnl": 250.0,
                    "runtime_count": 1,
                    "holding_count": 1,
                    "total_notional": 5000.0,
                    "total_exposure": 5000.0,
                    "worst_drawdown": 0.02,
                    "average_fill_rate": 0.90,
                    "average_slippage_bps": 0.8,
                    "total_trades": 5,
                },
                "collected_at": "2026-09-23T00:00:00Z",
            },
        ]
        self.personas = [
            {"id": "p-1", "persona_id": "p-1", "name": "Persona 1", "tenant_id": "tenant-alpha"},
            {"id": "p-2", "persona_id": "p-2", "name": "Persona 2", "tenant_id": "tenant-beta"},
        ]
        self.pools = [
            {"id": "pool-1", "pool_id": "pool-1", "name": "Pool 1", "tenant_id": "tenant-alpha"},
            {"id": "pool-2", "pool_id": "pool-2", "name": "Pool 2", "tenant_id": "tenant-beta"},
        ]
        self.strategies = [
            {"id": "s-1", "strategy_id": "s-1", "name": "Strategy 1", "tenant_id": "tenant-alpha"},
            {"id": "s-2", "strategy_id": "s-2", "name": "Strategy 2", "tenant_id": "tenant-beta"},
        ]

    def list_runtime_bindings(self, **_: Any) -> List[Dict[str, Any]]:
        return self.runtime_bindings

    def list_deployment_plans(self, **_: Any) -> List[Dict[str, Any]]:
        return []

    def list_bindings(self, **_: Any) -> List[Dict[str, Any]]:
        return []

    def list_capital_pools(self, **_: Any) -> List[Dict[str, Any]]:
        return self.pools

    def list_personas(self, **_: Any) -> List[Dict[str, Any]]:
        return self.personas

    def list_strategies(self, **_: Any) -> List[Dict[str, Any]]:
        return self.strategies

    def list_telemetry_summaries(self, **_: Any) -> List[Dict[str, Any]]:
        if self.status == "degraded":
            return []
        return self.telemetry

    def dataset_source(self, dataset: str) -> str:
        if self.status == "degraded" and dataset == "telemetry_summaries":
            return "degraded"
        return "bff_read_store"


class _UnavailableCapitalAuthority:
    """Authority that exposes no mutation methods, forcing fail-closed 503."""
    pass


class _FakeIncidentStore:
    """In-memory store providing incident records."""

    def __init__(self) -> None:
        self.incidents: List[Dict[str, Any]] = [
            {
                "id": "inc-001",
                "incident_id": "inc-001",
                "title": "Exposure breach in pool-alpha",
                "severity": "P1",
                "status": "active",
                "capital_pool_id": "pool-alpha",
                "tenant_id": "tenant-prime",
                "created_at": "2026-09-01T10:00:00Z",
            },
            {
                "id": "inc-002",
                "incident_id": "inc-002",
                "title": "Latency degradation in pool-beta",
                "severity": "P2",
                "status": "resolved",
                "capital_pool_id": "pool-beta",
                "tenant_id": "tenant-sec",
                "created_at": "2026-09-01T11:00:00Z",
            },
        ]

    def list_incidents(self, **_: Any) -> List[Dict[str, Any]]:
        return list(self.incidents)

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        for inc in self.incidents:
            if inc["id"] == incident_id or inc.get("incident_id") == incident_id:
                return inc
        return None


# ---------------------------------------------------------------------------
# Capital Allocation Seam Tests
# ---------------------------------------------------------------------------


class TestCapitalAllocationSemanticSeam:
    """Verifies PM12 semantic JSON comparison and line assertion hash in capital.service."""

    def test_semantic_canonicalization_primitives(self) -> None:
        assert _pm12_semantic_json_value(None) == ["null"]
        assert _pm12_semantic_json_value(True) == ["boolean", True]
        assert _pm12_semantic_json_value(False) == ["boolean", False]
        assert _pm12_semantic_json_value("foo") == ["string", "foo"]
        assert _pm12_semantic_json_value(1) == ["number", "1"]
        assert _pm12_semantic_json_value(1.0) == ["number", "1"]
        assert _pm12_semantic_json_value(Decimal("1.500")) == ["number", "1.5"]

    def test_semantic_canonicalization_collections(self) -> None:
        val1 = _pm12_semantic_json_value({"b": 2, "a": 1})
        val2 = _pm12_semantic_json_value({"a": 1.0, "b": 2.0})
        assert val1 == val2

    def test_semantic_values_match_tolerance_and_strictness(self) -> None:
        assert _pm12_semantic_values_match(1.0, 1)
        assert _pm12_semantic_values_match(Decimal("0.0"), 0)
        assert _pm12_semantic_values_match(
            {"weights": [1.0, 2.0], "label": "test"},
            {"label": "test", "weights": [1, 2]},
        )
        assert not _pm12_semantic_values_match(True, 1)
        assert not _pm12_semantic_values_match(False, 0)
        assert not _pm12_semantic_values_match("1", 1)
        assert not _pm12_semantic_values_match(float("nan"), 0)
        assert not _pm12_semantic_values_match(float("inf"), float("inf"))

    def test_allocation_line_assertion_hash_stability(self) -> None:
        line_a = {
            "persona_id": "p-1",
            "runtime_id": "rt-1",
            "strategy_id": "s-1",
            "capital_pool_id": "pool-1",
            "target_weight": 0.5,
            "target_notional": 1000.0,
            "cap_reasons": ["max_exposure"],
            "evidence_refs": ["ref-1"],
        }
        line_b = {
            "evidence_refs": ["ref-1"],
            "cap_reasons": ["max_exposure"],
            "target_notional": 1000,
            "target_weight": Decimal("0.5"),
            "capital_pool_id": "pool-1",
            "strategy_id": "s-1",
            "runtime_id": "rt-1",
            "persona_id": "p-1",
        }
        assert _pm12_allocation_line_assertion_hash(line_a) == _pm12_allocation_line_assertion_hash(line_b)


class TestCapitalMountedComposition:
    """Verifies mounted composition of create_capital_router for fresh, degraded, and fail-closed paths."""

    def test_capital_router_fresh_read_path(self) -> None:
        store = _FakeCapitalStore()
        app = FastAPI()
        app.include_router(
            create_capital_router(
                get_read_store=lambda: store,
                get_capital_authority=lambda: store,
                utc_now=lambda: "2026-09-23T00:00:00Z",
            )
        )
        client = TestClient(app)
        response = client.get("/bff/capital-pools")
        assert response.status_code == 200
        data = response.json()
        items = data.get("items") or data.get("data", {}).get("items") or []
        assert len(items) == 2
        pool_ids = {p["id"] for p in items}
        assert "pool-alpha" in pool_ids
        assert "pool-beta" in pool_ids

    def test_capital_router_fail_closed_503_without_owner_mutation(self) -> None:
        """When authority exposes no write method, router must fail closed with 503."""
        read_store = _FakeCapitalStore()
        app = FastAPI()
        app.include_router(
            create_capital_router(
                get_read_store=lambda: read_store,
                get_capital_authority=lambda: _UnavailableCapitalAuthority(),
                utc_now=lambda: "2026-09-23T00:00:00Z",
            )
        )
        client = TestClient(app)
        response = client.post(
            "/bff/capital-pools",
            json={"name": "Attacked Pool", "risk_limits": {"max_gross_exposure": 0.99}},
            headers={"X-Operator-ID": "op-admin", "X-Operator-Role": "admin", "Idempotency-Key": "no-owner-1"},
        )
        assert response.status_code == 503
        body = response.json()
        error = body.get("detail", {}).get("error") or body.get("error", {})
        assert error.get("code") in ("SERVICE_UNAVAILABLE", "DEPENDENCY_UNAVAILABLE")
        assert "mutation method" in str(error).lower()

    def test_capital_router_tenant_isolation(self) -> None:
        # Tenant Prime operator should only see Tenant Prime pools
        store_prime = _FakeCapitalStore(tenant_id="tenant-prime")
        app_prime = FastAPI()
        app_prime.include_router(
            create_capital_router(
                get_read_store=lambda: store_prime,
                get_capital_authority=lambda: store_prime,
                utc_now=lambda: "2026-09-23T00:00:00Z",
            )
        )
        client_prime = TestClient(app_prime)

        # 1. Tenant-prime listing sees pool-alpha, but NOT pool-beta
        res_list = client_prime.get("/bff/capital-pools")
        assert res_list.status_code == 200
        items = res_list.json().get("items") or res_list.json().get("data", {}).get("items") or []
        pool_ids = {p["id"] for p in items}
        assert "pool-alpha" in pool_ids
        assert "pool-beta" not in pool_ids

        # 2. Fetching pool-alpha succeeds for tenant-prime
        res_alpha = client_prime.get("/bff/capital-pools/pool-alpha")
        assert res_alpha.status_code == 200
        assert (res_alpha.json().get("id") == "pool-alpha" or res_alpha.json().get("data", {}).get("id") == "pool-alpha")

        # 3. Direct access to tenant-sec resource (pool-beta) by tenant-prime fails with 404
        res_beta_denied = client_prime.get("/bff/capital-pools/pool-beta")
        assert res_beta_denied.status_code == 404

        # 4. Inversely, Tenant Sec operator should only see pool-beta
        store_sec = _FakeCapitalStore(tenant_id="tenant-sec")
        app_sec = FastAPI()
        app_sec.include_router(
            create_capital_router(
                get_read_store=lambda: store_sec,
                get_capital_authority=lambda: store_sec,
                utc_now=lambda: "2026-09-23T00:00:00Z",
            )
        )
        client_sec = TestClient(app_sec)
        res_sec_list = client_sec.get("/bff/capital-pools")
        assert res_sec_list.status_code == 200
        sec_items = res_sec_list.json().get("items") or res_sec_list.json().get("data", {}).get("items") or []
        sec_pool_ids = {p["id"] for p in sec_items}
        assert "pool-beta" in sec_pool_ids
        assert "pool-alpha" not in sec_pool_ids
        assert client_sec.get("/bff/capital-pools/pool-alpha").status_code == 404

    def test_capital_router_idempotent_rebalance_apply(self) -> None:
        store = _FakeCapitalStore()
        app = FastAPI()
        app.include_router(
            create_capital_router(
                get_read_store=lambda: store,
                get_capital_authority=lambda: store,
                utc_now=lambda: "2026-09-23T00:00:00Z",
            )
        )
        client = TestClient(app)
        headers = {
            "Idempotency-Key": "idem-reb-apply-001",
            "X-Operator-ID": "op-admin",
            "X-Operator-Role": "admin",
        }
        # Initial request
        res1 = client.post("/bff/rebalances/reb-1/apply", json={"reason": "rebalance 1"}, headers=headers)
        assert res1.status_code == 202
        body1 = res1.json()
        assert body1.get("meta", {}).get("replayed") is False
        assert store.rebalances["reb-1"]["status"] == "applied"
        assert store.apply_rebalance_calls == 1

        # Replay with same key and identical payload: must replay cached result without second mutation
        res2 = client.post("/bff/rebalances/reb-1/apply", json={"reason": "rebalance 1"}, headers=headers)
        assert res2.status_code == 202
        body2 = res2.json()
        assert body2.get("meta", {}).get("replayed") is True
        assert store.apply_rebalance_calls == 1

        # Conflict request with same key but differing payload: must return 409 conflict
        res3 = client.post("/bff/rebalances/reb-1/apply", json={"reason": "conflicting modification"}, headers=headers)
        assert res3.status_code == 409
        assert store.apply_rebalance_calls == 1


# ---------------------------------------------------------------------------
# Command Adapters Seam Tests
# ---------------------------------------------------------------------------


class TestCommandAdaptersMountedComposition:
    """Verifies command adapters stored params and mounted router composition."""

    def test_stored_command_params_derivation(self) -> None:
        identity = OperatorIdentity(
            operator_id="op-123",
            tenant_id="tenant-alpha",
            roles={"reviewer", "operator"},
        )
        cmd = OperatorCommand(
            command=CommandType.APPROVE_DEPLOYMENT,
            target=TargetObject(type=ObjectType.DEPLOYMENT_PLAN, id="plan-1"),
            audit_context=AuditContext(reason="deployment review"),
            params={"target_artifact_id": "art-1"},
        )
        params = stored_command_params(cmd, identity)
        assert params["entity_id"] == "plan-1"
        assert params["actor_id"] == "op-123"
        assert params["actor_role"] == "reviewer"
        assert params["target_artifact_id"] == "art-1"

    def test_stored_command_params_drawer_passthrough(self) -> None:
        identity = OperatorIdentity(
            operator_id="op-admin",
            tenant_id="default",
            roles={"admin"},
        )
        cmd = OperatorCommand(
            command=CommandType.PAUSE_EXECUTION,
            target=TargetObject(type=ObjectType.RUNTIME, id="rt-1"),
            audit_context=AuditContext(reason="emergency stop"),
            params={"runtime_id": "rt-1", "reason": "drawer action"},
        )
        params = stored_command_params(cmd, identity)
        assert params["runtime_id"] == "rt-1"
        assert params["reason"] == "drawer action"

    def test_assert_duplicate_confirm_token_matches(self) -> None:
        cmd = OperatorCommand(
            command=CommandType.PAUSE_RUNTIME,
            target=TargetObject(type=ObjectType.RUNTIME, id="rt-1"),
            audit_context=AuditContext(reason="pause"),
        )
        duplicate_record = {
            "params": {"confirm_token_id": "token-xyz"},
        }
        assert_duplicate_confirm_token_matches(
            duplicate=duplicate_record,
            cmd=cmd,
            payload={},
            confirm_token="token-xyz",
            foundation_context={},
        )
        with pytest.raises(Exception) as exc_info:
            assert_duplicate_confirm_token_matches(
                duplicate=duplicate_record,
                cmd=cmd,
                payload={},
                confirm_token="token-mismatch",
                foundation_context={},
            )
        assert getattr(exc_info.value, "status_code", 409) == 409

    def test_command_adapters_router_fresh_confirmation_mounted(self, tmp_path) -> None:
        cmd_store = CommandStore(str(tmp_path / "cmds.jsonl"))
        headers = {
            "Authorization": "Bearer op-1:operator,admin:mfa",
            "Idempotency-Key": "ct-create-1",
        }
        app = FastAPI()
        app.include_router(
            create_command_adapters_router(
                command_store=cmd_store,
                extract_identity=lambda auth=None, **_: OperatorIdentity(
                    operator_id="op-1", roles=["operator", "admin"], auth_mode="bearer", has_mfa=True
                ),
                utc_now=lambda: "2026-09-23T00:00:00Z",
            )
        )
        client = TestClient(app)
        # 1. Create confirm token
        create_tok_res = client.post(
            "/bff/confirm-tokens",
            headers=headers,
            json={"tokenId": "tok-123", "reason": "test confirmation"},
        )
        assert create_tok_res.status_code == 201

        # 2. Confirm command with token
        payload = {
            "command_id": "cmd-1",
            "confirm_token": "tok-123",
        }
        conf_headers = {
            "Authorization": "Bearer op-1:operator,admin:mfa",
            "Idempotency-Key": "conf-cmd-1",
        }
        res = client.post("/bff/command-confirmations", json=payload, headers=conf_headers)
        assert res.status_code == 202
        data = res.json()
        assert data.get("status") == "accepted"
        assert data.get("lifecycleStatus") == "redeemed"


# ---------------------------------------------------------------------------
# Personas Common Identifiers Seam Tests
# ---------------------------------------------------------------------------


class TestPersonasCommonIdentifiersSeam:
    """Verifies common identifier filtering extracted to personas.service."""

    def test_filtering_by_identifiers_and_aliases(self) -> None:
        items = [
            {"persona_id": "p-1", "runtime_id": "rt-1", "strategy_id": "s-1", "capital_pool_id": "pool-1"},
            {"persona_id": "p-2", "runtime_id": "rt-2", "strategy_id": "s-1", "capital_pool_id": "pool-2"},
            {"persona_id": "p-1", "runtime_id": "rt-3", "strategy_id": "s-2", "capital_pool_id": "pool-1"},
        ]
        filtered_p1 = _filter_by_common_identifiers(items, persona_id="p-1")
        assert len(filtered_p1) == 2
        assert all(it["persona_id"] == "p-1" for it in filtered_p1)

        filtered_s1 = _filter_by_common_identifiers(items, strategy="s-1")
        assert len(filtered_s1) == 2
        assert all(it["strategy_id"] == "s-1" for it in filtered_s1)

        filtered_conj = _filter_by_common_identifiers(items, persona="p-1", strategy_id="s-2")
        assert len(filtered_conj) == 1
        assert filtered_conj[0]["runtime_id"] == "rt-3"


# ---------------------------------------------------------------------------
# Governance Timeout Seam Tests
# ---------------------------------------------------------------------------


class TestGovernanceTimeoutSeam:
    """Verifies human inbox timeout extracted to governance.service."""

    def test_timeout_ceiling_and_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PANTHEON_BFF_HUMAN_INBOX_SURFACE_TIMEOUT_SECONDS", "10.0")
        assert human_inbox_surface_timeout_seconds() == 1.0

        monkeypatch.setenv("PANTHEON_BFF_HUMAN_INBOX_SURFACE_TIMEOUT_SECONDS", "0.25")
        assert human_inbox_surface_timeout_seconds() == 0.25

        monkeypatch.setenv("PANTHEON_BFF_HUMAN_INBOX_SURFACE_TIMEOUT_SECONDS", "not-a-number")
        assert human_inbox_surface_timeout_seconds() == 1.0

        monkeypatch.setenv("PANTHEON_BFF_HUMAN_INBOX_SURFACE_TIMEOUT_SECONDS", "-1.0")
        assert human_inbox_surface_timeout_seconds() == 1.0


# ---------------------------------------------------------------------------
# Incidents Seam Tests
# ---------------------------------------------------------------------------


class TestIncidentsSeam:
    """Verifies incident filtering, projection, and mounted router composition."""

    def test_project_bff_incident_case(self) -> None:
        raw = {
            "incident_id": "inc-100",
            "artifact_id": "art-1",
            "artifact_version": "v1.0",
            "status": "active",
            "severity": "P1",
        }
        projected = _project_bff_incident_case(raw)
        assert projected["id"] == "inc-100"
        assert projected["incident_id"] == "inc-100"
        assert projected["lineage_ref"] == "art-1@v1.0"

    def test_filter_bff_incidents(self) -> None:
        inc_p1 = {"severity": "P1", "status": "active", "capital_pool_id": "pool-1"}
        inc_p2 = {"severity": "P2", "status": "resolved", "capital_pool_id": "pool-2"}

        assert _bff_incident_matches_filters(inc_p1, severity="P1")
        assert not _bff_incident_matches_filters(inc_p2, severity="P1")
        assert _bff_incident_matches_filters(inc_p2, status="resolved")
        assert not _bff_incident_matches_filters(inc_p1, status="resolved")
        assert _bff_incident_matches_filters(inc_p1, affected_pool_id="pool-1")
        assert not _bff_incident_matches_filters(inc_p2, affected_pool_id="pool-1")

    def test_incidents_router_mounted_list_and_filter(self) -> None:
        store = _FakeIncidentStore()
        service = IncidentService(read_surface=store)
        app = FastAPI()
        app.include_router(
            create_incident_router(
                service=service,
                extract_identity=lambda *a, **kw: OperatorIdentity(operator_id="op-1", roles=["operator", "viewer"]),
                list_bff_incidents=lambda **kw: service.list_incidents(**kw),
                get_bff_incident=lambda inc_id: service.get_incident(inc_id),
                utc_now=lambda: "2026-09-23T00:00:00Z",
            )
        )
        client = TestClient(app)
        res = client.get("/bff/incidents")
        assert res.status_code == 200
        items = res.json().get("items") or res.json().get("data", {}).get("items") or []
        assert len(items) == 2


# ---------------------------------------------------------------------------
# Performance Attribution Mounted Composition Tests
# ---------------------------------------------------------------------------


class TestPerformanceAttributionMountedComposition:
    """Verifies mounted performance attribution router decoupled from main."""

    def test_performance_attribution_router_mounted_agreement(self) -> None:
        store = _FakePerformanceStore()
        direct_res = pm12_performance_attribution_response(
            dimensions=["persona"],
            period="latest",
            page_token=None,
            page_size=50,
            tenant_id="tenant-alpha",
            read_store=store,
        )

        app = FastAPI()
        app.include_router(
            create_performance_attribution_router(
                extract_identity=lambda *a, **kw: OperatorIdentity(operator_id="op-1", roles=["operator", "viewer"]),
                require_read_role=lambda *a, **kw: None,
                bff_me_tenant_payload=lambda *a, **kw: {"id": "tenant-alpha", "tenant_id": "tenant-alpha"},
                pm12_performance_attribution_response=lambda **kw: pm12_performance_attribution_response(read_store=store, **kw),
                attribution_dimensions=("persona", "strategy", "pool", "asset", "broker", "runtime", "regime"),
            )
        )
        client = TestClient(app)
        res = client.get("/bff/management/performance-attribution?dimension=persona")
        assert res.status_code == 200
        body = res.json()

        assert body["data"]["items"] == direct_res["data"]["items"]
        assert body["data"]["summary"] == direct_res["data"]["summary"]
        assert body["meta"]["surfaces"]["performance_attribution"]["status"] == direct_res["meta"]["surfaces"]["performance_attribution"]["status"] == "ok"
        assert body["meta"]["surfaces"]["performance_attribution"]["source"] == direct_res["meta"]["surfaces"]["performance_attribution"]["source"] == "bff_composed"
        assert len(body["data"]["items"]) == 1
        assert body["data"]["items"][0]["dimension_key"] == "p-1"
        assert body["data"]["summary"]["total_pnl"] == 500.0

    def test_performance_attribution_fresh_path(self) -> None:
        store = _FakePerformanceStore(status="fresh")
        app = FastAPI()
        app.include_router(
            create_performance_attribution_router(
                extract_identity=lambda *a, **kw: OperatorIdentity(operator_id="op-1", roles=["operator", "viewer"]),
                require_read_role=lambda *a, **kw: None,
                bff_me_tenant_payload=lambda *a, **kw: {"id": "tenant-alpha", "tenant_id": "tenant-alpha"},
                pm12_performance_attribution_response=lambda **kw: pm12_performance_attribution_response(read_store=store, **kw),
                attribution_dimensions=("persona", "strategy", "pool", "asset", "broker", "runtime", "regime"),
            )
        )
        client = TestClient(app)
        res = client.get("/bff/management/performance-attribution?dimension=persona")
        assert res.status_code == 200
        body = res.json()
        perf = body["meta"]["surfaces"]["performance_attribution"]
        assert perf["status"] == "ok"
        assert perf["source"] == "bff_composed"
        assert perf["coverage"] == 1.0
        assert perf["missing_bindings"] is False
        assert len(body["data"]["items"]) == 1
        assert body["data"]["summary"]["total_pnl"] == 500.0

    def test_performance_attribution_degraded_path(self) -> None:
        store = _FakePerformanceStore(status="degraded")
        app = FastAPI()
        app.include_router(
            create_performance_attribution_router(
                extract_identity=lambda *a, **kw: OperatorIdentity(operator_id="op-1", roles=["operator", "viewer"]),
                require_read_role=lambda *a, **kw: None,
                bff_me_tenant_payload=lambda *a, **kw: {"id": "tenant-alpha", "tenant_id": "tenant-alpha"},
                pm12_performance_attribution_response=lambda **kw: pm12_performance_attribution_response(read_store=store, **kw),
                attribution_dimensions=("persona", "strategy", "pool", "asset", "broker", "runtime", "regime"),
            )
        )
        client = TestClient(app)
        res = client.get("/bff/management/performance-attribution?dimension=persona")
        assert res.status_code == 200
        body = res.json()
        perf = body["meta"]["surfaces"]["performance_attribution"]
        assert perf["status"] == "degraded"

    def test_performance_attribution_unavailable_path(self) -> None:
        direct_res = pm12_performance_attribution_response(
            dimensions=["persona"],
            period="latest",
            page_token=None,
            page_size=50,
            tenant_id="tenant-alpha",
            read_store=object(),
        )
        app = FastAPI()
        app.include_router(
            create_performance_attribution_router(
                extract_identity=lambda *a, **kw: OperatorIdentity(operator_id="op-1", roles=["operator", "viewer"]),
                require_read_role=lambda *a, **kw: None,
                bff_me_tenant_payload=lambda *a, **kw: {"id": "tenant-alpha", "tenant_id": "tenant-alpha"},
                pm12_performance_attribution_response=lambda **kw: pm12_performance_attribution_response(read_store=object(), **kw),
                attribution_dimensions=("persona", "strategy", "pool", "asset", "broker", "runtime", "regime"),
            )
        )
        client = TestClient(app)
        res = client.get("/bff/management/performance-attribution?dimension=persona")
        assert res.status_code == 200
        body = res.json()

        perf = body["meta"]["surfaces"]["performance_attribution"]
        assert perf["status"] == "unavailable"
        assert perf["source"] == "bff_composed"
        assert perf["coverage"] == 0.0
        assert perf["missing_bindings"] is True
        assert body["meta"]["surfaces"]["runtime_bindings"]["source"] == "missing"
        assert body["meta"]["surfaces"]["runtime_bindings"]["status"] == "unavailable"
        assert len(body["data"]["items"]) == 0

        assert direct_res["meta"]["surfaces"]["performance_attribution"]["status"] == "unavailable"
        assert direct_res["meta"]["surfaces"]["performance_attribution"]["coverage"] == 0.0
        assert direct_res["meta"]["surfaces"]["performance_attribution"]["missing_bindings"] is True

    def test_performance_attribution_cross_tenant_isolation(self) -> None:
        store = _FakePerformanceStore()
        app = FastAPI()
        app.include_router(
            create_performance_attribution_router(
                extract_identity=lambda auth=None, **_: OperatorIdentity(
                    operator_id="op-alpha" if "alpha" in str(auth) else "op-beta",
                    claims={"tenant_id": "tenant-alpha" if "alpha" in str(auth) else "tenant-beta"},
                    roles=["operator", "viewer"],
                ),
                require_read_role=lambda *a, **kw: None,
                bff_me_tenant_payload=lambda identity, **kw: {"id": identity.claims["tenant_id"], "tenant_id": identity.claims["tenant_id"]},
                pm12_performance_attribution_response=lambda **kw: pm12_performance_attribution_response(read_store=store, **kw),
                attribution_dimensions=("persona", "strategy", "pool", "asset", "broker", "runtime", "regime"),
            )
        )
        client = TestClient(app)

        res_alpha = client.get(
            "/bff/management/performance-attribution?dimension=persona",
            headers={"Authorization": "Bearer alpha"},
        )
        assert res_alpha.status_code == 200
        body_alpha = res_alpha.json()
        assert [it["dimension_key"] for it in body_alpha["data"]["items"]] == ["p-1"]
        assert body_alpha["data"]["summary"]["total_pnl"] == 500.0

        res_beta = client.get(
            "/bff/management/performance-attribution?dimension=persona",
            headers={"Authorization": "Bearer beta"},
        )
        assert res_beta.status_code == 200
        body_beta = res_beta.json()
        assert [it["dimension_key"] for it in body_beta["data"]["items"]] == ["p-2"]
        assert body_beta["data"]["summary"]["total_pnl"] == 250.0
