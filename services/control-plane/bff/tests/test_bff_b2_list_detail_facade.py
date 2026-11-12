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
    # The provisioning store is captured here, not at import time: another
    # module's wiring may legitimately rebind the global between this file's
    # collection and its first test, which made this guard order dependent.
    original_provisioning_store = persona_service._PERSONA_PROVISIONING_STORE

    persona_collection._coordinate_persona_create = _forward_coordinate_persona_create
    persona_service._coordinate_persona_create = _forward_coordinate_persona_create
    persona_service._PERSONA_PROVISIONING_STORE = _DummyProvisioningStore()
    try:
        yield
    finally:
        persona_collection._coordinate_persona_create = _ORIGINAL_PERSONA_COLLECTION_COORDINATE
        persona_service._coordinate_persona_create = _ORIGINAL_PERSONA_SERVICE_COORDINATE
        persona_service._PERSONA_PROVISIONING_STORE = original_provisioning_store
        assert persona_collection._coordinate_persona_create is _ORIGINAL_PERSONA_COLLECTION_COORDINATE
        assert persona_service._coordinate_persona_create is _ORIGINAL_PERSONA_SERVICE_COORDINATE
        assert persona_service._PERSONA_PROVISIONING_STORE is original_provisioning_store


facade_state = None


class _DynamicStoreProxy:
    def __getattr__(self, name: str) -> Any:
        if facade_state is None or getattr(facade_state, "read_store", None) is None:
            raise AttributeError(name)
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
        resolve_final_idempotency_key=lambda ik: str(ik or ""),
        reject_body_idempotency_key=lambda p: None,
        request_dry_run_requested=lambda: False,
        publish_event=lambda *a, **kw: "ev-1",
        sse_buffers={},
        sse_subscribers={},
        gov_bff_action_command=lambda *a, **kw: {},
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
        self.create_pool = lambda payload, **ctx: _mock_create_capital_pool(payload, ctx)
        self.create_rebalance = lambda payload, **ctx: _mock_create_rebalance(payload, ctx)
        self._coordinate_persona_create = _mock_coordinate_persona_create
        self.build_persona_runtime_profile = lambda *a, **kw: type('Profile', (), {'to_dict': lambda s: {}})()
        self.app = _create_app()


facade_state = _FacadeContext()


def _fresh_client(td: str) -> TestClient:
    facade_state.read_store = _ListDetailFacadeTestStore()
    facade_state.create_pool = lambda payload, **ctx: _mock_create_capital_pool(payload, ctx)
    facade_state.create_rebalance = lambda payload, **ctx: _mock_create_rebalance(payload, ctx)
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


def _memory_jwt(monkeypatch, tenant: str = "tenant-alpha") -> str:
    from services.runtime_auth_inbound import encode_jwt_hs256

    secret = "memory-auth-test-secret-0123456789abcdef"
    for name in ("PANTHEON_RUNTIME_JWT_SECRET", "PANTHEON_BFF_JWT_SECRET"):
        monkeypatch.setenv(name, secret)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
    return encode_jwt_hs256(
        {"sub": "op-mem", "operator_id": "op-mem", "roles": ["operator"], "tenant_id": tenant, "exp": 4102444800},
        secret=secret,
    )


class _MemFakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self._payload).encode()


