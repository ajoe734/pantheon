"""Tests for PAPER-TRADER-BOOK-PROVISION-20261127.

Verifies:
1. On first paper use the trader's servant persona gets exactly one paper
   CapitalPool, one active paper PersonaCapitalBinding, and one active paper
   RuntimeBinding through the existing Capital and Runtime-Manager owners;
   repeated calls are GET-first and idempotent.
2. No canary or live binding can be created from this path.
3. The starting capital comes from an operator setting
   (PANTHEON_TRADER_PAPER_STARTING_CAPITAL) and is shown with its source.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.agora.servant.router import create_servant_router
from services.control_plane.bff.auth.policy import (
    bff_error,
    extract_identity,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.models import utc_now
from services.control_plane.bff.persona_provisioning import MemoryPersonaProvisioningStore
from services.control_plane.bff.persona_provisioning_coordinator import (
    PersonaProvisioningCoordinationError,
    PersonaProvisioningCoordinator,
)
from services.control_plane.bff.personas import service as personas_service
from services.control_plane.bff.personas.routes.common import PersonaRouteContext
from services.control_plane.bff.personas.routes.provisioning import build_provisioning_router
from services.control_plane.bff.personas.service import _PersonaOwnerHttpTransport
from services.control_plane.bff.personas.trader_paper_book import (
    DEFAULT_TRADER_PAPER_STARTING_CAPITAL,
    DEFAULT_TRADER_PAPER_STARTING_CAPITAL_CURRENCY,
    TRADER_PAPER_STARTING_CAPITAL_CURRENCY_ENV,
    TRADER_PAPER_STARTING_CAPITAL_ENV,
    _resolve_dynamic_runtime_manager_auth,
    ensure_trader_paper_book,
    read_trader_paper_book,
    resolve_trader_paper_starting_capital,
    validate_paper_only_stage,
    validate_servant_persona_paper_ceiling,
)
from services.runtime_auth_inbound import encode_jwt_hs256
from services.runtime_manager.service import RuntimeManagerService


JWT_SECRET = "trader-paper-book-test-secret-20261127"
CAPITAL_ISSUER = "paper-trader-book-issuer"
CAPITAL_AUDIENCE = "paper-trader-book-capital-owner"
OWNER_BASE_URL = "http://testserver"
TENANT_ID = "tenant-alpha"
ROOT = Path(__file__).resolve().parents[4]


def _load_runtime_manager_client_class() -> Any:
    module_path = ROOT / "services" / "runtime-manager" / "runtime_manager_client.py"
    spec = importlib.util.spec_from_file_location("pantheon_runtime_manager_client_test", module_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.RuntimeManagerClient


def _mint_bearer(
    *,
    sub: str = "trader-alice",
    tenant_id: str = TENANT_ID,
    roles: Optional[List[str]] = None,
    capabilities: Optional[List[str]] = None,
) -> str:
    claims: Dict[str, Any] = {
        "sub": sub,
        "actor_id": sub,
        "user_id": sub,
        "agora_user_id": sub,
        "tenant_id": tenant_id,
        "roles": roles or ["operator"],
        "capabilities": capabilities
        or [
            "agora.servant.v1",
            "agora.session.v1",
            "agora.trading.v1",
        ],
        "iss": "pantheon-bff",
        "aud": "pantheon-services",
        "exp": 4102444800,
    }
    return "Bearer " + encode_jwt_hs256(claims, secret=JWT_SECRET)


class _InMemoryPersonaWriteOwner:
    """Test-scoped Persona write owner supporting servant creation & updates."""

    def __init__(self) -> None:
        self.personas: Dict[str, Dict[str, Any]] = {}
        self.capability_snapshots: Dict[str, Dict[str, Any]] = {}

    def get_persona(self, persona_id: str) -> Optional[Dict[str, Any]]:
        record = self.personas.get(persona_id)
        return json.loads(json.dumps(record)) if record is not None else None

    def list_personas(self) -> List[Dict[str, Any]]:
        return [json.loads(json.dumps(item)) for item in self.personas.values()]

    def create_persona(self, **kwargs: Any) -> Dict[str, Any]:
        persona_id = str(kwargs["persona_id"])
        record = {
            "id": persona_id,
            "persona_id": persona_id,
            "name": kwargs.get("name") or "Agora Servant",
            "archetype": kwargs.get("archetype") or "agora_servant",
            "lifecycle_state": kwargs.get("lifecycle_state") or "draft",
            "status": kwargs.get("lifecycle_state") or "draft",
            "risk_level": kwargs.get("risk_level") or "low",
            "mandate": kwargs.get("mandate") or "user_private_agora_servant",
            "strategy_family": kwargs.get("strategy_family") or "agora_servant",
            "traits": dict(kwargs.get("traits") or {}),
            "metadata": dict(kwargs.get("metadata") or {}),
            "created_at": kwargs.get("created_at"),
            "updated_at": kwargs.get("created_at"),
        }
        self.personas[persona_id] = record
        return json.loads(json.dumps(record))

    def update_persona(self, persona_id: str, **kwargs: Any) -> Dict[str, Any]:
        record = dict(self.personas.get(persona_id) or {"id": persona_id, "persona_id": persona_id})
        if kwargs.get("name") is not None:
            record["name"] = kwargs["name"]
        if kwargs.get("lifecycle_state") is not None:
            record["lifecycle_state"] = kwargs["lifecycle_state"]
            record["status"] = kwargs["lifecycle_state"]
        if kwargs.get("risk_level") is not None:
            record["risk_level"] = kwargs["risk_level"]
        if kwargs.get("metadata") is not None:
            record["metadata"] = dict(kwargs["metadata"])
        if kwargs.get("updated_at") is not None:
            record["updated_at"] = kwargs["updated_at"]
        self.personas[persona_id] = record
        return json.loads(json.dumps(record))

    def upsert_persona_capability_snapshot(self, **kwargs: Any) -> Dict[str, Any]:
        snapshot_id = str(kwargs["snapshot_id"])
        record = dict(kwargs)
        self.capability_snapshots[snapshot_id] = record
        return json.loads(json.dumps(record))


@pytest.fixture
def owner_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Wire real Capital FastAPI owner and real RuntimeManagerService with isolated stores."""
    capital_dir = tmp_path / "capital"
    capital_dir.mkdir(parents=True, exist_ok=True)
    rm_store_path = tmp_path / "runtime_manager" / "bindings.json"
    rm_store_path.parent.mkdir(parents=True, exist_ok=True)

    for name, value in {
        "PANTHEON_BFF_AUTH_MODE": "strict",
        "PANTHEON_BFF_JWT_SECRET": JWT_SECRET,
        "PANTHEON_BFF_JWT_ISSUER": "pantheon-bff",
        "PANTHEON_BFF_JWT_AUDIENCE": "pantheon-services",
        "PANTHEON_BFF_TENANT_ID": TENANT_ID,
        "CAPITAL_DATA_DIR": str(capital_dir),
        "PANTHEON_GOVERNANCE_DATA_DIR": str(capital_dir),
        "CAPITAL_STORE_BACKEND": "json",
        "CAPITAL_AUDIT_BACKEND": "jsonl",
        "CAPITAL_AUTH_DISABLED": "false",
        "CAPITAL_AUTH_MODE": "strict",
        "CAPITAL_JWT_SECRET": JWT_SECRET,
        "CAPITAL_JWT_ISSUER": CAPITAL_ISSUER,
        "CAPITAL_JWT_AUDIENCE": CAPITAL_AUDIENCE,
        "CAPITAL_ALLOWED_CALLER_SERVICES": "control-plane-bff",
        "PANTHEON_CAPITAL_API_URL": OWNER_BASE_URL,
        "PANTHEON_CAPITAL_JWT_SECRET": JWT_SECRET,
        "PANTHEON_CAPITAL_JWT_ISSUER": CAPITAL_ISSUER,
        "PANTHEON_CAPITAL_JWT_AUDIENCE": CAPITAL_AUDIENCE,
        "PANTHEON_RUNTIME_BINDING_STORE_PATH": str(rm_store_path),
        "PANTHEON_SINGLE_RUNTIME_ENFORCED": "true",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("PANTHEON_BFF_AUTH_STUB", raising=False)
    monkeypatch.delenv("PANTHEON_RUNTIME_MANAGER_URL", raising=False)
    monkeypatch.delenv(TRADER_PAPER_STARTING_CAPITAL_ENV, raising=False)
    monkeypatch.delenv(TRADER_PAPER_STARTING_CAPITAL_CURRENCY_ENV, raising=False)

    sys.modules.pop("services.capital.main", None)
    capital_module = importlib.reload(importlib.import_module("services.capital.main"))
    capital_client = TestClient(capital_module.app)

    capital_calls: List[Tuple[str, str, Optional[Dict[str, Any]], int]] = []

    def _fake_urlopen(request: Any, timeout: Any = None) -> io.BytesIO:
        payload = json.loads(request.data) if request.data else None
        response = capital_client.request(
            request.get_method(),
            request.full_url,
            content=request.data,
            headers=dict(request.header_items()),
        )
        capital_calls.append(
            (request.get_method().upper(), request.full_url, payload, response.status_code)
        )
        if response.is_error:
            raise HTTPError(
                request.full_url,
                response.status_code,
                response.text,
                response.headers,  # type: ignore[arg-type]
                io.BytesIO(response.content),
            )
        return io.BytesIO(response.content)

    monkeypatch.setattr(personas_service.urllib_request, "urlopen", _fake_urlopen)

    rm_client_cls = _load_runtime_manager_client_class()
    rm_client = rm_client_cls(allow_local=True)
    rm_service: RuntimeManagerService = rm_client._local()

    provisioning_store = MemoryPersonaProvisioningStore()
    persona_owner = _InMemoryPersonaWriteOwner()

    def _coordinator_for_tenant(tenant_id: str) -> PersonaProvisioningCoordinator:
        return PersonaProvisioningCoordinator(
            store=provisioning_store,
            transport=_PersonaOwnerHttpTransport(tenant_id=tenant_id),
            schedule_registrar=lambda *_a, **_kw: {},
            lease_owner="trader-paper-book-test-worker",
            actor_id=personas_service.PERSONA_OWNER_SERVICE_ACTOR_ID,
        )

    monkeypatch.setattr(personas_service, "_chain_paper_capital_coordinator", _coordinator_for_tenant)
    monkeypatch.setattr(personas_service, "_runtime_manager_client", lambda: rm_client)

    app = FastAPI()
    servant_router = create_servant_router(
        extract_identity=extract_identity,
        require_read_role=require_read_role,
        require_write_role=require_operator_role,
        bff_error=bff_error,
        utc_now=utc_now,
        get_read_store=lambda: persona_owner,
        get_persona_write_owner=lambda: persona_owner,
        sync_servant_agent=lambda persona: {
            "status": "ready",
            "agent_id": f"oc-{persona.get('persona_id')}",
            "model_id": "gpt-5.4-mini",
            "model": "gpt-5.4-mini",
            "workspace_ref": f"ws://{persona.get('persona_id')}",
        },
        ensure_paper_book=lambda **kwargs: ensure_trader_paper_book(
            runtime_client=rm_client,
            **kwargs,
        ),
        read_paper_book=lambda **kwargs: read_trader_paper_book(
            runtime_client=rm_client,
            **kwargs,
        ),
    )
    app.include_router(servant_router)

    class _DirectorySnapshot:
        def __init__(self, records_by_id: Dict[str, Dict[str, Any]]) -> None:
            self.records_by_id = records_by_id

    monkeypatch.setattr(
        "services.control_plane.bff.personas.routes.provisioning._get_persona_directory_snapshot",
        lambda _tenant: _DirectorySnapshot(dict(persona_owner.personas)),
    )
    monkeypatch.setattr(
        "services.control_plane.bff.personas.routes.provisioning._bff_me_tenant_payload",
        lambda identity, requested_tenant=None: {
            "id": str(getattr(identity, "tenant_id", None) or requested_tenant or TENANT_ID)
        },
    )

    route_ctx = PersonaRouteContext(
        service=persona_owner,
        read_store=persona_owner,
        command_store=persona_owner,
        ranking_write_owner=persona_owner,
        write_owner=persona_owner,
        extract_identity=extract_identity,
        require_read_role=require_read_role,
        require_operator_role=require_operator_role,
        bff_error=bff_error,
        utc_now=utc_now,
        page_slice=lambda items, _p, _s: (items, len(items)),
        snapshot_meta=lambda: {"snapshot_at": utc_now()},
        dataset_surface_status=lambda *_a, **_kw: "ok",
        read_surface_meta=lambda *_a, **_kw: {},
        raise_if_read_surface_unavailable=lambda *_a, **_kw: None,
        reject_body_idempotency_key=lambda *_a, **_kw: None,
        resolve_final_idempotency_key=lambda *_a, **_kw: "idem",
    )
    app.include_router(build_provisioning_router(route_ctx))

    try:
        yield {
            "client": TestClient(app),
            "capital_module": capital_module,
            "capital_dir": capital_dir,
            "rm_service": rm_service,
            "rm_client": rm_client,
            "persona_owner": persona_owner,
            "capital_calls": capital_calls,
            "coordinator_for_tenant": _coordinator_for_tenant,
        }
    finally:
        capital_client.close()
        sys.modules.pop("services.capital.main", None)


def _reload_capital_pools(capital_dir: Path) -> Dict[str, Any]:
    from services.capital.pg_store import build_capital_pool_store

    store = build_capital_pool_store(capital_dir / "capital_pools.json")
    return {pool.pool_id: pool for pool in store.list()}


def _reload_capital_bindings(capital_dir: Path) -> Dict[str, Any]:
    from services.capital.pg_store import build_capital_binding_store

    store = build_capital_binding_store(capital_dir / "persona_capital_bindings.json")
    return {binding.binding_id: binding for binding in store.list()}


def test_first_paper_use_provisions_one_paper_pool_and_one_paper_binding_idempotently(
    owner_stack: Dict[str, Any],
) -> None:
    client: TestClient = owner_stack["client"]
    capital_dir: Path = owner_stack["capital_dir"]
    rm_service: RuntimeManagerService = owner_stack["rm_service"]
    capital_calls: List[Tuple[str, str, Optional[Dict[str, Any]], int]] = owner_stack["capital_calls"]

    alice_headers = {
        "Authorization": _mint_bearer(sub="trader-alice", tenant_id=TENANT_ID),
        "Idempotency-Key": "idem-alice-paper-book-1",
        "X-Request-Id": "req-alice-paper-book-1",
        "X-Tenant-Id": TENANT_ID,
    }

    # Before first paper use, GET /bff/agora/servant/paper-book returns 404.
    before = client.get("/bff/agora/servant/paper-book", headers=alice_headers)
    assert before.status_code == 404, before.text

    capital_calls.clear()
    first = client.post("/bff/agora/servant/paper-book/ensure", headers=alice_headers, json={})
    assert first.status_code == 200, first.text
    first_data = first.json()["data"]

    assert first_data["status"] == "provisioned"
    assert first_data["tenant_id"] == TENANT_ID
    assert first_data["agora_user_id"] == "trader-alice"
    assert first_data["stage"] == "paper"
    assert first_data["deployment_stage"] == "paper"
    assert first_data["environment_ceiling"] == "paper"
    assert first_data["notional_only"] is True
    assert first_data["idempotent_replay"] is False
    assert first_data["capital_pool"]["created"] is True
    assert first_data["capital_pool"]["stage"] == "paper"
    assert first_data["capital_pool"]["notional_only"] is True
    assert first_data["persona_capital_binding"]["created"] is True
    assert first_data["persona_capital_binding"]["stage"] == "paper"
    assert first_data["persona_capital_binding"]["status"] == "active"
    assert first_data["runtime_binding"]["created"] is True
    assert first_data["runtime_binding"]["allowed_deployment_scope"] == "paper"
    assert first_data["runtime_binding"]["status"] == "active"
    assert first_data["runtime_binding"]["execution_mode"] == "paper"

    persona_id = first_data["persona_id"]
    pool_id = first_data["capital_pool_id"]
    capital_binding_id = first_data["persona_capital_binding_id"]
    runtime_binding_id = first_data["runtime_binding_id"]

    # Real Capital and Runtime-Manager owners hold exactly one paper pool and one paper binding.
    pools = _reload_capital_pools(capital_dir)
    assert list(pools.keys()) == [pool_id]
    assert pools[pool_id].status == "active"
    assert pools[pool_id].single_runtime_enforced is True
    assert pools[pool_id].metadata["execution_context"] == "paper"
    assert pools[pool_id].metadata["persona_id"] == persona_id

    cap_bindings = _reload_capital_bindings(capital_dir)
    assert list(cap_bindings.keys()) == [capital_binding_id]
    assert cap_bindings[capital_binding_id].capital_pool_id == pool_id
    assert cap_bindings[capital_binding_id].persona_id == persona_id
    assert str(cap_bindings[capital_binding_id].allowed_deployment_scope) == "paper"
    assert str(cap_bindings[capital_binding_id].status) == "active"

    rm_bindings = rm_service.find_bindings_for_pool(pool_id)
    assert len(rm_bindings) == 1
    assert rm_bindings[0].binding_id == runtime_binding_id
    assert str(getattr(rm_bindings[0].deployment_mode, "value", rm_bindings[0].deployment_mode)) == "paper"
    assert str(getattr(rm_bindings[0].status, "value", rm_bindings[0].status)) == "active"
    assert rm_bindings[0].capital_pool_id == pool_id
    assert rm_bindings[0].persona_capital_binding_id == capital_binding_id

    # Second call is GET-first and idempotent: zero new POST writes, same IDs returned.
    capital_calls.clear()
    second = client.post(
        "/bff/agora/servant/paper-book/ensure",
        headers={**alice_headers, "Idempotency-Key": "idem-alice-paper-book-2"},
        json={},
    )
    assert second.status_code == 200, second.text
    second_data = second.json()["data"]
    assert second_data["idempotent_replay"] is True
    assert second_data["capital_pool_id"] == pool_id
    assert second_data["persona_capital_binding_id"] == capital_binding_id
    assert second_data["runtime_binding_id"] == runtime_binding_id
    assert second_data["capital_pool"]["created"] is False
    assert second_data["persona_capital_binding"]["created"] is False
    assert second_data["runtime_binding"]["created"] is False
    assert not any(method != "GET" for method, _url, _payload, _status in capital_calls)

    # Third call via POST /bff/personas/{persona_id}/provisioning/paper-book is also idempotent.
    third = client.post(
        f"/bff/personas/{persona_id}/provisioning/paper-book",
        headers=alice_headers,
        json={"target_stage": "paper"},
    )
    assert third.status_code == 200, third.text
    third_book = third.json()["data"]
    assert third_book["idempotent_replay"] is True
    assert third_book["capital_pool_id"] == pool_id
    assert third_book["persona_capital_binding_id"] == capital_binding_id
    assert third_book["runtime_binding_id"] == runtime_binding_id

    # Still exactly 1 pool, 1 capital binding, 1 runtime binding in the real owners.
    assert len(_reload_capital_pools(capital_dir)) == 1
    assert len(_reload_capital_bindings(capital_dir)) == 1
    assert len(rm_service.find_bindings_for_pool(pool_id)) == 1

    # GET /bff/agora/servant/paper-book reads back the caller's paper book.
    readback = client.get("/bff/agora/servant/paper-book", headers=alice_headers)
    assert readback.status_code == 200, readback.text
    readback_data = readback.json()["data"]
    assert readback_data["capital_pool_id"] == pool_id
    assert readback_data["persona_capital_binding_id"] == capital_binding_id
    assert readback_data["runtime_binding_id"] == runtime_binding_id

    # Another trader in the same tenant does not share Alice's paper book and gets an isolated book.
    bob_headers = {
        "Authorization": _mint_bearer(sub="trader-bob", tenant_id=TENANT_ID),
        "Idempotency-Key": "idem-bob-paper-book-1",
        "X-Request-Id": "req-bob-paper-book-1",
        "X-Tenant-Id": TENANT_ID,
    }
    bob_before = client.get("/bff/agora/servant/paper-book", headers=bob_headers)
    assert bob_before.status_code == 404, bob_before.text

    bob_first = client.post("/bff/agora/servant/paper-book/ensure", headers=bob_headers, json={})
    assert bob_first.status_code == 200, bob_first.text
    bob_data = bob_first.json()["data"]
    assert bob_data["persona_id"] != persona_id
    assert bob_data["capital_pool_id"] != pool_id
    assert bob_data["persona_capital_binding_id"] != capital_binding_id
    assert bob_data["runtime_binding_id"] != runtime_binding_id


@pytest.mark.parametrize(
    "forbidden_payload",
    [
        {"target_stage": "canary"},
        {"target_stage": "live"},
        {"target_stage": "shadow"},
        {"stage": "canary"},
        {"stage": "live"},
        {"deployment_stage": "canary"},
        {"deployment_stage": "live"},
        {"allowed_deployment_scope": "canary"},
        {"allowed_deployment_scope": "live"},
        {"environment_ceiling": "canary"},
        {"environment_ceiling": "live"},
        {"execution_mode": "canary"},
        {"execution_mode": "live"},
    ],
)
def test_no_canary_or_live_binding_can_be_created_from_trader_paper_book_path(
    owner_stack: Dict[str, Any],
    forbidden_payload: Dict[str, Any],
) -> None:
    client: TestClient = owner_stack["client"]
    capital_dir: Path = owner_stack["capital_dir"]
    rm_service: RuntimeManagerService = owner_stack["rm_service"]
    persona_owner: _InMemoryPersonaWriteOwner = owner_stack["persona_owner"]
    capital_calls: List[Tuple[str, str, Optional[Dict[str, Any]], int]] = owner_stack["capital_calls"]

    with pytest.raises(PersonaProvisioningCoordinationError, match="PAPER_ONLY_TRADER_BOOK_STAGE_VIOLATION"):
        validate_paper_only_stage(**forbidden_payload)

    persona_owner.create_persona(
        persona_id="agora-servant-forbidden",
        name="Agora Servant",
        created_at="2026-11-27T00:00:00Z",
        metadata={
            "tenant_id": TENANT_ID,
            "agora_user_id": "trader-canary-attempt",
            "persona_class": "agora_servant",
            "environment_ceiling": "paper",
            "deployment_stage": "paper",
        },
    )

    headers = {
        "Authorization": _mint_bearer(sub="trader-canary-attempt", tenant_id=TENANT_ID),
        "Idempotency-Key": "idem-forbidden-stage",
        "X-Request-Id": "req-forbidden-stage",
        "X-Tenant-Id": TENANT_ID,
    }
    capital_calls.clear()
    servant_res = client.post(
        "/bff/agora/servant/paper-book/ensure",
        headers=headers,
        json=forbidden_payload,
    )
    assert servant_res.status_code == 409, servant_res.text
    assert "PAPER_ONLY_TRADER_BOOK_STAGE_VIOLATION" in servant_res.text

    if "environment_ceiling" not in forbidden_payload:
        persona_res = client.post(
            "/bff/personas/agora-servant-forbidden/provisioning/paper-book",
            headers=headers,
            json=forbidden_payload,
        )
        assert persona_res.status_code == 409, persona_res.text
        assert "PAPER_ONLY_TRADER_BOOK_STAGE_VIOLATION" in persona_res.text

    # Zero calls or writes to Capital or Runtime-Manager owners.
    assert capital_calls == []
    assert _reload_capital_pools(capital_dir) == {}
    assert rm_service.list_bindings() == []


@pytest.mark.parametrize(
    "bad_persona_meta",
    [
        {"environment_ceiling": "canary"},
        {"environment_ceiling": "live"},
        {"deployment_stage": "canary"},
        {"deployment_stage": "live"},
        {"live_capital_enabled": True},
    ],
)
def test_persona_with_non_paper_ceiling_is_rejected_before_any_owner_write(
    owner_stack: Dict[str, Any],
    bad_persona_meta: Dict[str, Any],
) -> None:
    capital_dir: Path = owner_stack["capital_dir"]
    rm_service: RuntimeManagerService = owner_stack["rm_service"]
    rm_client: Any = owner_stack["rm_client"]

    bad_record = {
        "persona_id": "agora-servant-bad-ceiling",
        "metadata": {
            "tenant_id": TENANT_ID,
            "persona_class": "agora_servant",
            "environment_ceiling": "paper",
            "deployment_stage": "paper",
            **bad_persona_meta,
        },
    }
    with pytest.raises(PersonaProvisioningCoordinationError, match="PAPER_ONLY_SERVANT_CEILING_VIOLATION"):
        validate_servant_persona_paper_ceiling(bad_record)

    with pytest.raises(PersonaProvisioningCoordinationError, match="PAPER_ONLY_SERVANT_CEILING_VIOLATION"):
        ensure_trader_paper_book(
            TENANT_ID,
            "agora-servant-bad-ceiling",
            requested_by="trader-alice",
            persona_record=bad_record,
            runtime_client=rm_client,
        )

    assert _reload_capital_pools(capital_dir) == {}
    assert rm_service.list_bindings() == []


def test_starting_capital_comes_from_operator_setting_and_shows_source(
    owner_stack: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client: TestClient = owner_stack["client"]
    capital_dir: Path = owner_stack["capital_dir"]
    rm_service: RuntimeManagerService = owner_stack["rm_service"]

    # 1. Default operator setting (unset env) uses DEFAULT_TRADER_PAPER_STARTING_CAPITAL and reports source.
    default_cap = resolve_trader_paper_starting_capital(as_of="2026-11-27T00:00:00Z")
    assert default_cap["amount"] == DEFAULT_TRADER_PAPER_STARTING_CAPITAL == 10_000_000.0
    assert default_cap["currency"] == DEFAULT_TRADER_PAPER_STARTING_CAPITAL_CURRENCY == "TWD"
    assert default_cap["source"] == "operator_setting_default"
    assert default_cap["source_ref"] == f"default:{TRADER_PAPER_STARTING_CAPITAL_ENV}"
    assert default_cap["setting_name"] == TRADER_PAPER_STARTING_CAPITAL_ENV
    assert default_cap["configured"] is False
    assert default_cap["notional_only"] is True
    assert default_cap["execution_context"] == "paper"

    # 2. Operator setting override via environment variable is reflected in the pool, bindings, and source metadata.
    monkeypatch.setenv(TRADER_PAPER_STARTING_CAPITAL_ENV, "25000000")
    monkeypatch.setenv(TRADER_PAPER_STARTING_CAPITAL_CURRENCY_ENV, "USD")

    configured_cap = resolve_trader_paper_starting_capital(as_of="2026-11-27T00:00:00Z")
    assert configured_cap["amount"] == 25_000_000.0
    assert configured_cap["currency"] == "USD"
    assert configured_cap["source"] == "operator_env"
    assert configured_cap["source_ref"] == f"env:{TRADER_PAPER_STARTING_CAPITAL_ENV}"
    assert configured_cap["currency_source"] == "operator_env"
    assert configured_cap["currency_source_ref"] == f"env:{TRADER_PAPER_STARTING_CAPITAL_CURRENCY_ENV}"
    assert configured_cap["configured"] is True

    headers = {
        "Authorization": _mint_bearer(sub="trader-configured", tenant_id=TENANT_ID),
        "Idempotency-Key": "idem-configured-cap-1",
        "X-Request-Id": "req-configured-cap-1",
        "X-Tenant-Id": TENANT_ID,
    }
    res = client.post("/bff/agora/servant/paper-book/ensure", headers=headers, json={})
    assert res.status_code == 200, res.text
    book = res.json()["data"]
    assert book["starting_capital"]["amount"] == 25_000_000.0
    assert book["starting_capital"]["currency"] == "USD"
    assert book["starting_capital"]["source"] == "operator_env"
    assert book["starting_capital"]["source_ref"] == f"env:{TRADER_PAPER_STARTING_CAPITAL_ENV}"
    assert book["starting_capital"]["setting_name"] == TRADER_PAPER_STARTING_CAPITAL_ENV
    assert book["starting_capital"]["configured"] is True

    pools = _reload_capital_pools(capital_dir)
    pool = pools[book["capital_pool_id"]]
    assert pool.budget == 25_000_000.0
    assert pool.currency == "USD"

    rm_binding = rm_service.get_binding(book["runtime_binding_id"])
    assert rm_binding is not None
    assert rm_binding.metadata["starting_capital"]["amount"] == 25_000_000.0
    assert rm_binding.metadata["starting_capital"]["source"] == "operator_env"

    # Also verify POST /bff/agora/servant/ensure exposes starting_capital and paper_book in profile metadata.
    ensure_res = client.post("/bff/agora/servant/ensure", headers=headers)
    assert ensure_res.status_code == 200, ensure_res.text
    profile_meta = ensure_res.json()["data"]["metadata"]
    assert profile_meta["starting_capital"]["amount"] == 25_000_000.0
    assert profile_meta["starting_capital"]["source"] == "operator_env"
    assert profile_meta["paper_book"]["capital_pool_id"] == book["capital_pool_id"]
    assert profile_meta["paper_book"]["runtime_binding_id"] == book["runtime_binding_id"]

    # 3. Invalid operator setting values fail closed.
    for invalid_value in ("0", "-1000", "nan", "inf", "not-a-number"):
        monkeypatch.setenv(TRADER_PAPER_STARTING_CAPITAL_ENV, invalid_value)
        with pytest.raises(PersonaProvisioningCoordinationError, match="INVALID_TRADER_PAPER_STARTING_CAPITAL"):
            resolve_trader_paper_starting_capital()


def test_dynamic_runtime_manager_bearer_auth_mints_jwt_in_strict_mode() -> None:
    auth = _resolve_dynamic_runtime_manager_auth(
        tenant_id=TENANT_ID,
        actor_id="control-plane-bff",
        env={
            "PANTHEON_RUNTIME_AUTH_MODE": "strict",
            "PANTHEON_BFF_JWT_SECRET": JWT_SECRET,
            "PANTHEON_BFF_JWT_ISSUER": "pantheon-bff",
            "PANTHEON_BFF_JWT_AUDIENCE": "pantheon-services",
        },
    )
    assert auth.source == "jwt_mint"
    assert auth.used_default is False
    assert auth.token.count(".") == 2


def test_v1_14_contract_bundle_records_servant_paper_book_routes() -> None:
    bundle_path = ROOT / "services/control-plane/specs/agora/bundle_index.v1_14.json"
    cap_path = ROOT / "services/control-plane/specs/agora/v15/capability_manifest_v1_14.json"
    openapi_path = ROOT / "services/control-plane/openapi/agora_v1_14.openapi.yaml"

    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    cap = json.loads(cap_path.read_text(encoding="utf-8"))
    openapi = json.loads(openapi_path.read_text(encoding="utf-8"))

    assert (
        bundle["files"]["specs/agora/v15/capability_manifest_v1_14.json"]
        == hashlib.sha256(cap_path.read_bytes()).hexdigest()
    )
    assert (
        bundle["openapi"]["sha256"]
        == hashlib.sha256(openapi_path.read_bytes()).hexdigest()
    )
    assert bundle["route_families"]["agora.servant.paper-book.owner.v1"] == [
        "GET /bff/agora/servant/paper-book",
        "POST /bff/agora/servant/paper-book/ensure",
    ]
    assert "/bff/agora/servant/paper-book" in openapi["paths"]
    assert "/bff/agora/servant/paper-book/ensure" in openapi["paths"]
    manifest_paths = {(entry["method"], entry["path"]) for entry in cap["routes"]}
    assert ("GET", "/bff/agora/servant/paper-book") in manifest_paths
    assert ("POST", "/bff/agora/servant/paper-book/ensure") in manifest_paths


def _load_fleet_desired_state_builder() -> Any:
    module_path = ROOT / "services" / "runtime-manager" / "fleet_desired_state.py"
    spec = importlib.util.spec_from_file_location(
        "pantheon_fleet_desired_state_test", module_path
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.build_fleet_desired_state


def test_provisioned_trader_paper_book_is_listed_in_runtime_fleet_desired_state_bindings(
    owner_stack: Dict[str, Any],
) -> None:
    """AC1 (PAPER-TRADER-BOOK-EXECUTABLE-INTENT-ONLY-20261127):
    A trader servant paper book RuntimeBinding (metadata.book_kind == 'trader_servant_paper_book')
    carries the StagePlanner.build_execution_projection artifact projection and is listed in
    runtime-manager desired-state bindings (not in excluded).
    """
    from services.execution.artifact_loader import ArtifactLoader

    client: TestClient = owner_stack["client"]
    rm_service: RuntimeManagerService = owner_stack["rm_service"]
    build_fleet_desired_state = _load_fleet_desired_state_builder()

    headers = {
        "Authorization": _mint_bearer(sub="trader-desired-state", tenant_id=TENANT_ID),
        "Idempotency-Key": "idem-desired-state-1",
        "X-Request-Id": "req-desired-state-1",
        "X-Tenant-Id": TENANT_ID,
    }
    res = client.post("/bff/agora/servant/paper-book/ensure", headers=headers, json={})
    assert res.status_code == 200, res.text
    book = res.json()["data"]
    rb_id = book["runtime_binding_id"]

    stored_binding = rm_service.get_binding(rb_id)
    assert stored_binding is not None
    assert stored_binding.metadata["book_kind"] == "trader_servant_paper_book"
    assert stored_binding.metadata["symbol"] == "2330.TW"
    assert isinstance(stored_binding.metadata.get("object_store"), dict)
    assert isinstance(stored_binding.metadata.get("market_data_policy"), dict)
    assert stored_binding.metadata["market_data_policy"]["owner"] == "source-ingest"

    loaded = ArtifactLoader(stored_binding.metadata["object_store"]).load_exact(
        registry_id=book["strategy_artifact_id"],
        strategy_id=book["strategy_id"],
        version=book["strategy_artifact_version"],
        execution_mode="paper",
        expected_checksum=stored_binding.metadata["artifact_checksum"],
    )
    assert loaded.metadata["registry_id"] == book["strategy_artifact_id"]

    desired = build_fleet_desired_state(
        [b.to_dict() for b in rm_service.list_all()], stage_filter="paper"
    )
    assert desired.active_count == 1
    assert desired.excluded_count == 0
    assert [b.binding_id for b in desired.bindings] == [rb_id]
    assert desired.excluded == []


def test_existing_hosted_trader_paper_book_without_execution_projection_is_repaired_in_place_on_same_binding_id(
    owner_stack: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2 (PAPER-TRADER-BOOK-EXECUTABLE-INTENT-ONLY-20261127):
    The existing hosted book (servant agora-servant-799a3ce8c021b91cbb54,
    pool-persona-paper-3ca8da543bef6ede0a07, pcb-persona-paper-3ca8da543bef6ede0a07,
    rb-77d962be4e324032808324109f2c154e, rt-persona-paper-3ca8da543bef6ede0a07) is repaired in
    place: ensure_trader_paper_book on an existing book whose binding lacks the projection fills
    it on the same RuntimeBinding id and creates no second pool, capital binding, or RuntimeBinding.
    """
    from services.control_plane.bff.personas.trader_paper_book import (
        trader_paper_book_ids,
        trader_paper_runtime_id,
    )
    from services.execution.artifact_loader import ArtifactLoader
    from services.runtime_manager.runtime_binding import (
        DeploymentMode,
        RuntimeBinding,
        RuntimeBindingStatus,
    )

    hosted_tenant = "tenant-dev"
    hosted_persona = "agora-servant-799a3ce8c021b91cbb54"
    hosted_binding_id = "rb-77d962be4e324032808324109f2c154e"
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", hosted_tenant)

    capital_dir: Path = owner_stack["capital_dir"]
    rm_service: RuntimeManagerService = owner_stack["rm_service"]
    rm_client: Any = owner_stack["rm_client"]
    coordinator_for_tenant = owner_stack["coordinator_for_tenant"]
    build_fleet_desired_state = _load_fleet_desired_state_builder()

    ids = trader_paper_book_ids(hosted_tenant, hosted_persona)
    assert ids.capital_pool_id == "pool-persona-paper-3ca8da543bef6ede0a07"
    assert ids.persona_capital_binding_id == "pcb-persona-paper-3ca8da543bef6ede0a07"
    assert trader_paper_runtime_id(ids) == "rt-persona-paper-3ca8da543bef6ede0a07"

    coordinator = coordinator_for_tenant(hosted_tenant)
    coordinator.ensure_chain_paper_capital(
        tenant_id=hosted_tenant,
        persona_id=hosted_persona,
        capital_pool_id=ids.capital_pool_id,
        binding_id=ids.persona_capital_binding_id,
        requested_by="agora-trader-1",
        currency="TWD",
        name=f"{hosted_persona} trader paper pool",
        budget=10_000_000.0,
        mandate="user_private_agora_servant_paper_book",
        labels={"task_id": "PAPER-TRADER-BOOK-PROVISION-20261127", "paper_only": True},
    )

    # Seed the exact pre-fix hosted RuntimeBinding (active, lacking object_store/symbol/market_data_policy).
    legacy_hosted_binding = RuntimeBinding(
        binding_id=hosted_binding_id,
        runtime_id=trader_paper_runtime_id(ids),
        capital_pool_id=ids.capital_pool_id,
        plan_id=ids.deployment_plan_id,
        artifact_id=ids.strategy_artifact_id,
        artifact_version=ids.version,
        deployment_mode=DeploymentMode.PAPER.value,
        status=RuntimeBindingStatus.ACTIVE.value,
        effective_at="2026-11-27T09:42:01Z",
        persona_capital_binding_id=ids.persona_capital_binding_id,
        execution_mode="paper",
        metadata={
            "tenant_id": hosted_tenant,
            "persona_id": hosted_persona,
            "sponsor_persona_id": hosted_persona,
            "strategy_id": ids.strategy_id,
            "execution_context": "paper",
            "paper_only": True,
            "book_kind": "trader_servant_paper_book",
            "requested_by": "agora-trader-1",
        },
    )
    rm_service._store.create(legacy_hosted_binding)

    # Before repair: excluded from desired-state with non_executable_missing_artifact_store.
    before_desired = build_fleet_desired_state(
        [b.to_dict() for b in rm_service.list_all()], stage_filter="paper"
    )
    assert before_desired.active_count == 0
    assert before_desired.excluded_count == 1
    assert before_desired.excluded[0].binding_id == hosted_binding_id
    assert (
        before_desired.excluded[0].exclusion_reason
        == "non_executable_missing_artifact_store"
    )

    # Call ensure_trader_paper_book to repair the existing book in place.
    repaired = ensure_trader_paper_book(
        hosted_tenant,
        hosted_persona,
        requested_by="agora-trader-1",
        agora_user_id="agora-trader-1",
        coordinator=coordinator,
        runtime_client=rm_client,
    )
    assert repaired["capital_pool_id"] == "pool-persona-paper-3ca8da543bef6ede0a07"
    assert (
        repaired["persona_capital_binding_id"]
        == "pcb-persona-paper-3ca8da543bef6ede0a07"
    )
    assert repaired["runtime_binding_id"] == hosted_binding_id
    assert repaired["runtime_id"] == "rt-persona-paper-3ca8da543bef6ede0a07"
    assert repaired["created_capital_pool"] is False
    assert repaired["created_runtime_binding"] is False
    assert repaired["idempotent_replay"] is True
    assert repaired["runtime_binding"]["created"] is False

    # No second pool, capital binding, or RuntimeBinding was created.
    assert list(_reload_capital_pools(capital_dir).keys()) == [
        "pool-persona-paper-3ca8da543bef6ede0a07"
    ]
    assert list(_reload_capital_bindings(capital_dir).keys()) == [
        "pcb-persona-paper-3ca8da543bef6ede0a07"
    ]
    pool_rm_bindings = rm_service.find_bindings_for_pool(
        "pool-persona-paper-3ca8da543bef6ede0a07"
    )
    assert len(pool_rm_bindings) == 1
    assert pool_rm_bindings[0].binding_id == hosted_binding_id
    assert pool_rm_bindings[0].status == "active"
    assert pool_rm_bindings[0].metadata["symbol"] == "2330.TW"

    # After repair: rb-77d962be4e324032808324109f2c154e is in desired-state bindings and not excluded.
    after_desired = build_fleet_desired_state(
        [b.to_dict() for b in rm_service.list_all()], stage_filter="paper"
    )
    assert after_desired.active_count == 1
    assert after_desired.excluded_count == 0
    assert [b.binding_id for b in after_desired.bindings] == [hosted_binding_id]
    assert after_desired.excluded == []

    loaded = ArtifactLoader(pool_rm_bindings[0].metadata["object_store"]).load_exact(
        registry_id=ids.strategy_artifact_id,
        strategy_id=ids.strategy_id,
        version=ids.version,
        execution_mode="paper",
        expected_checksum=pool_rm_bindings[0].metadata["artifact_checksum"],
    )
    assert loaded.metadata["registry_id"] == ids.strategy_artifact_id

    # Subsequent ensure_trader_paper_book call makes zero transitions and zero owner writes.
    transition_calls: List[str] = []
    orig_transition = rm_client.transition

    def _spy_transition(binding_id: str, new_status: str, **kwargs: Any) -> Any:
        transition_calls.append(f"{binding_id}:{new_status}")
        return orig_transition(binding_id, new_status, **kwargs)

    monkeypatch.setattr(rm_client, "transition", _spy_transition)
    replay = ensure_trader_paper_book(
        hosted_tenant,
        hosted_persona,
        requested_by="agora-trader-1",
        agora_user_id="agora-trader-1",
        coordinator=coordinator,
        runtime_client=rm_client,
    )
    assert replay["runtime_binding_id"] == hosted_binding_id
    assert replay["idempotent_replay"] is True
    assert transition_calls == []


def test_get_and_read_trader_paper_book_are_strictly_read_only_on_legacy_active_binding(
    owner_stack: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (PAPER-TRADER-BOOK-EXECUTABLE-INTENT-ONLY-20261127 P1):
    GET /bff/agora/servant/paper-book and read_trader_paper_book must perform
    zero Runtime-Manager transitions and zero owner writes on a legacy active
    binding lacking the execution projection; repair occurs only on the
    authorized ensure path.
    """
    from services.control_plane.bff.agora.servant.router import _stable_servant_persona_id
    from services.control_plane.bff.personas.trader_paper_book import (
        trader_paper_book_ids,
        trader_paper_runtime_id,
    )
    from services.runtime_manager.runtime_binding import (
        DeploymentMode,
        RuntimeBinding,
        RuntimeBindingStatus,
    )

    client: TestClient = owner_stack["client"]
    rm_service: RuntimeManagerService = owner_stack["rm_service"]
    rm_client: Any = owner_stack["rm_client"]
    capital_calls: List[Tuple[str, str, Optional[Dict[str, Any]], int]] = owner_stack["capital_calls"]
    coordinator_for_tenant = owner_stack["coordinator_for_tenant"]

    user_id = "trader-legacy-readonly"
    persona_id = _stable_servant_persona_id(tenant_id=TENANT_ID, agora_user_id=user_id)
    legacy_binding_id = "rb-legacy-active-readonly-001"
    ids = trader_paper_book_ids(TENANT_ID, persona_id)

    coordinator = coordinator_for_tenant(TENANT_ID)
    coordinator.ensure_chain_paper_capital(
        tenant_id=TENANT_ID,
        persona_id=persona_id,
        capital_pool_id=ids.capital_pool_id,
        binding_id=ids.persona_capital_binding_id,
        requested_by=user_id,
        currency="TWD",
        name=f"{persona_id} trader paper pool",
        budget=10_000_000.0,
        mandate="user_private_agora_servant_paper_book",
        labels={"task_id": "PAPER-TRADER-BOOK-PROVISION-20261127", "paper_only": True},
    )
    rm_service._store.create(
        RuntimeBinding(
            binding_id=legacy_binding_id,
            runtime_id=trader_paper_runtime_id(ids),
            capital_pool_id=ids.capital_pool_id,
            plan_id=ids.deployment_plan_id,
            artifact_id=ids.strategy_artifact_id,
            artifact_version=ids.version,
            deployment_mode=DeploymentMode.PAPER.value,
            status=RuntimeBindingStatus.ACTIVE.value,
            effective_at="2026-11-27T09:42:01Z",
            persona_capital_binding_id=ids.persona_capital_binding_id,
            execution_mode="paper",
            metadata={
                "tenant_id": TENANT_ID,
                "persona_id": persona_id,
                "sponsor_persona_id": persona_id,
                "strategy_id": ids.strategy_id,
                "execution_context": "paper",
                "paper_only": True,
                "book_kind": "trader_servant_paper_book",
                "requested_by": user_id,
            },
        )
    )

    transition_calls: List[str] = []
    deploy_calls: List[str] = []
    orig_transition = rm_client.transition
    orig_deploy = rm_client.deploy

    def _spy_transition(binding_id: str, new_status: str, **kwargs: Any) -> Any:
        transition_calls.append(f"{binding_id}:{new_status}")
        return orig_transition(binding_id, new_status, **kwargs)

    def _spy_deploy(request: Dict[str, Any]) -> Any:
        deploy_calls.append(str(request.get("plan_id") or ""))
        return orig_deploy(request)

    monkeypatch.setattr(rm_client, "transition", _spy_transition)
    monkeypatch.setattr(rm_client, "deploy", _spy_deploy)

    capital_calls.clear()
    direct_read = read_trader_paper_book(
        TENANT_ID,
        persona_id,
        agora_user_id=user_id,
        coordinator=coordinator,
        runtime_client=rm_client,
    )
    assert direct_read is not None
    assert direct_read["runtime_binding_id"] == legacy_binding_id
    assert transition_calls == []
    assert deploy_calls == []
    assert not any(method != "GET" for method, _url, _payload, _status in capital_calls)
    assert "object_store" not in (rm_service.get_binding(legacy_binding_id).metadata or {})

    headers = {
        "Authorization": _mint_bearer(sub=user_id, tenant_id=TENANT_ID),
        "Idempotency-Key": "idem-legacy-readonly-1",
        "X-Request-Id": "req-legacy-readonly-1",
        "X-Tenant-Id": TENANT_ID,
    }
    capital_calls.clear()
    get_res = client.get("/bff/agora/servant/paper-book", headers=headers)
    assert get_res.status_code == 200, get_res.text
    assert get_res.json()["data"]["runtime_binding_id"] == legacy_binding_id
    assert transition_calls == []
    assert deploy_calls == []
    assert not any(method != "GET" for method, _url, _payload, _status in capital_calls)
    assert "object_store" not in (rm_service.get_binding(legacy_binding_id).metadata or {})

    # Authorized ensure path performs the in-place repair on the same binding_id.
    ensure_res = client.post("/bff/agora/servant/paper-book/ensure", headers=headers, json={})
    assert ensure_res.status_code == 200, ensure_res.text
    assert ensure_res.json()["data"]["runtime_binding_id"] == legacy_binding_id
    assert transition_calls == [
        f"{legacy_binding_id}:pending_pause",
        f"{legacy_binding_id}:paused",
        f"{legacy_binding_id}:active",
    ]
    assert deploy_calls == []
    assert isinstance(rm_service.get_binding(legacy_binding_id).metadata.get("object_store"), dict)


def test_get_and_ensure_preserve_deliberately_paused_legacy_binding_with_zero_transitions(
    owner_stack: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (PAPER-TRADER-BOOK-EXECUTABLE-INTENT-ONLY-20261127 P1):
    A deliberately paused legacy RuntimeBinding must never be transitioned or
    resumed to active by GET /bff/agora/servant/paper-book, read_trader_paper_book,
    POST /bff/agora/servant/paper-book/ensure, or ensure_trader_paper_book.
    """
    from services.control_plane.bff.agora.servant.router import _stable_servant_persona_id
    from services.control_plane.bff.personas.trader_paper_book import (
        trader_paper_book_ids,
        trader_paper_runtime_id,
    )
    from services.runtime_manager.runtime_binding import (
        DeploymentMode,
        RuntimeBinding,
        RuntimeBindingStatus,
    )

    client: TestClient = owner_stack["client"]
    rm_service: RuntimeManagerService = owner_stack["rm_service"]
    rm_client: Any = owner_stack["rm_client"]
    capital_calls: List[Tuple[str, str, Optional[Dict[str, Any]], int]] = owner_stack["capital_calls"]
    coordinator_for_tenant = owner_stack["coordinator_for_tenant"]

    user_id = "trader-legacy-paused"
    persona_id = _stable_servant_persona_id(tenant_id=TENANT_ID, agora_user_id=user_id)
    paused_binding_id = "rb-legacy-paused-001"
    ids = trader_paper_book_ids(TENANT_ID, persona_id)

    coordinator = coordinator_for_tenant(TENANT_ID)
    coordinator.ensure_chain_paper_capital(
        tenant_id=TENANT_ID,
        persona_id=persona_id,
        capital_pool_id=ids.capital_pool_id,
        binding_id=ids.persona_capital_binding_id,
        requested_by=user_id,
        currency="TWD",
        name=f"{persona_id} trader paper pool",
        budget=10_000_000.0,
        mandate="user_private_agora_servant_paper_book",
        labels={"task_id": "PAPER-TRADER-BOOK-PROVISION-20261127", "paper_only": True},
    )
    rm_service._store.create(
        RuntimeBinding(
            binding_id=paused_binding_id,
            runtime_id=trader_paper_runtime_id(ids),
            capital_pool_id=ids.capital_pool_id,
            plan_id=ids.deployment_plan_id,
            artifact_id=ids.strategy_artifact_id,
            artifact_version=ids.version,
            deployment_mode=DeploymentMode.PAPER.value,
            status=RuntimeBindingStatus.PAUSED.value,
            effective_at="2026-11-27T09:42:01Z",
            persona_capital_binding_id=ids.persona_capital_binding_id,
            execution_mode="paper",
            metadata={
                "tenant_id": TENANT_ID,
                "persona_id": persona_id,
                "sponsor_persona_id": persona_id,
                "strategy_id": ids.strategy_id,
                "execution_context": "paper",
                "paper_only": True,
                "book_kind": "trader_servant_paper_book",
                "requested_by": user_id,
            },
        )
    )

    transition_calls: List[str] = []
    deploy_calls: List[str] = []
    orig_transition = rm_client.transition
    orig_deploy = rm_client.deploy

    def _spy_transition(binding_id: str, new_status: str, **kwargs: Any) -> Any:
        transition_calls.append(f"{binding_id}:{new_status}")
        return orig_transition(binding_id, new_status, **kwargs)

    def _spy_deploy(request: Dict[str, Any]) -> Any:
        deploy_calls.append(str(request.get("plan_id") or ""))
        return orig_deploy(request)

    monkeypatch.setattr(rm_client, "transition", _spy_transition)
    monkeypatch.setattr(rm_client, "deploy", _spy_deploy)

    capital_calls.clear()
    with pytest.raises(PersonaProvisioningCoordinationError) as read_exc:
        read_trader_paper_book(
            TENANT_ID,
            persona_id,
            agora_user_id=user_id,
            coordinator=coordinator,
            runtime_client=rm_client,
        )
    assert read_exc.value.reason_code == "runtime_binding_not_active"
    assert transition_calls == []
    assert deploy_calls == []
    assert not any(method != "GET" for method, _url, _payload, _status in capital_calls)
    assert rm_service.get_binding(paused_binding_id).status == "paused"

    headers = {
        "Authorization": _mint_bearer(sub=user_id, tenant_id=TENANT_ID),
        "Idempotency-Key": "idem-legacy-paused-1",
        "X-Request-Id": "req-legacy-paused-1",
        "X-Tenant-Id": TENANT_ID,
    }
    capital_calls.clear()
    get_res = client.get("/bff/agora/servant/paper-book", headers=headers)
    assert get_res.status_code == 409, get_res.text
    assert transition_calls == []
    assert deploy_calls == []
    assert not any(method != "GET" for method, _url, _payload, _status in capital_calls)
    assert rm_service.get_binding(paused_binding_id).status == "paused"

    with pytest.raises(PersonaProvisioningCoordinationError) as ensure_exc:
        ensure_trader_paper_book(
            TENANT_ID,
            persona_id,
            requested_by=user_id,
            agora_user_id=user_id,
            coordinator=coordinator,
            runtime_client=rm_client,
        )
    assert ensure_exc.value.reason_code == "runtime_binding_not_active"
    assert transition_calls == []
    assert deploy_calls == []
    assert rm_service.get_binding(paused_binding_id).status == "paused"

    ensure_res = client.post("/bff/agora/servant/paper-book/ensure", headers=headers, json={})
    assert ensure_res.status_code == 409, ensure_res.text
    assert transition_calls == []
    assert deploy_calls == []
    assert rm_service.get_binding(paused_binding_id).status == "paused"
