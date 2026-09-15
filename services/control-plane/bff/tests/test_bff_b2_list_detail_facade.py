"""
BFF-B2-001: Integration tests for the B2.1 Strategy / Persona / Capital /
Deployment list-detail facade (14 read endpoints).

Covers:
  - GET /bff/strategies           list + page_info + DTO shape
  - GET /bff/strategies/{id}      detail + 404 for unknown id
  - GET /bff/strategies/{id}/specs  sub-resource list
  - GET /bff/personas             list + page_info + DTO shape
  - GET /bff/personas/{id}        detail + 404 for unknown id
  - GET /bff/personas/{id}/route-policy
  - GET /bff/personas/{id}/evaluations
  - GET /bff/personas/{id}/memory
  - GET /bff/capital-pools        list + page_info
  - GET /bff/capital-pools/{id}   detail + 404 for unknown id
  - GET /bff/deployments          list + page_info
  - GET /bff/deployments/{id}     detail + 404 for unknown id
  - GET /bff/rebalances           list + page_info
  - GET /bff/rebalances/{id}      detail + 404 for unknown id
  - Unauthenticated requests return HTTP 401 for all 14 endpoints
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from typing import Any, Callable, Dict, List, Optional, Sequence
import urllib.request as urllib_request
import uuid

import pytest
from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.models import ErrorCode, OperatorIdentity
from services.control_plane.bff.ports import create_in_memory_read_surface_ports
from services.control_plane.bff.strategies.router import create_strategies_router
from services.control_plane.bff.personas import PersonaService, create_personas_router
import services.control_plane.bff.personas.routes.collection as persona_collection
import services.control_plane.bff.personas.service as persona_service
from services.control_plane.bff.capital.router import create_capital_router
from services.control_plane.bff.deployment.router import create_deployment_router
from services.control_plane.bff.deployment.ports import DeploymentQueries


def _stable_json_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _pm12_allocation_line_digest(line: dict[str, Any]) -> str:
    basis = {
        'persona_id': line.get('persona_id'),
        'stage': line.get('stage'),
        'capital_scope': line.get('capital_scope') or 'pool',
        'capital_pool_id': line.get('capital_pool_id'),
        'target_weight': line.get('target_weight'),
        'delta': line.get('delta'),
        'cap_reasons': list(line.get('cap_reasons') or []),
        'evidence_refs': list(line.get('evidence_refs') or []),
    }
    return _stable_json_hash(basis)


OPERATOR_HEADERS = {"Authorization": "Bearer op-b2:operator"}
NO_AUTH_HEADERS: dict = {}

_TS = "2026-05-23T00:00:00Z"


def _test_bff_error(status_code: int, code: Any, message: str, reason: Optional[str] = None, **kwargs: Any) -> HTTPException:
    error_code = code.value if hasattr(code, "value") else str(code)
    detail = {
        "error": {
            "code": error_code,
            "message": message,
            "reason": reason or message,
            **kwargs,
        }
    }
    return HTTPException(status_code=status_code, detail=detail)


def _test_extract_identity(authorization: Optional[str] = None) -> OperatorIdentity:
    if not authorization or not authorization.strip():
        raise _test_bff_error(401, "UNAUTHORIZED", "Missing authorization", "Missing authorization")
    return OperatorIdentity(
        operator_id="op-b2",
        roles=["operator", "reader", "admin", "approver"],
        claims={"tenant_id": "tenant-default", "tenant": "tenant-default", "tenant_ids": ["tenant-default"]},
        token_kind="bearer",
    )


def _test_require_read_role(identity: Any) -> None:
    if not identity or not getattr(identity, "operator_id", None):
        raise _test_bff_error(401, "UNAUTHORIZED", "Unauthorized")


def _test_require_operator_role(identity: Any) -> None:
    if not identity or not getattr(identity, "operator_id", None):
        raise _test_bff_error(401, "UNAUTHORIZED", "Unauthorized")
    roles = set(getattr(identity, "roles", []))
    if "operator" not in roles and "admin" not in roles:
        raise _test_bff_error(403, ErrorCode.FORBIDDEN, "Operator role required")


class _ListDetailFacadeTestStore:
    def __init__(self) -> None:
        self.ports = create_in_memory_read_surface_ports()
        self._personas: dict[str, dict[str, Any]] = {}
        self._pools: dict[str, dict[str, Any]] = {}
        self._rebalances: dict[str, dict[str, Any]] = {}
        self._route_policies: dict[str, dict[str, Any]] = {}
        self._evaluations: dict[str, list[dict[str, Any]]] = {}
        self._memories: dict[str, list[dict[str, Any]]] = {}
        self._strategies: dict[str, dict[str, Any]] = {}
        self._deployments: dict[str, dict[str, Any]] = {}

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self.ports, name, None)
        if attr is not None and callable(attr):
            def _safe_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return attr(*args, **kwargs)
                except TypeError:
                    return attr(*args)
            return _safe_wrapper
        if attr is not None:
            return attr
        raise AttributeError(f"'_ListDetailFacadeTestStore' has no attribute '{name}'")

    def dataset_source(self, dataset: str, **kwargs: Any) -> str:
        return "local_snapshot"

    def list_strategies(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._strategies.values())

    def list_strategy_specs(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._strategies.values())

    def get_strategy(self, strategy_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not strategy_id:
            return None
        return self._strategies.get(strategy_id)

    def get_strategy_spec(self, strategy_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not strategy_id:
            return None
        return self._strategies.get(strategy_id)

    def get_strategy_spec_detail(self, strategy_id: Optional[str], version_selector: str = "current") -> Optional[dict[str, Any]]:
        if not strategy_id:
            return None
        return self._strategies.get(strategy_id)

    def list_strategy_spec_versions(self, strategy_id: Optional[str]) -> list[dict[str, Any]]:
        if strategy_id and strategy_id in self._strategies:
            return [{"id": f"spec-{strategy_id}", "version": "1.0.0"}]
        return []

    def upsert_strategy(self, record: dict[str, Any]) -> dict[str, Any]:
        strat_id = record.get("id") or record.get("strategy_id") or f"strat-{len(self._strategies) + 1}"
        item = {**record, "id": strat_id, "strategy_id": strat_id}
        self._strategies[strat_id] = item
        return item

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._personas.values())

    def create_persona(self, **kwargs: Any) -> dict[str, Any]:
        persona_id = kwargs.get("persona_id") or kwargs.get("id") or f"persona-{len(self._personas) + 1}"
        tenant_id = kwargs.get("tenant_id") or "tenant-default"
        name = kwargs.get("name") or persona_id
        archetype = kwargs.get("archetype") or "generalist"
        metadata = dict(kwargs.get("metadata") or {})
        metadata.setdefault("archetype", archetype)
        metadata.setdefault("owner", "op-b2")
        metadata.setdefault("risk_level", "low")
        metadata.setdefault("tenant_id", tenant_id)
        metadata.setdefault("paper_ledger_id", f"ledger-{persona_id}")
        metadata.setdefault("capital_pool_id", "pool-main")
        metadata.setdefault("legacy_paper_capital_pool_id", "pool-main")
        metadata.setdefault("runtime_binding_id", f"runtime-{persona_id}")
        metadata.setdefault("deployment_stage", "paper")
        metadata.setdefault("capital_mode", "paper")
        item = {
            "id": persona_id,
            "persona_id": persona_id,
            "tenant_id": tenant_id,
            "name": name,
            "state": kwargs.get("state") or kwargs.get("lifecycle_state") or "active",
            "lifecycle_state": kwargs.get("lifecycle_state") or kwargs.get("state") or "active",
            "archetype": archetype,
            "created_at": kwargs.get("created_at") or "2026-05-23T00:00:00Z",
            "updated_at": kwargs.get("updated_at") or "2026-05-23T00:00:00Z",
            **kwargs,
            "metadata": metadata,
        }
        self._personas[persona_id] = item
        return item

    def get_persona(self, persona_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not persona_id:
            return None
        return self._personas.get(persona_id)

    def list_capital_pools(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._pools.values())

    def create_capital_pool(self, **kwargs: Any) -> dict[str, Any]:
        pool_id = kwargs.get("pool_id") or kwargs.get("id") or f"pool-{len(self._pools) + 1}"
        item = {
            "id": pool_id,
            "pool_id": pool_id,
            "name": kwargs.get("name") or pool_id,
            "status": "active",
            "created_at": "2026-05-23T00:00:00Z",
            "updated_at": "2026-05-23T00:00:00Z",
            **kwargs,
        }
        self._pools[pool_id] = item
        return item

    def get_capital_pool(self, pool_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not pool_id:
            return None
        return self._pools.get(pool_id)

    def list_rebalances(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._rebalances.values())

    def create_rebalance(self, **kwargs: Any) -> dict[str, Any]:
        rb_id = kwargs.get("rebalance_id") or kwargs.get("id") or f"rb-{len(self._rebalances) + 1}"
        item = {
            "id": rb_id,
            "rebalance_id": rb_id,
            "status": "pending",
            "created_at": "2026-05-23T00:00:00Z",
            "updated_at": "2026-05-23T00:00:00Z",
            **kwargs,
        }
        self._rebalances[rb_id] = item
        return item

    def create_capital_rebalance_proposal(self, **kwargs: Any) -> dict[str, Any]:
        return self.create_rebalance(**kwargs)

    def get_rebalance(self, rebalance_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not rebalance_id:
            return None
        return self._rebalances.get(rebalance_id)

    def list_deployments(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._deployments.values())

    def get_deployment(self, deployment_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not deployment_id:
            return None
        return self._deployments.get(deployment_id)

    def get_route_policy_for_persona(self, persona_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not persona_id:
            return None
        return self._route_policies.get(persona_id, {
            "persona_id": persona_id,
            "personaId": persona_id,
            "policy": "default",
            "route": "default",
            "version": "v1",
            "rules": [],
        })

    def get_persona_route_policy(self, persona_id: Optional[str]) -> Optional[dict[str, Any]]:
        return self.get_route_policy_for_persona(persona_id)

    def get_evaluations_for_persona(self, persona_id: Optional[str]) -> list[dict[str, Any]]:
        if not persona_id:
            return []
        return self._evaluations.get(persona_id, [{"eval_id": f"eval-{persona_id}", "persona_id": persona_id, "score": 90.0, "status": "completed"}])

    def list_persona_evaluations(self, persona_id: Optional[str]) -> list[dict[str, Any]]:
        return self.get_evaluations_for_persona(persona_id)

    def get_allocation_evaluation(self, eval_id: Optional[str]) -> Optional[dict[str, Any]]:
        eval_id = eval_id or "eval-alloc-001"
        snapshot_id = "rk-snap-001"
        policy_version = "v1"
        line = {
            "ranking_snapshot_id": snapshot_id,
            "allocation_evaluation_id": eval_id,
            "allocation_policy_version": policy_version,
            "persona_id": "persona-alpha",
            "stage": "paper",
            "capital_scope": "pool",
            "capital_pool_id": "pool-main",
            "capital_sleeve_id": None,
            "current_weight": 0.0,
            "target_weight": 0.5,
            "delta": 0.5,
            "cap_reasons": [],
            "evidence_refs": [],
            "status": "admitted",
            "amount": 1000,
        }
        line["allocation_line_digest"] = facade_state._pm12_allocation_line_digest(line)
        content_digest = facade_state._stable_json_hash({
            "ranking_snapshot_id": snapshot_id,
            "allocation_evaluation_id": eval_id,
            "allocation_policy_version": policy_version,
            "lines": [line],
        })
        return {
            "id": eval_id,
            "allocation_evaluation_id": eval_id,
            "capital_pool_id": "pool-main",
            "status": "completed",
            "created_at": "2026-05-23T00:00:00Z",
            "ranking_snapshot_id": snapshot_id,
            "allocation_policy_version": policy_version,
            "content_digest": content_digest,
            "lines": [line],
            "admitted_lines": [line],
        }

    def list_persona_memories(self, persona_id: Optional[str]) -> list[dict[str, Any]]:
        if not persona_id:
            return []
        return self._memories.get(persona_id, [{"memory_id": f"mem-{persona_id}", "persona_id": persona_id, "content": "test memory", "created_at": "2026-05-23T00:00:00Z"}])

    def get_teaching_sessions_for_persona(self, persona_id: Optional[str]) -> list[dict[str, Any]]:
        return [{"session_id": f"session-{persona_id}", "persona_id": persona_id}]

    def list_rankings(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"id": "rk-snap-001", "ranking_snapshot_id": "rk-snap-001", "snapshot_id": "rk-snap-001", "quarter": "2026Q2", "status": "admitted", "rankings": []}]

    def get_ranking(self, ranking_id: Optional[str]) -> Optional[dict[str, Any]]:
        return {"id": "rk-snap-001", "ranking_snapshot_id": "rk-snap-001", "snapshot_id": "rk-snap-001", "quarter": "2026Q2", "status": "admitted", "rankings": []}

    def get_quarterly_ranking_snapshot(self, snapshot_id: Optional[str]) -> Optional[dict[str, Any]]:
        return self.get_ranking_snapshot(snapshot_id)

    def get_ranking_snapshot(self, snapshot_id: Optional[str]) -> Optional[dict[str, Any]]:
        formula_version = getattr(facade_state, "_PM12_LEAGUE_FORMULA_VERSION", "v1")
        payload = {
            "surface": "quarterly",
            "period": "2026Q2",
            "formula_version": formula_version,
            "items": [],
        }
        digest = facade_state._stable_json_hash(payload)
        return {
            "id": snapshot_id or "rk-snap-001",
            "snapshot_id": snapshot_id or "rk-snap-001",
            "surface": "quarterly",
            "period": "2026Q2",
            "formula_version": formula_version,
            "items": [],
            "content_digest": digest,
            "status": "admitted",
        }


def _mock_create_capital_pool(payload: dict, context: Optional[dict] = None) -> dict:
    pool_id = payload.get("pool_id") or f"pool-{uuid.uuid4().hex[:8]}"
    pool = {
        "id": pool_id,
        "pool_id": pool_id,
        "name": payload.get("name", "Main Pool"),
        "status": payload.get("status", "active"),
        "owner_id": payload.get("owner_id", "op-b2"),
        "owner_type": payload.get("owner_type", "operator"),
        "currency": payload.get("currency", "USD"),
        "budget": payload.get("budget", 100000),
        "created_at": "2026-05-23T00:00:00Z",
        "updated_at": "2026-05-23T00:00:00Z",
    }
    if hasattr(facade_state.read_store, "_pools"):
        facade_state.read_store._pools[pool_id] = pool
    return pool


def _mock_create_rebalance(payload: dict, context: Optional[dict] = None) -> dict:
    reb_id = f"reb-{uuid.uuid4().hex[:8]}"
    item = {
        "id": reb_id,
        "rebalance_id": reb_id,
        "capital_pool_id": payload.get("capital_pool_id"),
        "status": "pending",
        "reason": payload.get("reason", "b2 test"),
        "created_at": "2026-05-23T00:00:00Z",
    }
    if hasattr(facade_state.read_store, "_rebalances"):
        facade_state.read_store._rebalances[reb_id] = item
    return item


def _mock_coordinate_persona_create(record: Any, payload: dict, owner: str) -> tuple:
    persona_id = getattr(record, "persona_id", None) or f"persona-{uuid.uuid4().hex[:8]}"
    tenant_id = str(getattr(record, "tenant_id", "") or "tenant-default")
    archetype = payload.get("archetype") or "generalist"
    meta = {
        "archetype": archetype,
        "owner": owner,
        "tenant_id": tenant_id,
        "risk_level": "low",
        "paper_ledger_id": f"ledger-{persona_id}",
        "paper_ledger": {
            "id": f"ledger-{persona_id}",
            "mode": "paper",
            "persona_id": persona_id,
            "is_isolated": True,
        },
        "evidence_refs": [],
        "capital_pool_id": "pool-main",
        "legacy_paper_capital_pool_id": "pool-main",
        "runtime_binding_id": f"runtime-{persona_id}",
        "deployment_stage": "paper",
        "capital_mode": "paper",
    }
    persona = {
        "id": persona_id,
        "persona_id": persona_id,
        "tenant_id": tenant_id,
        "name": payload.get("name", "Persona"),
        "state": "active",
        "lifecycle_state": "active",
        "archetype": archetype,
        "created_at": "2026-05-23T00:00:00Z",
        "updated_at": "2026-05-23T00:00:00Z",
        "metadata": meta,
    }
    if hasattr(facade_state.read_store, "_personas"):
        facade_state.read_store._personas[persona_id] = persona
    facade_state._PERSONA_BFF_OVERLAY[persona_id] = {
        "id": persona_id,
        "persona_id": persona_id,
        "name": persona["name"],
        "state": "active",
        "updatedAt": "2026-05-23T00:00:00Z",
        "archetype": archetype,
        "owner": owner,
        "risk": "low",
        "tenantId": tenant_id,
    }
    if hasattr(record, "state"):
        record.state = "completed"
    if hasattr(record, "current_step"):
        record.current_step = "ready"
    if hasattr(record, "references"):
        record.references = []
    return record, persona, meta, None


def _forward_coordinate_persona_create(record: Any, *, payload: dict, owner: str):
    return facade_state._coordinate_persona_create(record, payload, owner)


class _DummyProvisioningStore:
    def list_by_tenant(self, tenant_id: str) -> list:
        return []

    def list_all(self) -> list:
        return []

    def get(self, tenant_id: str, key: str) -> Any:
        return None

    def get_by_persona(self, tenant_id: str, persona_id: str) -> Any:
        return None

    def reserve(self, **kwargs: Any) -> tuple:
        return None, None


_ORIGINAL_PERSONA_COLLECTION_COORDINATE = persona_collection._coordinate_persona_create
_ORIGINAL_PERSONA_SERVICE_COORDINATE = persona_service._coordinate_persona_create
_ORIGINAL_PERSONA_PROVISIONING_STORE = persona_service._PERSONA_PROVISIONING_STORE


@pytest.fixture(scope="module", autouse=True)
def _patch_persona_create_coordination_seam():
    """Route persona-create coordination to this module's test double.

    ``personas/routes/collection.py`` imports ``_coordinate_persona_create``
    by value at module-import time, and ``_persona_provisioning_store()``
    reads a bare module global; the production router factory has no
    dependency-injection hook for either, so this fixture patches the two
    production module globals for the lifetime of this test module only and
    restores the originals afterward, with an isolation check on both ends
    so a leaked patch fails loudly instead of silently affecting other
    collected suites.
    """
    assert persona_collection._coordinate_persona_create is _ORIGINAL_PERSONA_COLLECTION_COORDINATE
    assert persona_service._coordinate_persona_create is _ORIGINAL_PERSONA_SERVICE_COORDINATE
    assert persona_service._PERSONA_PROVISIONING_STORE is _ORIGINAL_PERSONA_PROVISIONING_STORE

    persona_collection._coordinate_persona_create = _forward_coordinate_persona_create
    persona_service._coordinate_persona_create = _forward_coordinate_persona_create
    persona_service._PERSONA_PROVISIONING_STORE = _DummyProvisioningStore()
    try:
        yield
    finally:
        persona_collection._coordinate_persona_create = _ORIGINAL_PERSONA_COLLECTION_COORDINATE
        persona_service._coordinate_persona_create = _ORIGINAL_PERSONA_SERVICE_COORDINATE
        persona_service._PERSONA_PROVISIONING_STORE = _ORIGINAL_PERSONA_PROVISIONING_STORE
        assert persona_collection._coordinate_persona_create is _ORIGINAL_PERSONA_COLLECTION_COORDINATE
        assert persona_service._coordinate_persona_create is _ORIGINAL_PERSONA_SERVICE_COORDINATE
        assert persona_service._PERSONA_PROVISIONING_STORE is _ORIGINAL_PERSONA_PROVISIONING_STORE


class _DynamicStoreProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(facade_state.read_store, name)


dynamic_store = _DynamicStoreProxy()


class _FacadeDeploymentQueries:
    def __init__(self, get_store: Callable[[], Any]) -> None:
        self._get_store = get_store

    def list_deployment_plans(self, *args: Any, **kwargs: Any) -> Sequence[Dict[str, Any]]:
        store = self._get_store()
        if hasattr(store, "list_deployments"):
            return store.list_deployments()
        return []

    def get_deployment_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        store = self._get_store()
        if hasattr(store, "get_deployment"):
            return store.get_deployment(plan_id)
        return None

    def list_registry_entries(self) -> Sequence[Dict[str, Any]]:
        return []

    def get_binding(self, binding_id: str) -> Optional[Dict[str, Any]]:
        return None

    def get_approval_decision(self, decision_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return None

    def get_review_summary(self, plan_id: str) -> Optional[Dict[str, Any]]:
        return None

    def get_allowed_actions(self, plan_id: str) -> Optional[Dict[str, Any]]:
        return None

    def get_capital_pool(self, pool_id: Optional[str]) -> Optional[Dict[str, Any]]:
        store = self._get_store()
        if hasattr(store, "get_capital_pool"):
            return store.get_capital_pool(pool_id)
        return None

    def get_bindings_for_pool(self, pool_id: Optional[str]) -> Sequence[Dict[str, Any]]:
        return []

    def get_runtime_binding(self, runtime_binding_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return None

    def list_runtime_bindings(self) -> Sequence[Dict[str, Any]]:
        return []

    def get_rollbacks(self, runtime_id: Optional[str]) -> Sequence[Dict[str, Any]]:
        return []

    def get_latest_run(self, plan_id: str) -> Optional[Dict[str, Any]]:
        return None

    def get_deployment_diff(self, plan_id: str) -> Optional[Dict[str, Any]]:
        return None

    def dataset_source(self, dataset: str) -> str:
        return "local_snapshot"

    def get_paper_runtime_monitoring_session(self, *args: Any, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return None

    def get_telemetry_summary(self, runtime_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return None


def _create_app() -> FastAPI:
    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request, exc: HTTPException):
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    strat_router = create_strategies_router(
        read_surface=dynamic_store,
        get_read_store=lambda: facade_state.read_store,
        extract_identity=_test_extract_identity,
        require_read_role=_test_require_read_role,
        require_operator_role=_test_require_operator_role,
        bff_error=_test_bff_error,
        list_strategy_summaries=lambda: facade_state.read_store.list_strategy_specs(),
        strategy_write_owner=dynamic_store,
    )
    app.include_router(strat_router)

    persona_svc = PersonaService(
        write_owner=dynamic_store,
        ranking_write_owner=dynamic_store,
        read_store=dynamic_store,
        command_store=type("DummyCmdStore", (), {})(),
        provisioning_store=_DummyProvisioningStore(),
        bff_error_fn=_test_bff_error,
    )
    persona_rtr = create_personas_router(
        service=persona_svc,
        extract_identity_fn=_test_extract_identity,
        require_read_role_fn=_test_require_read_role,
        require_operator_role_fn=_test_require_operator_role,
        bff_error_fn=_test_bff_error,
    )
    app.include_router(persona_rtr)

    cap_router = create_capital_router(
        read_surface=dynamic_store,
        get_capital_authority=lambda: facade_state,
        extract_identity=_test_extract_identity,
        require_read_role=_test_require_read_role,
        require_operator_role=_test_require_operator_role,
        bff_error=_test_bff_error,
    )
    app.include_router(cap_router)

    dep_router = create_deployment_router(
        queries=_FacadeDeploymentQueries(lambda: facade_state.read_store),
        commands=None,
        extract_identity=_test_extract_identity,
        require_read_role=_test_require_read_role,
        require_operator_role=_test_require_operator_role,
        bff_error=_test_bff_error,
        utc_now=lambda: _TS,
        page_slice=lambda items, token, size: (list(items), None),
        snapshot_meta=lambda ts: {"snapshot_at": ts},
        dataset_surface_status=lambda *a, **kw: {"status": "ok"},
        composed_surface_status=lambda *a, **kw: {"status": "ok"},
        read_surface_meta=lambda *a, **kw: {"snapshot_at": _TS},
        raise_if_read_surface_unavailable=lambda *a, **kw: None,
        aggregate_group_surface=lambda *a, **kw: {"status": "ok"},
        split_csv_query=lambda q: q.split(",") if q else None,
        meta_staleness=lambda: None,
        stable_json_hash=_stable_json_hash,
        resolve_final_idempotency_key=lambda ik, xik: str(ik or xik or ""),
        reject_body_idempotency_key=lambda p: None,
        request_dry_run_requested=lambda: False,
        gov_bff_idempotency={},
        publish_event=lambda *a, **kw: "ev-1",
        sse_buffers={},
        sse_subscribers={},
        gov_bff_action_command=lambda *a, **kw: {},
        deprecated_bff_path_response=lambda *a, **kw: {},
        sem_command_response=lambda *a, **kw: {},
        stream_generic_events=lambda *a, **kw: None,
        surface_degradation_reason=lambda *a, **kw: None,
    )
    app.include_router(dep_router)

    return app


class _FacadeContext:
    def __init__(self):
        self.read_store = _ListDetailFacadeTestStore()
        self.urllib_request = urllib_request
        self._PM12_LEAGUE_FORMULA_VERSION = 'v1'
        self._stable_json_hash = _stable_json_hash
        self._pm12_allocation_line_digest = _pm12_allocation_line_digest
        self._STRATEGY_PERSONA_BFF_IDEMPOTENCY: dict[str, Any] = {}
        self._STRATEGY_BFF_OVERLAY: dict[str, Any] = {}
        self._PERSONA_BFF_OVERLAY: dict[str, Any] = {}
        self._CAPITAL_BFF_IDEMPOTENCY: dict[str, Any] = {}
        self.create_capital_pool = _mock_create_capital_pool
        self.create_rebalance = _mock_create_rebalance
        self.create_capital_rebalance_proposal = _mock_create_rebalance
        self._coordinate_persona_create = _mock_coordinate_persona_create
        self.build_persona_runtime_profile = lambda *a, **kw: type('Profile', (), {'to_dict': lambda s: {}})()
        self.app = _create_app()


facade_state = _FacadeContext()


def _fresh_client(td: str) -> TestClient:
    facade_state.read_store = _ListDetailFacadeTestStore()
    facade_state.create_capital_pool = _mock_create_capital_pool
    facade_state.create_rebalance = _mock_create_rebalance
    facade_state.create_capital_rebalance_proposal = _mock_create_rebalance
    facade_state._coordinate_persona_create = _mock_coordinate_persona_create
    facade_state.build_persona_runtime_profile = lambda *a, **kw: type("Profile", (), {"to_dict": lambda s: {}})()
    facade_state._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
    facade_state._STRATEGY_BFF_OVERLAY.clear()
    facade_state._PERSONA_BFF_OVERLAY.clear()
    facade_state._CAPITAL_BFF_IDEMPOTENCY.clear()
    return TestClient(facade_state.app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_persona(client: TestClient, name: str = "Momentum Persona") -> str:
    """Create a persona via BFF and return its id."""
    import uuid
    key = f"b2-persona-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/bff/personas",
        json={"name": name},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": key},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _seed_capital_pool(client: TestClient, name: str = "Main Pool") -> str:
    """Create a capital pool via BFF and return its id."""
    import uuid
    key = f"b2-pool-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/bff/capital-pools",
        json={"name": name},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": key},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    return str(data.get("id") or data.get("pool_id") or body.get("id") or body.get("pool_id") or "")


def _seed_strategy(client: TestClient, name: str = "Alpha Strategy") -> str:
    """Create a strategy via BFF overlay and return its id."""
    import uuid
    key = f"b2-strategy-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/bff/strategies",
        json={"name": name},
        headers={**OPERATOR_HEADERS, "Idempotency-Key": key},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _seed_rebalance(client: TestClient, pool_id: str) -> str:
    """Create a rebalance via BFF and return its id."""
    import uuid
    key = f"b2-rebalance-{uuid.uuid4().hex[:8]}"
    eval_rec = facade_state.read_store.get_allocation_evaluation("eval-alloc-001") or {}
    lines = eval_rec.get("lines") or [{"pool_id": pool_id, "amount": 1000}]
    resp = client.post(
        "/bff/rebalances",
        json={
            "capital_pool_id": pool_id,
            "reason": "b2 test",
            "ranking_snapshot_id": "rk-snap-001",
            "allocation_evaluation_id": "eval-alloc-001",
            "allocation_policy_version": "v1",
            "simulation": {"passed": True},
            "constraints": {"max_drawdown": 0.1},
            "rollback_target": "rb-target-001",
            "lines": lines,
        },
        headers={**OPERATOR_HEADERS, "Idempotency-Key": key},
    )
    assert resp.status_code in (201, 202), resp.text
    body = resp.json()
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    return str(data.get("rebalance_id") or data.get("id") or body.get("rebalance_id") or body.get("id") or "")


# ---------------------------------------------------------------------------
# 1. GET /bff/strategies
# ---------------------------------------------------------------------------

def test_bff_strategies_list_returns_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/strategies", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            assert "meta" in body
            assert "page_info" in body
        finally:
            facade_state.read_store = original


def test_bff_strategies_list_dto_shape() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            _seed_strategy(client)
            resp = client.get("/bff/strategies", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]
            assert len(items) >= 1
            item = items[0]
            assert "id" in item
            assert "name" in item
            assert "state" in item
            assert "risk" in item
            assert "personaIds" in item
            assert "capitalPoolId" in item
        finally:
            facade_state.read_store = original


def test_bff_strategies_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/strategies", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            facade_state.read_store = original


# ---------------------------------------------------------------------------
# 2. GET /bff/strategies/{id}
# ---------------------------------------------------------------------------

def test_bff_strategy_detail_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            sid = _seed_strategy(client, "Detail Test Strategy")
            resp = client.get(f"/bff/strategies/{sid}", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body and "meta" in body
            data = body["data"]
            assert data["id"] == sid
            assert "name" in data
            assert "state" in data
            assert "risk" in data
        finally:
            facade_state.read_store = original


def test_bff_strategy_detail_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/strategies/nonexistent-strategy-b2", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
            detail = resp.json()
            assert detail["error"]["code"] == "RESOURCE_NOT_FOUND"
        finally:
            facade_state.read_store = original


def test_bff_strategy_detail_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/strategies/any-id", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            facade_state.read_store = original


# ---------------------------------------------------------------------------
# 3. GET /bff/strategies/{id}/specs
# ---------------------------------------------------------------------------

def test_bff_strategy_specs_list_returns_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            sid = _seed_strategy(client, "Specs Strategy")
            resp = client.get(f"/bff/strategies/{sid}/specs", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            assert "meta" in body
        finally:
            facade_state.read_store = original


def test_bff_strategy_specs_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/strategies/any-id/specs", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            facade_state.read_store = original


# ---------------------------------------------------------------------------
# 4. GET /bff/personas
# ---------------------------------------------------------------------------

def test_bff_personas_list_returns_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            assert "meta" in body
            assert "page_info" in body
        finally:
            facade_state.read_store = original


def test_bff_personas_list_dto_shape() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            _seed_persona(client)
            resp = client.get("/bff/personas", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]
            assert len(items) >= 1
            item = items[0]
            assert "id" in item
            assert "name" in item
            assert "state" in item
            assert "archetype" in item
        finally:
            facade_state.read_store = original


def test_bff_personas_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            facade_state.read_store = original


# ---------------------------------------------------------------------------
# 5. GET /bff/personas/{id}
# ---------------------------------------------------------------------------

def test_bff_persona_detail_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            pid = _seed_persona(client, "Detail Persona")
            resp = client.get(f"/bff/personas/{pid}", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body and "meta" in body
            data = body["data"]
            assert data["id"] == pid
            assert "name" in data
            assert "state" in data
            assert "archetype" in data
        finally:
            facade_state.read_store = original


def test_bff_persona_detail_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas/nonexistent-persona-b2", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
            detail = resp.json()
            assert detail["error"]["code"] == "RESOURCE_NOT_FOUND"
        finally:
            facade_state.read_store = original


def test_bff_persona_detail_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas/any-id", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            facade_state.read_store = original


# ---------------------------------------------------------------------------
# 6. GET /bff/personas/{id}/route-policy
# ---------------------------------------------------------------------------

def test_bff_persona_route_policy_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            pid = _seed_persona(client, "Route Policy Persona")
            resp = client.get(f"/bff/personas/{pid}/route-policy", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body and "meta" in body
            assert body["data"]["personaId"] == pid
        finally:
            facade_state.read_store = original


def test_bff_persona_route_policy_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas/ghost-persona/route-policy", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            facade_state.read_store = original


def test_bff_persona_route_policy_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas/any-id/route-policy", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            facade_state.read_store = original


# ---------------------------------------------------------------------------
# 7. GET /bff/personas/{id}/evaluations
# ---------------------------------------------------------------------------

def test_bff_persona_evaluations_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            pid = _seed_persona(client, "Eval Persona")
            resp = client.get(f"/bff/personas/{pid}/evaluations", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body and "meta" in body
            assert isinstance(body["data"], list)
        finally:
            facade_state.read_store = original


def test_bff_persona_evaluations_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas/ghost-persona/evaluations", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            facade_state.read_store = original


def test_bff_persona_evaluations_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas/any-id/evaluations", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            facade_state.read_store = original


# ---------------------------------------------------------------------------
# 8. GET /bff/personas/{id}/memory
# ---------------------------------------------------------------------------

def test_bff_persona_memory_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            pid = _seed_persona(client, "Memory Persona")
            resp = client.get(f"/bff/personas/{pid}/memory", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            # memory endpoint returns a list of memory items under data
            assert "data" in body and "meta" in body and "page_info" in body
            assert isinstance(body["data"], list)
            assert body["meta"]["status"] == "degraded"
            assert body["meta"]["memory_source"]["reason"] == "memory_plane_unconfigured"
            assert body["meta"]["memory_source"]["fallback_used"] is False
        finally:
            facade_state.read_store = original


def test_bff_persona_memory_reads_canonical_memory_plane(monkeypatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps(
                {
                    "hits": [
                        {
                            "type": "persona",
                            "relevance_score": 0.93,
                            "entry": {"memory_id": "pmem-1", "persona_id": captured["persona_id"]},
                        },
                        {"type": "institutional", "entry": {"entry_id": "inst-1"}},
                    ],
                    "authz": {"policy_version": "governance-authz.v1"},
                }
            ).encode()

    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse()

    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            monkeypatch.setenv("PANTHEON_MEMORY_API_URL", "http://memory:8080")
            monkeypatch.setattr(facade_state.urllib_request, "urlopen", fake_urlopen)
            client = _fresh_client(td)
            pid = _seed_persona(client, "Canonical Memory Persona")
            captured["persona_id"] = pid
            resp = client.get(f"/bff/personas/{pid}/memory", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["data"] == [
                {"memory_id": "pmem-1", "persona_id": pid, "relevance_score": 0.93}
            ]
            assert body["meta"]["status"] == "ok"
            source = body["meta"]["memory_source"]
            assert source["kind"] == "canonical_memory_plane"
            assert source["available"] is True
            assert source["workspace_is_source_of_truth"] is False
            assert "scope=persona" in captured["url"]
            assert f"persona_id={pid}" in captured["url"]
        finally:
            facade_state.read_store = original


def test_bff_persona_memory_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas/ghost-persona/memory", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
        finally:
            facade_state.read_store = original


def test_bff_persona_memory_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/personas/any-id/memory", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            facade_state.read_store = original


# ---------------------------------------------------------------------------
# 9. GET /bff/capital-pools
# ---------------------------------------------------------------------------

def test_bff_capital_pools_list_returns_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/capital-pools", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            assert "meta" in body
            assert "page_info" in body
        finally:
            facade_state.read_store = original


def test_bff_capital_pools_list_dto_shape() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            _seed_capital_pool(client)
            resp = client.get("/bff/capital-pools", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]
            assert len(items) >= 1
            item = items[0]
            assert "id" in item or "pool_id" in item
        finally:
            facade_state.read_store = original


def test_bff_capital_pools_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/capital-pools", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            facade_state.read_store = original


# ---------------------------------------------------------------------------
# 10. GET /bff/capital-pools/{id}
# ---------------------------------------------------------------------------

def test_bff_capital_pool_detail_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            pool_id = _seed_capital_pool(client, "Detail Pool")
            resp = client.get(f"/bff/capital-pools/{pool_id}", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body and "meta" in body
        finally:
            facade_state.read_store = original


def test_bff_capital_pool_detail_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/capital-pools/nonexistent-pool-b2", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
            detail = resp.json()
            assert detail["error"]["code"] == "RESOURCE_NOT_FOUND"
        finally:
            facade_state.read_store = original


def test_bff_capital_pool_detail_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/capital-pools/any-id", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            facade_state.read_store = original


# ---------------------------------------------------------------------------
# 11. GET /bff/deployments
# ---------------------------------------------------------------------------

def test_bff_deployments_list_returns_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/deployments", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            assert "meta" in body
            assert "page_info" in body
            assert isinstance(body["data"], list)
        finally:
            facade_state.read_store = original


def test_bff_deployments_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/deployments", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            facade_state.read_store = original


# ---------------------------------------------------------------------------
# 12. GET /bff/deployments/{id}
# ---------------------------------------------------------------------------

def test_bff_deployment_detail_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/deployments/nonexistent-deploy-b2", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
            detail = resp.json()
            assert detail["error"]["code"] == "RESOURCE_NOT_FOUND"
        finally:
            facade_state.read_store = original


def test_bff_deployment_detail_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/deployments/any-id", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            facade_state.read_store = original


# ---------------------------------------------------------------------------
# 13. GET /bff/rebalances
# ---------------------------------------------------------------------------

def test_bff_rebalances_list_returns_envelope() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/rebalances", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body
            assert "meta" in body
            assert "page_info" in body
        finally:
            facade_state.read_store = original


def test_bff_rebalances_list_dto_shape() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            pool_id = _seed_capital_pool(client, "Rebalance Pool")
            _seed_rebalance(client, pool_id)
            resp = client.get("/bff/rebalances", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            items = resp.json()["data"]
            assert len(items) >= 1
        finally:
            facade_state.read_store = original


def test_bff_rebalances_list_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/rebalances", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            facade_state.read_store = original


# ---------------------------------------------------------------------------
# 14. GET /bff/rebalances/{id}
# ---------------------------------------------------------------------------

def test_bff_rebalance_detail_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            pool_id = _seed_capital_pool(client, "Detail Rebalance Pool")
            rb_id = _seed_rebalance(client, pool_id)
            assert rb_id, "Expected a non-empty rebalance id"
            resp = client.get(f"/bff/rebalances/{rb_id}", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "data" in body and "meta" in body
        finally:
            facade_state.read_store = original


def test_bff_rebalance_detail_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/rebalances/nonexistent-rb-b2", headers=OPERATOR_HEADERS)
            assert resp.status_code == 404, resp.text
            detail = resp.json()
            assert detail["error"]["code"] == "RESOURCE_NOT_FOUND"
        finally:
            facade_state.read_store = original


def test_bff_rebalance_detail_unauthorized() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/rebalances/any-id", headers=NO_AUTH_HEADERS)
            assert resp.status_code == 401, resp.text
        finally:
            facade_state.read_store = original