def test_bff_persona_memory_reads_canonical_memory_plane(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = {k.lower(): v for k, v in request.header_items()}
        return _MemFakeResponse(
            {
                "hits": [
                    {"type": "persona", "relevance_score": 0.93,
                     "entry": {"memory_id": "pmem-1", "persona_id": captured["persona_id"]}},
                    {"type": "institutional", "entry": {"entry_id": "inst-1"}},
                ],
                "authz": {"policy_version": "governance-authz.v1"},
            }
        )

    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            monkeypatch.setenv("PANTHEON_MEMORY_API_URL", "http://memory:8080")
            monkeypatch.setattr(facade_state.urllib_request, "urlopen", fake_urlopen)
            client = _fresh_client(td)
            pid = _seed_persona(client, "Canonical Memory Persona")
            captured["persona_id"] = pid
            token = _memory_jwt(monkeypatch)
            resp = client.get(f"/bff/personas/{pid}/memory", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["data"] == [
                {"memory_id": "pmem-1", "persona_id": pid, "relevance_score": 0.93}
            ]
            assert body["meta"]["status"] == "ok"
            source = body["meta"]["memory_source"]
            assert source["available"] is True
            assert source["workspace_is_source_of_truth"] is False
            assert "scope=persona" in captured["url"]
            assert f"persona_id={pid}" in captured["url"]
            # Actual transport carries the caller's verified identity and tenant;
            # roles are never asserted through the query string.
            assert captured["headers"]["authorization"] == f"Bearer {token}"
            assert captured["headers"]["x-tenant-id"] == "tenant-alpha"
            assert "actor_roles=&" in captured["url"] or "actor_roles=" in captured["url"]
            assert "operator" not in captured["url"].split("actor_roles=")[1].split("&")[0]
        finally:
            facade_state.read_store = original


def test_bff_persona_memory_denied_stays_explicit(monkeypatch) -> None:
    from urllib import error as urllib_error

    def deny(request, timeout):
        raise urllib_error.HTTPError(request.full_url, 403, "forbidden", {}, None)

    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            monkeypatch.setenv("PANTHEON_MEMORY_API_URL", "http://memory:8080")
            monkeypatch.setattr(facade_state.urllib_request, "urlopen", deny)
            client = _fresh_client(td)
            pid = _seed_persona(client, "Denied Memory Persona")
            token = _memory_jwt(monkeypatch)
            resp = client.get(f"/bff/personas/{pid}/memory", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["data"] == []
            assert body["meta"]["status"] == "degraded"
            src = body["meta"]["memory_source"]
            assert src["available"] is False and src["fallback_used"] is False
            assert src["reason"] == "memory_plane_access_denied" and src["http_status"] == 403
        finally:
            facade_state.read_store = original


def test_bff_persona_memory_unbound_tenant_never_calls_memory(monkeypatch) -> None:
    calls = []
    monkeypatch.setenv("PANTHEON_MEMORY_API_URL", "http://memory:8080")
    monkeypatch.setattr(facade_state.urllib_request, "urlopen", lambda *a, **k: calls.append(a))
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            pid = _seed_persona(client, "Unbound Memory Persona")
            token = _memory_jwt(monkeypatch)
            resp = client.get(
                f"/bff/personas/{pid}/memory",
                headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "tenant-beta"},
            )
            assert resp.status_code == 200, resp.text
            src = resp.json()["meta"]["memory_source"]
            assert src["available"] is False
            assert src["reason"] == "memory_plane_tenant_unbound"
            assert calls == []
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


# ---------------------------------------------------------------------------
# 15. GET /api/v1/operator/persona-management/{persona_id} Readiness & Linkage
# ---------------------------------------------------------------------------

def test_persona_management_resolves_bound_deployment_plans_approvals_and_paper_running_health() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            store = facade_state.read_store
            persona_id = "persona-be19a1fac471247f2b2a"
            runtime_id = "rb-677cd34c70a2b20c64b4"
            plan_id = "plan-677cd34c70a2b20c64b4"
            decision_id = "apv-677cd34c70a2b20c64b4"
            artifact_id = "artifact-qinghe-r2"
            pool_id = "pool-paper-qinghe-r2"
            cap_binding_id = "pcb-qinghe-r2"

            store._personas[persona_id] = {
                "persona_id": persona_id,
                "name": "青禾 R2",
                "display_name": "青禾 R2",
                "tenant_id": "tenant-default",
                "lifecycle_state": "research_only",
                "paper_runtime_state": "paper_running",
                "strategy_family": "momentum",
                "mandate": "Paper momentum validation",
                "metadata": {
                    "paper_runtime_state": "paper_running",
                    "environment_ceiling": "paper",
                    "persona_capital_binding_id": cap_binding_id,
                    "capital_pool_id": pool_id,
                    "runtime_binding_id": runtime_id,
                    "strategy_artifact_id": artifact_id,
                },
            }
            runtime_bindings = {
                runtime_id: {
                    "runtime_id": runtime_id,
                    "binding_id": runtime_id,
                    "tenant_id": "tenant-default",
                    "persona_id": persona_id,
                    "plan_id": plan_id,
                    "status": "active",
                    "health_status": "healthy",
                    "environment": "paper",
                    "capital_pool_id": pool_id,
                    "persona_capital_binding_id": cap_binding_id,
                    "strategy_artifact_id": artifact_id,
                    "updated_at": "2026-11-07T08:00:00Z",
                    "metadata": {
                        "object_store": {"artifact_id": artifact_id},
                    },
                }
            }
            # DeploymentPlan linked via binding_id/capital_pool_id/approval_decision_id (no direct persona_id)
            deployment_plans = {
                plan_id: {
                    "plan_id": plan_id,
                    "tenant_id": "tenant-default",
                    "binding_id": runtime_id,
                    "capital_pool_id": pool_id,
                    "approval_decision_id": decision_id,
                    "strategy_artifact_id": artifact_id,
                    "status": "active",
                    "environment": "paper",
                },
                "plan-foreign": {
                    "plan_id": "plan-foreign",
                    "tenant_id": "tenant-other",
                    "persona_id": persona_id,
                    "binding_id": runtime_id,
                },
            }
            # Terminal approved ApprovalDecision linked via decision_id and target_id=artifact_id
            approval_decisions = {
                decision_id: {
                    "decision_id": decision_id,
                    "tenant_id": "tenant-default",
                    "status": "approved",
                    "decision": "approved",
                    "target_type": "strategy_artifact",
                    "target_id": artifact_id,
                    "decided_at": "2026-11-07T07:00:00Z",
                },
                "apv-foreign": {
                    "decision_id": "apv-foreign",
                    "tenant_id": "tenant-other",
                    "persona_id": persona_id,
                    "status": "approved",
                },
            }
            rq = store.ports.ooda_management.review_queue
            rq._approval_decisions_reader = lambda: list(approval_decisions.values())
            rq._approval_decisions_by_id_reader = lambda ids: [
                approval_decisions[i] for i in ids if i in approval_decisions
            ]
            store.list_runtime_bindings = lambda: list(runtime_bindings.values())
            store.list_deployment_plans = lambda: list(deployment_plans.values())
            store.get_deployment_plan = lambda pid: deployment_plans.get(str(pid))

            resp = client.get(
                f"/api/v1/operator/persona-management/{persona_id}",
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            payload = resp.json()["data"]

            # Governance lifecycle maturity stays research_only (never falsely promoted to paper_owner)
            assert payload["persona"]["lifecycle_state"] == "research_only"

            # Health separates lifecycle_maturity from current paper execution mode
            health = payload["health"]
            assert health["status"] == "healthy"
            assert "persona_lifecycle_not_active" not in (health.get("reasons") or [])
            assert health["lifecycle_maturity"] == "research_only"
            assert health["execution_mode"] == "paper"
            assert health["operational_state"] == "paper_running"

            # Bound DeploymentPlan and terminal ApprovalDecision are resolved and tenant-filtered
            plan_ids = [p.get("plan_id") or p.get("id") for p in payload["deploymentPlans"]]
            assert plan_ids == [plan_id]
            decision_ids = [a.get("decision_id") or a.get("id") for a in payload["approvals"]]
            assert decision_ids == [decision_id]
        finally:
            facade_state.read_store = original


def test_persona_management_keeps_unreconciled_research_only_degraded() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = facade_state.read_store
        try:
            client = _fresh_client(td)
            store = facade_state.read_store
            persona_id = "persona-research-unreconciled"
            store._personas[persona_id] = {
                "persona_id": persona_id,
                "name": "Unreconciled Research",
                "display_name": "Unreconciled Research",
                "tenant_id": "tenant-default",
                "lifecycle_state": "research_only",
                "strategy_family": "mean_reversion",
                "metadata": {},
            }

            resp = client.get(
                f"/api/v1/operator/persona-management/{persona_id}",
                headers=OPERATOR_HEADERS,
            )
            assert resp.status_code == 200, resp.text
            payload = resp.json()["data"]
            assert payload["persona"]["lifecycle_state"] == "research_only"
            health = payload["health"]
            assert health["status"] == "degraded"
            assert "persona_lifecycle_not_active" in (health.get("reasons") or [])
            assert health["lifecycle_maturity"] == "research_only"
            assert health["execution_mode"] == "unknown"
        finally:
            facade_state.read_store = original


# ============================================================================
# 16. BFF-PERSONA-DETAIL-BOUNDED-READS-20261109: Bounded Owner Reads & CPU
# ============================================================================


def _build_bounded_detail_fixture() -> dict[str, Any]:
    from services.control_plane.bff.persona_provisioning import MemoryPersonaProvisioningStore
    from services.control_plane.bff.tests.test_bff_persona_list_compact_rows import (
        _CONNECTOR,
        _real_raw,
        _real_truth,
    )

    truth = _real_truth()
    prov_store = MemoryPersonaProvisioningStore()
    raws: list[dict[str, Any]] = []
    for i in range(160):
        raw = _real_raw(i)
        raw["tenant_id"] = "tenant-default"
        raw["lifecycle_state"] = "active" if i == 0 else "research_only"
        raw["strategy_family"] = "momentum"
        raw["metadata"]["tenant_id"] = "tenant-default"
        raw["metadata"]["deployment_plan_id"] = f"plan-missing-{i}"
        raw["metadata"]["approval_decision_id"] = f"apv-missing-{i}"
        raw["metadata"]["data_source_status"]["required_source_health"] = [truth[_CONNECTOR]]
        raw["metadata"]["provisioning_authoritative_readback"] = {
            "deployment": {"plan_id": f"plan-{i}", "status": "ready_to_deploy"},
            "governance": {"approval_decision_id": f"apv-{i}", "decision_state": "decided"},
        }
        raws.append(raw)
        prov_store.reserve(
            tenant_id="tenant-default",
            idempotency_key=f"idem-bounded-{i}",
            request_hash=f"hash-bounded-{i}",
            normalized_name=f"p{i}",
            persona_id=f"persona-{i}",
            request_payload={"name": f"P{i}", "market": "TW", "requested_by": "op-b2"},
        )

    bindings = [
        {
            "id": "bind-0",
            "binding_id": "bind-0",
            "persona_id": "persona-0",
            "capital_pool_id": "pool-0",
            "allocation_weight": 0.25,
            "status": "active",
        }
    ]
    pools = [
        {
            "id": "pool-0",
            "pool_id": "pool-0",
            "name": "Alpha Pool",
            "status": "active",
        }
    ]

    plans = [
        {
            "id": f"plan-{i}",
            "plan_id": f"plan-{i}",
            "tenant_id": "tenant-default",
            "persona_id": f"persona-{i}",
            "approval_decision_id": f"apv-{i}",
            "status": "ready_to_deploy",
            "deployment_stage": "paper",
            "metadata": {"detail": "p" * 2000},
        }
        for i in range(160)
    ]
    rbs = [
        {
            "id": f"rb-{i}",
            "binding_id": f"rb-{i}",
            "runtime_id": f"rt-{i}",
            "persona_id": f"persona-{i}",
            "plan_id": f"plan-unlisted-{i}",
            "status": "running",
            "deployment_mode": "paper",
            "environment": "paper",
        }
        for i in range(160)
    ]
    approvals = [
        {
            "decision_id": f"apv-{i}",
            "id": f"apv-{i}",
            "tenant_id": "tenant-default",
            "target_id": f"plan-{i}",
            "persona_id": f"persona-{i}",
            "decision_state": "decided",
            "status": "approved",
            "outcome": "approved",
        }
        for i in range(160)
    ]
    specs = {
        f"strat-{i}": {
            "strategy_id": f"strat-{i}",
            "title": f"Strategy {i}",
            "name": f"Strategy {i}",
            "lifecycle_state": "approved",
            "risk": "medium",
            "persona_ids": [f"persona-{i % 40}"],
            "metadata": {},
            "current_spec_version_id": f"reg-strat-{i}-v1",
            "current_spec_version": "1.0.0",
            "versions": [
                {
                    "spec_version_id": f"reg-strat-{i}-v{v}",
                    "spec_version": f"1.0.{v}",
                    "title": f"Strategy {i}",
                    "lifecycle_state": "approved",
                    "risk": "medium",
                    "persona_ids": [f"persona-{i % 40}"],
                    "metadata": {"payload": "s" * 1000},
                    "created_at": f"2026-09-0{v}T00:00:00Z",
                    "updated_at": f"2026-09-0{v}T00:00:00Z",
                }
                for v in range(1, 5)
            ],
        }
        for i in range(160)
    }
    return {
        "truth": truth,
        "prov_store": prov_store,
        "raws": raws,
        "plans": plans,
        "rbs": rbs,
        "approvals": approvals,
        "specs": specs,
        "bindings": bindings,
        "pools": pools,
    }


def test_persona_detail_routes_bounded_owner_reads_no_per_plan_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC1: Detail routes make <= 1 deployment plan read and <= 1 approval read with zero per-item fetches."""
    from services.control_plane.bff.command_queue import CommandStore
    from services.control_plane.bff.ports import create_persona_registry_write_owner

    fx = _build_bounded_detail_fixture()
    counts = {
        "personas_provider": 0,
        "plans_provider": 0,
        "approvals_reader": 0,
        "get_deployment_plan": 0,
        "get_approval_decision": 0,
        "prov_get_by_persona": 0,
        "prov_list_by_tenant": 0,
    }

    def personas_provider() -> list[dict[str, Any]]:
        counts["personas_provider"] += 1
        return list(fx["raws"])

    def plans_provider() -> list[dict[str, Any]]:
        counts["plans_provider"] += 1
        return list(fx["plans"])

    def approvals_reader() -> list[dict[str, Any]]:
        counts["approvals_reader"] += 1
        return list(fx["approvals"])

    store = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={
            "personas": fx["raws"],
            "deployment_plans": fx["plans"],
            "runtime_bindings": fx["rbs"],
            "bindings": fx["bindings"],
            "pools": fx["pools"],
        },
        ooda_management_kwargs={
            "deployment_plans": fx["plans"],
            "approval_decisions": fx["approvals"],
        },
    )
    store.persona_capital_runtime.persona._records_provider = personas_provider
    store.persona_capital_runtime.deployment._plans_provider = plans_provider
    store.ooda_management.review_queue._deployment_plans_reader = plans_provider
    store.ooda_management.review_queue._approval_decisions_reader = approvals_reader
    store.research_knowledge_source._strategy_specs = fx["specs"]

    orig_get_plan = store.get_deployment_plan
    orig_get_apv = store.get_approval_decision

    def spy_get_plan(plan_id: Optional[str]) -> Optional[dict[str, Any]]:
        counts["get_deployment_plan"] += 1
        return orig_get_plan(plan_id)

    def spy_get_apv(decision_id: Optional[str]) -> Optional[dict[str, Any]]:
        counts["get_approval_decision"] += 1
        return orig_get_apv(decision_id)

    monkeypatch.setattr(store, "get_deployment_plan", spy_get_plan)
    monkeypatch.setattr(store, "get_approval_decision", spy_get_apv)

    prov_store = fx["prov_store"]
    orig_get_by_persona = prov_store.get_by_persona
    orig_list_by_tenant = prov_store.list_by_tenant

    def spy_get_by_persona(tenant_id: str, persona_id: str) -> Any:
        counts["prov_get_by_persona"] += 1
        return orig_get_by_persona(tenant_id, persona_id)

    def spy_list_by_tenant(tenant_id: str) -> Any:
        counts["prov_list_by_tenant"] += 1
        return orig_list_by_tenant(tenant_id)

    monkeypatch.setattr(prov_store, "get_by_persona", spy_get_by_persona)
    monkeypatch.setattr(prov_store, "list_by_tenant", spy_list_by_tenant)
    monkeypatch.setattr(persona_service, "_PERSONA_PROVISIONING_STORE", prov_store)

    with tempfile.TemporaryDirectory() as td:
        service = PersonaService(
            write_owner=create_persona_registry_write_owner(),
            ranking_write_owner=object(),
            read_store=store,
            command_store=CommandStore(os.path.join(td, "cmd.jsonl")),
            provisioning_store=prov_store,
            bff_error_fn=_test_bff_error,
        )
        monkeypatch.setattr(service, "_source_ingest_truth_by_connector", lambda: fx["truth"])
        monkeypatch.setattr(persona_service, "_composed_persona_service", service)
        monkeypatch.setattr(persona_service, "utc_now", lambda: _TS)

        app = FastAPI()
        app.include_router(
            create_personas_router(
                service=service,
                extract_identity_fn=_test_extract_identity,
                require_read_role_fn=_test_require_read_role,
                require_operator_role_fn=_test_require_operator_role,
                bff_error_fn=_test_bff_error,
            )
        )
        client = TestClient(app)

        # 1. GET /bff/personas/{persona_id}
        for k in counts:
            counts[k] = 0
        r_bff = client.get("/bff/personas/persona-0", headers=OPERATOR_HEADERS)
        assert r_bff.status_code == 200, r_bff.text
        assert counts["plans_provider"] == 0
        assert counts["approvals_reader"] == 0
        assert counts["get_deployment_plan"] == 0
        assert counts["get_approval_decision"] == 0
        assert counts["personas_provider"] == 1
        assert counts["prov_get_by_persona"] == 1
        assert counts["prov_list_by_tenant"] == 0

        # 2. GET /api/v1/personas/{persona_id}
        for k in counts:
            counts[k] = 0
        r_api = client.get("/api/v1/personas/persona-0", headers=OPERATOR_HEADERS)
        assert r_api.status_code == 200, r_api.text
        assert counts["plans_provider"] == 0
        assert counts["approvals_reader"] == 0
        assert counts["get_deployment_plan"] == 0
        assert counts["get_approval_decision"] == 0
        assert counts["personas_provider"] == 1

        # 3. GET /api/v1/operator/persona-management/{persona_id}
        for k in counts:
            counts[k] = 0
        r_mgmt = client.get("/api/v1/operator/persona-management/persona-0", headers=OPERATOR_HEADERS)
        assert r_mgmt.status_code == 200, r_mgmt.text
        assert counts["plans_provider"] == 1
        assert counts["approvals_reader"] == 1
        assert counts["get_deployment_plan"] == 0
        assert counts["get_approval_decision"] == 0
        assert counts["personas_provider"] == 1
        mgmt_data = r_mgmt.json()["data"]
        assert [p["plan_id"] for p in mgmt_data["deploymentPlans"]] == ["plan-0"]
        assert [a["decision_id"] for a in mgmt_data["approvals"]] == ["apv-0"]


def test_persona_management_detail_does_not_compute_rich_source_health_or_unused_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2: persona-management detail never computes rich source health overlay or unreturned blocks."""
    from services.control_plane.bff.command_queue import CommandStore
    from services.control_plane.bff.ports import create_persona_registry_write_owner
    import services.control_plane.bff.personas.routes.detail as detail_routes

    fx = _build_bounded_detail_fixture()
    store = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={
            "personas": fx["raws"],
            "deployment_plans": fx["plans"],
            "runtime_bindings": fx["rbs"],
            "bindings": fx["bindings"],
            "pools": fx["pools"],
        },
        ooda_management_kwargs={
            "deployment_plans": fx["plans"],
            "approval_decisions": fx["approvals"],
        },
    )

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("persona-management detail must not compute rich source health or unused blocks")

    monkeypatch.setattr(persona_service, "_overlay_source_health_truth", forbidden)
    monkeypatch.setattr(persona_service, "_source_health_bindings_from_requirements", forbidden)
    monkeypatch.setattr(persona_service, "_routed_strategies_for_persona", forbidden)
    monkeypatch.setattr(detail_routes, "_project_persona_dto", forbidden)
    monkeypatch.setattr(detail_routes, "_project_persona_fleet_item", forbidden)
    monkeypatch.setattr(store, "list_evolution_decisions", forbidden)

    with tempfile.TemporaryDirectory() as td:
        service = PersonaService(
            write_owner=create_persona_registry_write_owner(),
            ranking_write_owner=object(),
            read_store=store,
            command_store=CommandStore(os.path.join(td, "cmd.jsonl")),
            provisioning_store=fx["prov_store"],
            bff_error_fn=_test_bff_error,
        )
        monkeypatch.setattr(service, "overlay_source_health_truth", forbidden)
        monkeypatch.setattr(service, "list_evolution_decisions", forbidden)
        monkeypatch.setattr(persona_service, "_composed_persona_service", service)
        monkeypatch.setattr(persona_service, "utc_now", lambda: _TS)

        app = FastAPI()
        app.include_router(
            create_personas_router(
                service=service,
                extract_identity_fn=_test_extract_identity,
                require_read_role_fn=_test_require_read_role,
                require_operator_role_fn=_test_require_operator_role,
                bff_error_fn=_test_bff_error,
            )
        )
        client = TestClient(app)

        resp = client.get("/api/v1/operator/persona-management/persona-0", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["meta"]["surfaces"]["persona_health"]["status"] == "ok"
        assert body["data"]["health"]["status"] == "healthy"
        assert len(body["data"]["runtimeBindings"]) == 1
        assert len(body["data"]["deploymentPlans"]) == 1
        assert len(body["data"]["approvals"]) == 1


def test_persona_detail_field_for_field_identity_and_cpu_reduction(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC3 & AC4: Detail responses stay field-for-field identical and CPU time drops by >= 50%."""
    import time
    from services.control_plane.bff.command_queue import CommandStore
    from services.control_plane.bff.core.owner_reads import request_read_scope
    from services.control_plane.bff.ports import create_persona_registry_write_owner

    fx = _build_bounded_detail_fixture()
    store = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={
            "personas": fx["raws"],
            "deployment_plans": fx["plans"],
            "runtime_bindings": fx["rbs"],
            "bindings": fx["bindings"],
            "pools": fx["pools"],
        },
        ooda_management_kwargs={
            "deployment_plans": fx["plans"],
            "approval_decisions": fx["approvals"],
        },
    )
    store.research_knowledge_source._strategy_specs = fx["specs"]
    monkeypatch.setattr(persona_service, "_PERSONA_PROVISIONING_STORE", fx["prov_store"])

    with tempfile.TemporaryDirectory() as td:
        service = PersonaService(
            write_owner=create_persona_registry_write_owner(),
            ranking_write_owner=object(),
            read_store=store,
            command_store=CommandStore(os.path.join(td, "cmd.jsonl")),
            provisioning_store=fx["prov_store"],
            bff_error_fn=_test_bff_error,
        )
        monkeypatch.setattr(service, "_source_ingest_truth_by_connector", lambda: fx["truth"])
        monkeypatch.setattr(persona_service, "_composed_persona_service", service)
        monkeypatch.setattr(persona_service, "utc_now", lambda: _TS)
        token = persona_service._current_persona_service.set(service)
        try:
            router = create_personas_router(
                service=service,
                extract_identity_fn=_test_extract_identity,
                require_read_role_fn=_test_require_read_role,
                require_operator_role_fn=_test_require_operator_role,
                bff_error_fn=_test_bff_error,
            )
            app = FastAPI()
            app.include_router(router)
            client = TestClient(app)

            # Warm up lazy imports
            client.get("/api/v1/personas/persona-0", headers=OPERATOR_HEADERS)

            r_bff = client.get("/bff/personas/persona-0", headers=OPERATOR_HEADERS)
            r_mgmt = client.get("/api/v1/operator/persona-management/persona-0", headers=OPERATOR_HEADERS)
            assert r_bff.status_code == 200 and r_mgmt.status_code == 200

            # Verify field-for-field identity against legacy directory snapshot + fleet item projection
            directory = persona_service._get_persona_directory_snapshot("tenant-default", snapshot_at=_TS)
            legacy_raw = directory.records_by_id["persona-0"]
            legacy_routed = len(store.list_strategy_specs(persona_id="persona-0") or [])
            legacy_dto = persona_service._project_persona_dto(legacy_raw, overlay=None, routed_strategies=legacy_routed)
            assert json.dumps(r_bff.json()["data"], sort_keys=True) == json.dumps(legacy_dto, sort_keys=True)

            legacy_fleet = persona_service._project_persona_fleet_item(
                store.get_persona("persona-0"),
                all_runtime_bindings=service.list_runtime_bindings(),
                all_incidents=service.list_incidents(),
                all_evolution_decisions=service.list_evolution_decisions(),
            )
            mgmt_data = r_mgmt.json()["data"]
            assert json.dumps(mgmt_data["health"], sort_keys=True) == json.dumps(legacy_fleet["health"], sort_keys=True)
            assert json.dumps(mgmt_data["runtimeBindings"], sort_keys=True) == json.dumps(
                legacy_fleet["runtimeBindings"], sort_keys=True
            )
            assert json.dumps(mgmt_data["capitalPools"], sort_keys=True) == json.dumps(
                legacy_fleet["capitalPools"], sort_keys=True
            )
            assert json.dumps(mgmt_data["activeIncidents"], sort_keys=True) == json.dumps(
                legacy_fleet["activeIncidents"], sort_keys=True
            )
            assert json.dumps(mgmt_data["latestTelemetry"], sort_keys=True) == json.dumps(
                (legacy_fleet["telemetrySummary"] or {}).get("latest"), sort_keys=True
            )

            # Measure CPU reduction: legacy vs bounded on the same machine
            def legacy_bff_call() -> dict[str, Any]:
                snap = persona_service._get_persona_directory_snapshot("tenant-default", snapshot_at=_TS)
                raw = snap.records_by_id["persona-0"]
                routed = len(store.list_strategy_specs(persona_id="persona-0") or [])
                return persona_service._project_persona_dto(raw, overlay=None, routed_strategies=routed)

            def bounded_bff_call() -> dict[str, Any]:
                with request_read_scope():
                    raw = persona_service._get_persona_record_for_detail(
                        "persona-0", "tenant-default", fallback_owner="op-b2"
                    )
                    routed = persona_service._routed_strategies_for_persona("persona-0")
                    return persona_service._project_persona_dto(raw, overlay=None, routed_strategies=routed)

            def legacy_mgmt_call() -> dict[str, Any]:
                p = service.get_persona("persona-0")
                _ = len(store.list_strategy_specs(persona_id="persona-0") or [])
                fi = persona_service._project_persona_fleet_item(
                    p,
                    all_runtime_bindings=service.list_runtime_bindings(),
                    all_incidents=service.list_incidents(),
                    all_evolution_decisions=service.list_evolution_decisions(),
                )
                all_p = service.list_deployment_plans()
                for mid in ("plan-0", "plan-missing-0"):
                    service.get_deployment_plan(mid)
                all_a = list(store.ooda_management.review_queue._approval_decisions_reader() or [])
                all_a.extend(store.ooda_management.list_approval_queue_items() or [])
                for aid in ("apv-0", "apv-missing-0"):
                    service.get_approval_decision(aid)
                return fi

            def bounded_mgmt_call() -> dict[str, Any]:
                with request_read_scope():
                    p = service.get_persona("persona-0")
                    all_p = service.list_deployment_plans()
                    plans_by_id = {
                        str(pl.get("plan_id") or pl.get("id") or ""): pl
                        for pl in all_p
                        if isinstance(pl, dict) and str(pl.get("plan_id") or pl.get("id") or "")
                    }
                    all_a = service.list_approval_decisions()
                    return persona_service._project_persona_management_operational_surface(
                        p,
                        bindings=service.get_bindings_for_persona("persona-0"),
                        sessions=service.get_sessions_for_persona("persona-0"),
                        all_runtime_bindings=service.list_runtime_bindings(),
                        all_incidents=service.list_incidents(),
                        plans_by_id=plans_by_id,
                    )

            t0 = time.process_time()
            legacy_bff_call()
            dt_legacy_bff = time.process_time() - t0

            t0 = time.process_time()
            bounded_bff_call()
            dt_bounded_bff = time.process_time() - t0

            t0 = time.process_time()
            legacy_mgmt_call()
            dt_legacy_mgmt = time.process_time() - t0

            t0 = time.process_time()
            bounded_mgmt_call()
            dt_bounded_mgmt = time.process_time() - t0

            assert dt_bounded_bff <= dt_legacy_bff * 0.5, (dt_legacy_bff, dt_bounded_bff)
            assert dt_bounded_mgmt <= dt_legacy_mgmt * 0.5, (dt_legacy_mgmt, dt_bounded_mgmt)
        finally:
            persona_service._current_persona_service.reset(token)


# ============================================================================
# 17. BFF-PERSONA-ROUTED-COUNT-HOSTED-20261110: Hosted Registry Bounded Count
# ============================================================================


def _build_hosted_registry_strategy_fixture() -> dict[str, Any]:
    """Build 3,376 canonical registry strategy spec entries across 163 personas.

    Covers:
    - 157 personas with routed strategies (persona-0 .. persona-155 + persona-pack-a-momentum)
    - 6 personas with 0 routed strategies (persona-157 .. persona-162)
    - Superseded-only personas (persona-superseded-v1, persona-superseded-v2)
    - Real fixture pack spec loaded from services/control-plane/bff/data/fixtures_pack_a.json
    - Multi-version strategy family with semver ordering (1.10.0 > 1.2.0 > 1.0.0)
    - Multi-version family whose latest version is retired
    - Single-version retired entries (artifact_state, metadata.lifecycle_state, metadata.state)
    - Metadata fallback bindings (personaIds, persona_ids, persona_id, inner.metadata.persona_id)
    - Whitespace-padded duplicate persona IDs
    """
    from pathlib import Path
    from services.control_plane.bff.persona_provisioning import MemoryPersonaProvisioningStore
    from services.control_plane.bff.tests.test_bff_persona_list_compact_rows import (
        _CONNECTOR,
        _real_raw,
        _real_truth,
    )
    from services.registry.storage import persona_binding_counts

    fixture_pack_path = Path(__file__).resolve().parent.parent / "data" / "fixtures_pack_a.json"
    fixture_pack = json.loads(fixture_pack_path.read_text(encoding="utf-8"))
    pack_a_spec = fixture_pack["datasets"]["strategy_specs"]["strategy-pack-a-momentum"]
    pack_a_ver = pack_a_spec["versions"][0]

    truth = _real_truth()
    prov_store = MemoryPersonaProvisioningStore()
    raws: list[dict[str, Any]] = []
    persona_ids_all = [f"persona-{i}" for i in range(156)] + [
        "persona-pack-a-momentum",
        "persona-157",
        "persona-158",
        "persona-159",
        "persona-160",
        "persona-161",
        "persona-162",
    ]
    assert len(persona_ids_all) == 163

    for idx, pid in enumerate(persona_ids_all):
        raw = _real_raw(idx)
        raw["id"] = pid
        raw["persona_id"] = pid
        raw["name"] = f"Persona {pid}"
        raw["display_name"] = f"Persona {pid}"
        raw["tenant_id"] = "tenant-default"
        raw["lifecycle_state"] = "active" if idx == 0 else "research_only"
        raw["strategy_family"] = "momentum"
        raw["metadata"]["tenant_id"] = "tenant-default"
        raw["metadata"]["owner"] = "op-b2"
        raw["metadata"]["data_source_status"]["required_source_health"] = [truth[_CONNECTOR]]
        raws.append(raw)
        prov_store.reserve(
            tenant_id="tenant-default",
            idempotency_key=f"idem-hosted-{idx}",
            request_hash=f"hash-hosted-{idx}",
            normalized_name=f"hosted-{idx}",
            persona_id=pid,
            request_payload={"name": f"Persona {pid}", "market": "TW", "requested_by": "op-b2"},
        )

    registry_entries: list[dict[str, Any]] = []

    # 1. Fixture pack strategy-pack-a-momentum from fixtures_pack_a.json (1 entry)
    registry_entries.append(
        {
            "entry": {
                "registry_id": pack_a_ver["spec_version_id"],
                "artifact_type": "strategy_spec",
                "artifact_state": pack_a_ver["lifecycle_state"],
                "version": pack_a_ver["spec_version"],
                "strategy_id": pack_a_spec["strategy_id"],
                "owner_tenant": "tenant-default",
                "created_at": pack_a_ver["created_at"],
                "updated_at": pack_a_ver["updated_at"],
                "metadata": {
                    "spec_version_id": pack_a_ver["spec_version_id"],
                    "spec_version": pack_a_ver["spec_version"],
                    "title": pack_a_ver["title"],
                    "lifecycle_state": pack_a_ver["lifecycle_state"],
                    "source_kind": pack_a_ver["source_kind"],
                    "persona_ids": list(pack_a_ver["persona_ids"]),
                    "strategy_spec": copy.deepcopy(pack_a_ver) if "copy" in globals() else json.loads(json.dumps(pack_a_ver)),
                },
            }
        }
    )

    # 2. Multi-version semver family (3 entries): 1.0.0 -> persona-superseded-v1,
    #    1.2.0 -> persona-superseded-v2, 1.10.0 (current) -> persona-0 + persona-pack-a-momentum
    for ver_idx, (ver, pids, ts) in enumerate(
        [
            ("1.0.0", ["persona-superseded-v1"], "2026-08-01T00:00:00Z"),
            ("1.10.0", [" persona-0 ", "persona-0", "persona-pack-a-momentum"], "2026-08-02T00:00:00Z"),
            ("1.2.0", ["persona-superseded-v2"], "2026-08-03T00:00:00Z"),
        ],
        start=1,
    ):
        registry_entries.append(
            {
                "entry": {
                    "registry_id": f"reg-multi-semver-v{ver_idx}",
                    "artifact_type": "strategy_spec",
                    "artifact_state": "approved",
                    "version": ver,
                    "strategy_id": "strategy-multi-semver",
                    "owner_tenant": "tenant-default",
                    "created_at": ts,
                    "updated_at": ts,
                    "metadata": {
                        "spec_version_id": f"reg-multi-semver-v{ver_idx}",
                        "spec_version": ver,
                        "title": "Multi Semver Strategy",
                        "personaIds": pids,
                        "strategy_spec": {
                            "strategy_id": "strategy-multi-semver",
                            "spec_version": ver,
                            "title": "Multi Semver Strategy",
                            "lifecycle_state": "approved",
                            "hypothesis": "Multi-version semver ordering check " * 5,
                            "market_scope": {"symbols": ["2330.TW"], "frequency": "1d"},
                            "execution_profile": {"execution_mode_hint": "paper"},
                            "governance": {"approval_required": True, "risk_level": "medium"},
                        },
                    },
                }
            }
        )

    # 3. Multi-version family where current version (2.0.0) is retired (2 entries)
    for ver_idx, (ver, state, ts) in enumerate(
        [
            ("1.0.0", "approved", "2026-08-01T00:00:00Z"),
            ("2.0.0", "retired", "2026-08-05T00:00:00Z"),
        ],
        start=1,
    ):
        registry_entries.append(
            {
                "entry": {
                    "registry_id": f"reg-retired-latest-v{ver_idx}",
                    "artifact_type": "strategy_spec",
                    "artifact_state": state,
                    "version": ver,
                    "strategy_id": "strategy-retired-latest",
                    "owner_tenant": "tenant-default",
                    "created_at": ts,
                    "updated_at": ts,
                    "metadata": {
                        "spec_version_id": f"reg-retired-latest-v{ver_idx}",
                        "spec_version": ver,
                        "persona_id": "persona-157",
                        "strategy_spec": {
                            "strategy_id": "strategy-retired-latest",
                            "spec_version": ver,
                            "lifecycle_state": state,
                            "persona_ids": ["persona-157"],
                        },
                    },
                }
            }
        )

    # 4. Single-version retired entries across different state fields (3 entries)
    for r_idx, pid in enumerate(["persona-158", "persona-159", "persona-160"]):
        registry_entries.append(
            {
                "entry": {
                    "registry_id": f"reg-retired-single-{r_idx}",
                    "artifact_type": "strategy_spec",
                    "artifact_state": "retired" if r_idx == 0 else None,
                    "version": "1.0.0",
                    "strategy_id": f"strategy-retired-single-{r_idx}",
                    "owner_tenant": "tenant-default",
                    "created_at": "2026-08-10T00:00:00Z",
                    "updated_at": "2026-08-10T00:00:00Z",
                    "metadata": {
                        "spec_version_id": f"reg-retired-single-{r_idx}",
                        "spec_version": "1.0.0",
                        "lifecycle_state": "retired" if r_idx == 1 else None,
                        "state": "retired" if r_idx == 2 else None,
                        "persona_ids": [pid],
                        "strategy_spec": {
                            "strategy_id": f"strategy-retired-single-{r_idx}",
                            "spec_version": "1.0.0",
                            "lifecycle_state": "retired",
                            "persona_ids": [pid],
                        },
                    },
                }
            }
        )

    # 5. Remaining entries to reach exact hosted scale of 3,376 canonical registry entries:
    #    - 156 active strategy families (one per persona-0 .. persona-155),
    #      first 111 families have 22 versions (2,442 entries) and next 45 families have 20 versions (900 entries),
    #      plus 25 retired families (25 entries): 9 + 2,442 + 900 + 25 = 3,376 entries.
    for fam_idx in range(156):
        target_pid = f"persona-{fam_idx}"
        ver_count = 22 if fam_idx < 111 else 20
        sid = f"strategy-hosted-fam-{fam_idx}"
        for v in range(1, ver_count + 1):
            semver = f"1.0.{v}"
            bound_pid = target_pid if v == ver_count else "persona-superseded-v1"
            registry_entries.append(
                {
                    "entry": {
                        "registry_id": f"reg-hosted-{fam_idx}-v{v}",
                        "artifact_type": "strategy_spec",
                        "artifact_state": "approved",
                        "version": semver,
                        "strategy_id": sid,
                        "owner_tenant": "tenant-default",
                        "created_at": f"2026-09-{(v % 28) + 1:02d}T00:00:00Z",
                        "updated_at": f"2026-09-{(v % 28) + 1:02d}T00:00:00Z",
                        "metadata": {
                            "spec_version_id": f"reg-hosted-{fam_idx}-v{v}",
                            "spec_version": semver,
                            "title": f"Hosted Strategy {fam_idx}",
                            "persona_ids": [bound_pid],
                            "strategy_spec": {
                                "strategy_id": sid,
                                "spec_version": semver,
                                "title": f"Hosted Strategy {fam_idx}",
                                "lifecycle_state": "approved",
                                "persona_ids": [bound_pid],
                                "hypothesis": f"Canonical hosted strategy hypothesis {fam_idx} v{v} " * 4,
                                "objective": "Deliver risk-adjusted paper execution signals.",
                                "market_scope": {"symbols": ["2330.TW", "2317.TW"], "frequency": "1h"},
                                "execution_profile": {
                                    "execution_mode_hint": "paper",
                                    "capital_pool_id": "pool-0",
                                },
                                "governance": {"approval_required": True, "risk_level": "medium"},
                                "metadata": {"notes": "m" * 256},
                            },
                        },
                    }
                }
            )

    for ret_idx in range(25):
        sid = f"strategy-hosted-retired-extra-{ret_idx}"
        registry_entries.append(
            {
                "entry": {
                    "registry_id": f"reg-hosted-retired-extra-{ret_idx}",
                    "artifact_type": "strategy_spec",
                    "artifact_state": "retired",
                    "version": "1.0.0",
                    "strategy_id": sid,
                    "owner_tenant": "tenant-default",
                    "created_at": "2026-09-15T00:00:00Z",
                    "updated_at": "2026-09-15T00:00:00Z",
                    "metadata": {
                        "spec_version_id": f"reg-hosted-retired-extra-{ret_idx}",
                        "spec_version": "1.0.0",
                        "title": f"Retired Extra {ret_idx}",
                        "lifecycle_state": "retired",
                        "persona_ids": [f"persona-{157 + (ret_idx % 6)}"],
                        "strategy_spec": {
                            "strategy_id": sid,
                            "spec_version": "1.0.0",
                            "lifecycle_state": "retired",
                            "persona_ids": [f"persona-{157 + (ret_idx % 6)}"],
                        },
                    },
                }
            }
        )

    assert len(registry_entries) == 3376
    flat_rows = [item["entry"] for item in registry_entries]
    binding_counts = persona_binding_counts(flat_rows)
    assert len(binding_counts) == 157
    assert binding_counts["persona-0"] == 2
    assert binding_counts["persona-pack-a-momentum"] == 2
    assert binding_counts["persona-1"] == 1
    for z_idx in range(157, 163):
        assert binding_counts.get(f"persona-{z_idx}", 0) == 0
    assert binding_counts.get("persona-superseded-v1", 0) == 0
    assert binding_counts.get("persona-superseded-v2", 0) == 0

    return {
        "truth": truth,
        "prov_store": prov_store,
        "raws": raws,
        "registry_entries": registry_entries,
        "binding_counts": binding_counts,
        "persona_ids_all": persona_ids_all,
    }


def test_hosted_persona_detail_uses_bounded_persona_binding_counts_and_never_full_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1: With PANTHEON_REGISTRY_API_URL set, GET /bff/personas/{id} uses persona_binding_counts and fails on any full spec fetch."""
    import services.control_plane.bff.command_adapters.base as adapter_base
    from services.control_plane.bff.command_queue import CommandStore
    from services.control_plane.bff.core import owner_reads
    from services.control_plane.bff.ports import ReadSurfacePorts, create_persona_registry_write_owner
    from services.control_plane.bff.ports.research_knowledge_source import DefaultResearchKnowledgeSourcePort

    fx = _build_hosted_registry_strategy_fixture()
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.hosted.internal")

    requested_urls: list[str] = []

    def strict_counts_only_http(url: str, **_kwargs: Any) -> Any:
        requested_urls.append(url)
        if not url.endswith("/api/registry/strategy-specs?view=persona_binding_counts"):
            raise AssertionError(f"Forbidden full strategy spec fetch on hosted path: {url}")
        return dict(fx["binding_counts"])

    monkeypatch.setattr(adapter_base, "http_request_json", strict_counts_only_http)

    base_store = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={"personas": fx["raws"]},
    )
    # Use uninjected DefaultResearchKnowledgeSourcePort as on hosted production
    hosted_rks = DefaultResearchKnowledgeSourcePort()
    assert hosted_rks._has_injected_strategy_specs is False

    def forbidden_full_read(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Full strategy spec read must never be called when counting routed strategies")

    monkeypatch.setattr(DefaultResearchKnowledgeSourcePort, "_fetch_canonical_strategy_specs", forbidden_full_read)
    monkeypatch.setattr(DefaultResearchKnowledgeSourcePort, "_fetch_canonical_strategy_specs_uncached", forbidden_full_read)
    monkeypatch.setattr(DefaultResearchKnowledgeSourcePort, "_list_strategy_specs_uncached", forbidden_full_read)
    monkeypatch.setattr(DefaultResearchKnowledgeSourcePort, "list_strategy_specs", forbidden_full_read)

    store = ReadSurfacePorts(
        operations_consultation=base_store.operations_consultation,
        persona_capital_runtime=base_store.persona_capital_runtime,
        ooda_management=base_store.ooda_management,
        research_knowledge_source=hosted_rks,
        lifecycle_telemetry_governance=base_store.lifecycle_telemetry_governance,
        persona_training=base_store.persona_training,
        job_read=base_store.job_read,
        paper_runtime_monitoring_sessions_provider=lambda: [],
        reconciliation_drift_reads=base_store.reconciliation_drift_reads,
    )
    monkeypatch.setattr(persona_service, "_PERSONA_PROVISIONING_STORE", fx["prov_store"])

    tok_auth = owner_reads.authorization.set("Bearer op-b2:operator:tenant-default")
    tok_tenant = owner_reads.selected_tenant.set("tenant-default")
    try:
        with tempfile.TemporaryDirectory() as td:
            service = PersonaService(
                write_owner=create_persona_registry_write_owner(),
                ranking_write_owner=object(),
                read_store=store,
                command_store=CommandStore(os.path.join(td, "cmd.jsonl")),
                provisioning_store=fx["prov_store"],
                bff_error_fn=_test_bff_error,
            )
            monkeypatch.setattr(service, "_source_ingest_truth_by_connector", lambda: fx["truth"])
            monkeypatch.setattr(persona_service, "_composed_persona_service", service)
            monkeypatch.setattr(persona_service, "utc_now", lambda: _TS)

            app = FastAPI()
            app.include_router(
                create_personas_router(
                    service=service,
                    extract_identity_fn=_test_extract_identity,
                    require_read_role_fn=_test_require_read_role,
                    require_operator_role_fn=_test_require_operator_role,
                    bff_error_fn=_test_bff_error,
                )
            )
            client = TestClient(app)

            for pid, expected_count in (
                ("persona-0", 2),
                ("persona-pack-a-momentum", 2),
                ("persona-1", 1),
                ("persona-157", 0),
            ):
                requested_urls.clear()
                resp = client.get(f"/bff/personas/{pid}", headers=OPERATOR_HEADERS)
                assert resp.status_code == 200, resp.text
                assert resp.json()["data"]["routedStrategies"] == expected_count
                assert requested_urls == [
                    "http://registry.hosted.internal/api/registry/strategy-specs?view=persona_binding_counts"
                ]
    finally:
        owner_reads.authorization.reset(tok_auth)
        owner_reads.selected_tenant.reset(tok_tenant)


def test_all_routed_strategies_for_persona_callers_match_full_read_across_real_spec_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2: Every caller of _routed_strategies_for_persona gets the exact same count as the full read across retired, multi-version, fixture pack, and zero-strategy personas."""
    import services.control_plane.bff.command_adapters.base as adapter_base
    from services.control_plane.bff.command_queue import CommandStore
    from services.control_plane.bff.core import owner_reads
    from services.control_plane.bff.core.owner_reads import request_read_scope
    from services.control_plane.bff.ports import ReadSurfacePorts, create_persona_registry_write_owner
    from services.control_plane.bff.ports.research_knowledge_source import DefaultResearchKnowledgeSourcePort

    fx = _build_hosted_registry_strategy_fixture()
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.hosted.internal")

    tok_auth = owner_reads.authorization.set("Bearer op-b2:operator:tenant-default")
    tok_tenant = owner_reads.selected_tenant.set("tenant-default")
    try:
        # Step 1: Compute full-read reference counts on the same 3,376 registry entries
        monkeypatch.setattr(
            adapter_base,
            "http_request_json",
            lambda url, **_kw: fx["registry_entries"] if not url.endswith("view=persona_binding_counts") else fx["binding_counts"],
        )
        full_port = DefaultResearchKnowledgeSourcePort()
        sample_personas = [
            "persona-0",                  # multi-strategy (hosted family + multi-semver 1.10.0 + whitespace dedup) -> 2
            "persona-pack-a-momentum",    # fixture pack strategy-pack-a-momentum + multi-semver 1.10.0 -> 2
            "persona-1",                  # standard 1-strategy active persona -> 1
            "persona-155",                # 20-version family persona -> 1
            "persona-157",                # multi-version retired-latest + retired extra -> 0
            "persona-158",                # retired artifact_state -> 0
            "persona-159",                # retired metadata.lifecycle_state -> 0
            "persona-160",                # retired metadata.state -> 0
            "persona-161",                # zero strategies -> 0
            "persona-162",                # zero strategies -> 0
            "persona-superseded-v1",      # bound only on superseded v1.0.0 -> 0
            "persona-superseded-v2",      # bound only on superseded v1.2.0 -> 0
        ]
        full_read_counts: dict[str, int] = {}
        with request_read_scope():
            for pid in fx["persona_ids_all"] + ["persona-superseded-v1", "persona-superseded-v2"]:
                full_read_counts[pid] = len(full_port.list_strategy_specs(persona_id=pid) or [])

        assert full_read_counts["persona-0"] == 2
        assert full_read_counts["persona-pack-a-momentum"] == 2
        assert full_read_counts["persona-1"] == 1
        assert full_read_counts["persona-155"] == 1
        for zero_pid in (
            "persona-157",
            "persona-158",
            "persona-159",
            "persona-160",
            "persona-161",
            "persona-162",
            "persona-superseded-v1",
            "persona-superseded-v2",
        ):
            assert full_read_counts[zero_pid] == 0

        # Step 2: Enforce bounded count-only HTTP read and test every caller of _routed_strategies_for_persona
        count_urls: list[str] = []

        def bounded_only_http(url: str, **_kw: Any) -> Any:
            count_urls.append(url)
            if not url.endswith("/api/registry/strategy-specs?view=persona_binding_counts"):
                raise AssertionError(f"Full spec read forbidden: {url}")
            return dict(fx["binding_counts"])

        monkeypatch.setattr(adapter_base, "http_request_json", bounded_only_http)

        base_store = create_in_memory_read_surface_ports(
            persona_capital_runtime_kwargs={"personas": fx["raws"]},
        )
        hosted_rks = DefaultResearchKnowledgeSourcePort()
        store = ReadSurfacePorts(
            operations_consultation=base_store.operations_consultation,
            persona_capital_runtime=base_store.persona_capital_runtime,
            ooda_management=base_store.ooda_management,
            research_knowledge_source=hosted_rks,
            lifecycle_telemetry_governance=base_store.lifecycle_telemetry_governance,
            persona_training=base_store.persona_training,
            job_read=base_store.job_read,
            paper_runtime_monitoring_sessions_provider=lambda: [],
            reconciliation_drift_reads=base_store.reconciliation_drift_reads,
        )
        raw_by_id = {r["persona_id"]: r for r in fx["raws"]}

        class _InMemoryPersonaWriteOwner:
            def update_persona(self, persona_id: str, **kwargs: Any) -> Optional[dict[str, Any]]:
                rec = raw_by_id.get(persona_id)
                if rec is None:
                    return None
                updated = json.loads(json.dumps(rec))
                if kwargs.get("name"):
                    updated["name"] = kwargs["name"]
                if isinstance(kwargs.get("metadata"), dict):
                    updated.setdefault("metadata", {}).update(kwargs["metadata"])
                raw_by_id[persona_id] = updated
                return updated

        write_owner = _InMemoryPersonaWriteOwner()
        monkeypatch.setattr(persona_service, "_PERSONA_PROVISIONING_STORE", fx["prov_store"])

        with tempfile.TemporaryDirectory() as td:
            service = PersonaService(
                write_owner=write_owner,
                ranking_write_owner=object(),
                read_store=store,
                command_store=CommandStore(os.path.join(td, "cmd.jsonl")),
                provisioning_store=fx["prov_store"],
                bff_error_fn=_test_bff_error,
            )
            monkeypatch.setattr(service, "_source_ingest_truth_by_connector", lambda: fx["truth"])
            monkeypatch.setattr(persona_service, "_composed_persona_service", service)
            monkeypatch.setattr(persona_service, "utc_now", lambda: _TS)
            token = persona_service._current_persona_service.set(service)
            try:
                app = FastAPI()
                app.include_router(
                    create_personas_router(
                        service=service,
                        extract_identity_fn=_test_extract_identity,
                        require_read_role_fn=_test_require_read_role,
                        require_operator_role_fn=_test_require_operator_role,
                        bff_error_fn=_test_bff_error,
                    )
                )
                client = TestClient(app)

                # Verify all 163 + 2 personas match on _routed_strategies_for_persona
                with request_read_scope():
                    for pid, expected in full_read_counts.items():
                        assert persona_service._routed_strategies_for_persona(pid) == expected

                raw_by_id = {r["persona_id"]: r for r in fx["raws"]}
                for idx, pid in enumerate(sample_personas):
                    expected = full_read_counts[pid]
                    if pid not in raw_by_id:
                        continue
                    raw = raw_by_id[pid]

                    # Caller 1: GET /bff/personas/{persona_id} (routes/detail.py:580)
                    r_detail = client.get(f"/bff/personas/{pid}", headers=OPERATOR_HEADERS)
                    assert r_detail.status_code == 200, r_detail.text
                    assert r_detail.json()["data"]["routedStrategies"] == expected

                    # Caller 2: POST /bff/personas/{persona_id}/provisioning/reconcile (routes/provisioning.py:116)
                    r_reconcile = client.post(
                        f"/bff/personas/{pid}/provisioning/reconcile",
                        headers=OPERATOR_HEADERS,
                    )
                    assert r_reconcile.status_code == 200, r_reconcile.text
                    assert r_reconcile.json()["data"]["routedStrategies"] == expected

                    # Caller 3: PATCH /bff/personas/{persona_id} (PersonaService.patch_persona, service.py:15795 & 15863)
                    before_patch_urls = len(count_urls)
                    r_patch = client.patch(
                        f"/bff/personas/{pid}",
                        json={"name": f"Updated {pid}"},
                        headers={**OPERATOR_HEADERS, "Idempotency-Key": f"idem-patch-{idx}"},
                    )
                    assert r_patch.status_code == 200, r_patch.text
                    assert r_patch.json()["data"]["routedStrategies"] == expected
                    assert len(count_urls) - before_patch_urls == 1

                    with request_read_scope():
                        # Caller 4: _project_persona_fleet_item (service.py:3525)
                        fleet_item = persona_service._project_persona_fleet_item(
                            raw,
                            all_runtime_bindings=[],
                            all_incidents=[],
                            all_evolution_decisions=[],
                        )
                        assert fleet_item["persona"]["routedStrategies"] == expected

                        # Caller 5: _project_persona_league_row (service.py:7165, routed_strategies=None)
                        league_row = persona_service._project_persona_league_row(
                            raw,
                            include_route_policy=False,
                            include_health=False,
                            include_allowed_actions=False,
                            routed_strategies=None,
                        )
                        assert league_row["routed_strategy_count"] == expected

                        # Caller 6: _project_persona_fleet_row_impl (service.py:9119, routed_strategies=None)
                        fleet_row = persona_service._project_persona_fleet_row_impl(
                            detail=False,
                            persona=raw,
                            league_entry={},
                            binding={},
                            runtime={},
                            active_incidents=[],
                            telemetry_summaries=[],
                            context_metadata=dict(raw.get("metadata") or {}),
                            context_persona=raw,
                            snapshot_at=_TS,
                            routed_strategies=None,
                        )
                        assert fleet_row is not None
                        assert fleet_row["routed_strategies"] == expected
            finally:
                persona_service._current_persona_service.reset(token)
    finally:
        owner_reads.authorization.reset(tok_auth)
        owner_reads.selected_tenant.reset(tok_tenant)


def test_persona_detail_registry_count_failures_preserve_exact_status_and_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC3: Registry count read failures keep the exact same status and error payload that detail routes return on full-read failures."""
    import urllib.error
    import services.control_plane.bff.command_adapters.base as adapter_base
    from services.control_plane.bff.command_adapters.base import ActionUnavailableError
    from services.control_plane.bff.command_queue import CommandStore
    from services.control_plane.bff.core import owner_reads
    from services.control_plane.bff.ports import ReadSurfacePorts, create_persona_registry_write_owner
    from services.control_plane.bff.ports.research_knowledge_source import DefaultResearchKnowledgeSourcePort

    fx = _build_hosted_registry_strategy_fixture()
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.hosted.internal")

    base_store = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={"personas": fx["raws"][:2]},
    )
    hosted_rks = DefaultResearchKnowledgeSourcePort()
    store = ReadSurfacePorts(
        operations_consultation=base_store.operations_consultation,
        persona_capital_runtime=base_store.persona_capital_runtime,
        ooda_management=base_store.ooda_management,
        research_knowledge_source=hosted_rks,
        lifecycle_telemetry_governance=base_store.lifecycle_telemetry_governance,
        persona_training=base_store.persona_training,
        job_read=base_store.job_read,
        paper_runtime_monitoring_sessions_provider=lambda: [],
        reconciliation_drift_reads=base_store.reconciliation_drift_reads,
    )
    monkeypatch.setattr(persona_service, "_PERSONA_PROVISIONING_STORE", fx["prov_store"])

    tok_auth = owner_reads.authorization.set("Bearer op-b2:operator:tenant-default")
    tok_tenant = owner_reads.selected_tenant.set("tenant-default")
    try:
        with tempfile.TemporaryDirectory() as td:
            service = PersonaService(
                write_owner=create_persona_registry_write_owner(),
                ranking_write_owner=object(),
                read_store=store,
                command_store=CommandStore(os.path.join(td, "cmd.jsonl")),
                provisioning_store=fx["prov_store"],
                bff_error_fn=_test_bff_error,
            )
            monkeypatch.setattr(service, "_source_ingest_truth_by_connector", lambda: fx["truth"])
            monkeypatch.setattr(persona_service, "_composed_persona_service", service)
            monkeypatch.setattr(persona_service, "utc_now", lambda: _TS)

            app = FastAPI()
            app.include_router(
                create_personas_router(
                    service=service,
                    extract_identity_fn=_test_extract_identity,
                    require_read_role_fn=_test_require_read_role,
                    require_operator_role_fn=_test_require_operator_role,
                    bff_error_fn=_test_bff_error,
                )
            )
            client = TestClient(app)

            failure_cases = [
                (
                    "http_401",
                    lambda url, **_k: (_ for _ in ()).throw(
                        urllib.error.HTTPError(url, 401, "Unauthorized", hdrs=None, fp=None)
                    ),
                    403,
                    "FORBIDDEN",
                    "strategy_audience_mismatch",
                ),
                (
                    "http_403",
                    lambda url, **_k: (_ for _ in ()).throw(
                        urllib.error.HTTPError(url, 403, "Forbidden", hdrs=None, fp=None)
                    ),
                    403,
                    "FORBIDDEN",
                    "strategy_audience_mismatch",
                ),
                (
                    "http_500",
                    lambda url, **_k: (_ for _ in ()).throw(
                        urllib.error.HTTPError(url, 500, "Internal Server Error", hdrs=None, fp=None)
                    ),
                    503,
                    "DEPENDENCY_UNAVAILABLE",
                    "strategy_registry_http_500",
                ),
                (
                    "http_503",
                    lambda url, **_k: (_ for _ in ()).throw(
                        urllib.error.HTTPError(url, 503, "Service Unavailable", hdrs=None, fp=None)
                    ),
                    503,
                    "DEPENDENCY_UNAVAILABLE",
                    "strategy_registry_http_503",
                ),
                (
                    "action_unavailable_403",
                    lambda _url, **_k: (_ for _ in ()).throw(
                        ActionUnavailableError("tenant denied", downstream_status=403)
                    ),
                    403,
                    "FORBIDDEN",
                    "strategy_audience_mismatch",
                ),
                (
                    "action_unavailable_503",
                    lambda _url, **_k: (_ for _ in ()).throw(
                        ActionUnavailableError("owner down", downstream_status=503)
                    ),
                    503,
                    "DEPENDENCY_UNAVAILABLE",
                    "strategy_store_unavailable",
                ),
                (
                    "runtime_conn_error",
                    lambda _url, **_k: (_ for _ in ()).throw(RuntimeError("registry unreachable")),
                    503,
                    "DEPENDENCY_UNAVAILABLE",
                    "registry unreachable",
                ),
            ]

            for label, failing_http, expected_status, expected_code, expected_reason in failure_cases:
                urls_seen: list[str] = []

                def wrapped_http(url: str, _fn=failing_http, **kw: Any) -> Any:
                    urls_seen.append(url)
                    return _fn(url, **kw)

                monkeypatch.setattr(adapter_base, "http_request_json", wrapped_http)
                resp = client.get("/bff/personas/persona-0", headers=OPERATOR_HEADERS)
                assert resp.status_code == expected_status, (label, resp.status_code, resp.text)
                err = resp.json()["detail"]["error"]
                assert err["code"] == expected_code, (label, err)
                assert err["reason"] == expected_reason, (label, err)
                assert urls_seen == [
                    "http://registry.hosted.internal/api/registry/strategy-specs?view=persona_binding_counts"
                ]

                # Compare with full-read error envelope on the exact same failure
                with pytest.raises(HTTPException) as full_exc:
                    hosted_rks._fetch_canonical_strategy_specs_uncached()
                assert resp.status_code == full_exc.value.status_code
                assert resp.json()["detail"] == full_exc.value.detail

            # Invalid count payloads fail closed with 503 DEPENDENCY_UNAVAILABLE
            for bad_payload in ([], {"persona-0": -1}, {"persona-0": True}, {"persona-0": "1"}):
                monkeypatch.setattr(adapter_base, "http_request_json", lambda _url, _p=bad_payload, **_k: _p)
                resp_bad = client.get("/bff/personas/persona-0", headers=OPERATOR_HEADERS)
                assert resp_bad.status_code == 503
                err_bad = resp_bad.json()["detail"]["error"]
                assert err_bad["code"] == "DEPENDENCY_UNAVAILABLE"
                assert err_bad["reason"] == "strategy_store_unavailable"

            # Missing caller authorization fails closed with identical 503 error
            tok_no_auth = owner_reads.authorization.set(None)
            try:
                resp_no_auth = client.get("/bff/personas/persona-0", headers=OPERATOR_HEADERS)
                assert resp_no_auth.status_code == 503
                with pytest.raises(HTTPException) as full_no_auth:
                    hosted_rks._fetch_canonical_strategy_specs_uncached()
                assert resp_no_auth.json()["detail"] == full_no_auth.value.detail
            finally:
                owner_reads.authorization.reset(tok_no_auth)
    finally:
        owner_reads.authorization.reset(tok_auth)
        owner_reads.selected_tenant.reset(tok_tenant)


def test_persona_detail_hosted_registry_url_cpu_drops_by_at_least_70_percent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4: BFF CPU for one GET /bff/personas/{id} with PANTHEON_REGISTRY_API_URL set drops by >= 70% on the same machine."""
    import time
    import services.control_plane.bff.command_adapters.base as adapter_base
    from services.control_plane.bff.command_queue import CommandStore
    from services.control_plane.bff.core import owner_reads
    from services.control_plane.bff.ports import ReadSurfacePorts, create_persona_registry_write_owner
    from services.control_plane.bff.ports.research_knowledge_source import DefaultResearchKnowledgeSourcePort

    fx = _build_hosted_registry_strategy_fixture()
    entries_json = json.dumps(fx["registry_entries"])
    counts_json = json.dumps(fx["binding_counts"])
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", "http://registry.hosted.internal")

    def registry_http(url: str, **_kw: Any) -> Any:
        if url.endswith("view=persona_binding_counts"):
            return json.loads(counts_json)
        return json.loads(entries_json)

    monkeypatch.setattr(adapter_base, "http_request_json", registry_http)

    base_store = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={"personas": fx["raws"]},
    )
    hosted_rks = DefaultResearchKnowledgeSourcePort()
    store = ReadSurfacePorts(
        operations_consultation=base_store.operations_consultation,
        persona_capital_runtime=base_store.persona_capital_runtime,
        ooda_management=base_store.ooda_management,
        research_knowledge_source=hosted_rks,
        lifecycle_telemetry_governance=base_store.lifecycle_telemetry_governance,
        persona_training=base_store.persona_training,
        job_read=base_store.job_read,
        paper_runtime_monitoring_sessions_provider=lambda: [],
        reconciliation_drift_reads=base_store.reconciliation_drift_reads,
    )
    monkeypatch.setattr(persona_service, "_PERSONA_PROVISIONING_STORE", fx["prov_store"])

    tok_auth = owner_reads.authorization.set("Bearer op-b2:operator:tenant-default")
    tok_tenant = owner_reads.selected_tenant.set("tenant-default")
    try:
        with tempfile.TemporaryDirectory() as td:
            service = PersonaService(
                write_owner=create_persona_registry_write_owner(),
                ranking_write_owner=object(),
                read_store=store,
                command_store=CommandStore(os.path.join(td, "cmd.jsonl")),
                provisioning_store=fx["prov_store"],
                bff_error_fn=_test_bff_error,
            )
            monkeypatch.setattr(service, "_source_ingest_truth_by_connector", lambda: fx["truth"])
            monkeypatch.setattr(persona_service, "_composed_persona_service", service)
            monkeypatch.setattr(persona_service, "utc_now", lambda: _TS)

            app = FastAPI()
            app.include_router(
                create_personas_router(
                    service=service,
                    extract_identity_fn=_test_extract_identity,
                    require_read_role_fn=_test_require_read_role,
                    require_operator_role_fn=_test_require_operator_role,
                    bff_error_fn=_test_bff_error,
                )
            )
            client = TestClient(app)

            def legacy_routed_strategies_for_persona(persona_id: str) -> int:
                clean_id = str(persona_id or "").strip()
                if not clean_id:
                    return 0
                items = store.list_strategy_specs(persona_id=clean_id) or []
                return len(items)

            # Warm up route & imports
            r_warm = client.get("/bff/personas/persona-0", headers=OPERATOR_HEADERS)
            assert r_warm.status_code == 200

            # Measure AFTER (bounded persona_binding_counts read on hosted registry URL path)
            t0_after = time.process_time()
            r_after = client.get("/bff/personas/persona-0", headers=OPERATOR_HEADERS)
            cpu_after = time.process_time() - t0_after
            assert r_after.status_code == 200

            # Measure BEFORE (legacy full strategy spec read on hosted registry URL path)
            orig_fn = persona_service._routed_strategies_for_persona
            import services.control_plane.bff.personas.routes.detail as detail_routes

            monkeypatch.setattr(persona_service, "_routed_strategies_for_persona", legacy_routed_strategies_for_persona)
            monkeypatch.setattr(detail_routes, "_routed_strategies_for_persona", legacy_routed_strategies_for_persona)
            try:
                t0_before = time.process_time()
                r_before = client.get("/bff/personas/persona-0", headers=OPERATOR_HEADERS)
                cpu_before = time.process_time() - t0_before
                assert r_before.status_code == 200
            finally:
                monkeypatch.setattr(persona_service, "_routed_strategies_for_persona", orig_fn)
                monkeypatch.setattr(detail_routes, "_routed_strategies_for_persona", orig_fn)

            assert json.dumps(r_after.json(), sort_keys=True) == json.dumps(r_before.json(), sort_keys=True)
            assert cpu_after <= cpu_before * 0.30, (cpu_before, cpu_after)
    finally:
        owner_reads.authorization.reset(tok_auth)
        owner_reads.selected_tenant.reset(tok_tenant)



