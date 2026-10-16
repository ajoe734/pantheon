"""Default owner readers preserve auth and distinguish empty from unavailable."""
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from services.control_plane.bff.core import owner_reads
from services.control_plane.bff.ports import create_read_surface_ports


@pytest.mark.parametrize("dataset,method,path,key", [
    ("capital_pools", "list_capital_pools", "/api/capital-pools", None),
    ("bindings", "list_bindings", "/api/bindings", None),
    ("deployment_plans", "list_deployment_plans", "/api/deployment/plans", None),
    ("runtime_bindings", "list_runtime_bindings", "/api/runtime-bindings", "bindings"),
])
def test_default_readers_preserve_auth_and_availability(monkeypatch, dataset, method, path, key):
    for env in ("PANTHEON_CAPITAL_API_URL", "PANTHEON_DEPLOYMENT_API_URL", "PANTHEON_RUNTIME_MANAGER_URL"):
        monkeypatch.setenv(env, "http://owner")
    calls = []
    row = {"pool_id": "p1", "binding_id": "b1", "plan_id": "d1"}
    rows = [row]

    def transport(url, **kwargs):
        assert kwargs["auth_token"] == "caller-jwt"
        calls.append(urlsplit(url).path)
        if urlsplit(url).path != path:
            return []
        return {key: rows} if key else rows

    monkeypatch.setattr(owner_reads, "http_request_json", transport)
    ports = create_read_surface_ports()
    read = getattr(ports, method)
    assert read() == []
    assert ports.dataset_source(dataset) == "unavailable"
    token = owner_reads.authorization.set("Bearer caller-jwt")
    try:
        assert read()[0].items() >= row.items()
        assert path in calls
        assert ports.dataset_source(dataset) != "missing"
        rows.clear()
        assert read() == []
        assert ports.dataset_source(dataset) != "missing"
        monkeypatch.setattr(owner_reads, "http_request_json", lambda *a, **k: {"error": "unavailable"})
        assert read() == []
        assert ports.dataset_source(dataset) == "unavailable"
    finally:
        owner_reads.authorization.reset(token)


def test_evolution_readers_preserve_static_auth_and_tenant_contract(monkeypatch, tmp_path):
    import io
    import json
    import os
    import urllib.error
    import urllib.request
    from urllib.parse import urlsplit

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from services.control_plane.bff.auth.policy import (
        bff_error,
        extract_identity_jwt,
        require_operator_role,
        require_read_role,
    )
    from services.control_plane.bff.command_adapters.base import ActionUnavailableError
    from services.control_plane.bff.core.owner_reads import (
        OwnerReadContextMiddleware,
        authorization,
        selected_tenant,
    )
    from services.control_plane.bff.evolution.router import (
        create_evolution_programs_router,
        create_evolution_router,
    )
    from services.control_plane.bff.models import utc_now
    from services.control_plane.bff.ports import create_read_surface_ports
    from services.evolution import main as evo_main
    from services.evolution.dispatch_outbox import (
        CompensationLedger,
        EvolutionDispatchOutbox,
        build_dispatch_outbox_store,
    )
    from services.evolution.program_store import build_program_store
    from services.runtime_auth_inbound import encode_jwt_hs256

    _JWT_ENV = {
        "PANTHEON_BFF_JWT_SECRET": "receipt-owner-signing-secret-0123456789",
        "PANTHEON_BFF_JWT_ISSUER": "receipt-owner-tests",
        "PANTHEON_BFF_JWT_AUDIENCE": "bff-operators",
        "PANTHEON_BFF_AUTH_MODE": "strict",
    }
    for k, v in _JWT_ENV.items():
        monkeypatch.setenv(k, v)

    service_token = "pantheon-local-evolution-service"
    monkeypatch.setenv("EVOLUTION_AUTH_MODE", "token")
    monkeypatch.setenv("EVOLUTION_AUTH_TOKEN", service_token)
    monkeypatch.setenv("EVOLUTION_AUTH_ALLOWED_TENANTS", "tenant-dev")
    monkeypatch.setenv("PANTHEON_EVOLUTION_API_URL", "http://evolution-owner.local")

    monkeypatch.setattr(
        evo_main,
        "store",
        evo_main.EvolutionDecisionStore(
            storage_path=str(tmp_path / "decisions.json")
        ),
    )
    program_store = build_program_store(tmp_path / "programs.json")
    monkeypatch.setattr(evo_main, "program_store", program_store)
    monkeypatch.setattr(evo_main.program_service, "_store", program_store)
    monkeypatch.setattr(
        evo_main,
        "dispatch_outbox",
        EvolutionDispatchOutbox(build_dispatch_outbox_store(data_dir=tmp_path)),
    )
    monkeypatch.setattr(
        evo_main,
        "compensation_ledger",
        CompensationLedger(data_dir=tmp_path),
    )

    def _tok(tenant, roles=("operator", "viewer")):
        claims = {
            "sub": "operator-1",
            "roles": list(roles),
            "exp": 4102444800,
            "iss": _JWT_ENV["PANTHEON_BFF_JWT_ISSUER"],
            "aud": _JWT_ENV["PANTHEON_BFF_JWT_AUDIENCE"],
        }
        if tenant:
            claims["tenant_id"] = tenant
        return encode_jwt_hs256(claims, secret=_JWT_ENV["PANTHEON_BFF_JWT_SECRET"])

    evo_client = TestClient(evo_main.app)

    class MockHttpResponse:
        def __init__(self, status: int, content: bytes):
            self.status = status
            self._io = io.BytesIO(content)

        def read(self) -> bytes:
            return self._io.read()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    server_state = {"mode": "200"}
    calls = []

    def transport_urlopen(req, timeout=30):
        if server_state.get("mode") == "503":
            raise urllib.error.HTTPError(
                req.full_url,
                503,
                "Service Unavailable",
                {},
                io.BytesIO(b'{"detail":"Evolution owner unavailable"}'),
            )
        url = req.full_url
        split = urlsplit(url)
        path = split.path + ("?" + split.query if split.query else "")
        headers = {k: v for k, v in req.header_items()}
        calls.append({
            "path": split.path,
            "auth": headers.get("Authorization", ""),
            "tenant": headers.get("X-tenant-id", ""),
            "service": headers.get("X-pantheon-service", ""),
        })
        resp = evo_client.get(path, headers=headers)
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(
                url,
                resp.status_code,
                getattr(resp, "reason_phrase", "Error"),
                {},
                io.BytesIO(resp.content),
            )
        return MockHttpResponse(resp.status_code, resp.content)

    monkeypatch.setattr(urllib.request, "urlopen", transport_urlopen)

    # Seed program using real Evolution program store/service
    prog, _ = evo_main.program_service.create_program(
        tenant_id="tenant-dev", actor_id="admin-1", name="Alpha Program"
    )
    prog_id = prog["program_id"]

    # Seed decisions via real Evolution service proposal endpoints
    evo_h = {"Authorization": f"Bearer {service_token}", "X-Tenant-Id": "tenant-dev"}
    r1 = evo_client.post(
        "/api/evolution/proposals",
        headers=evo_h,
        json={
            "decision_id": "dec-1",
            "tenant_id": "tenant-dev",
            "target_type": "candidate_artifact",
            "target_id": "art-1",
            "target_version": "1.0.0",
            "action_type": "retrain",
            "rationale": "Rolling drawdown breached expected baseline",
            "created_by_id": "ctrl-1",
            "linked_incident_id": "inc-1",
            "metadata": {"program_id": prog_id, "resolved_at": "2026-10-16T03:30:00Z"},
        },
    )
    assert r1.status_code == 201

    r2 = evo_client.post(
        "/api/evolution/proposals",
        headers=evo_h,
        json={
            "decision_id": "dec-2",
            "tenant_id": "tenant-dev",
            "target_type": "candidate_artifact",
            "target_id": "art-2",
            "target_version": "1.0.0",
            "action_type": "retire",
            "rationale": "Severity-1 incident recurrence",
            "created_by_id": "ctrl-1",
            "linked_incident_id": "inc-2",
            "metadata": {"program_id": prog_id},
        },
    )
    assert r2.status_code == 201

    rev_r2 = evo_client.post(
        "/api/evolution/proposals/dec-2/review",
        headers=evo_h,
        json={
            "actor_role": "governance_committee",
            "actor_id": "gov-1",
            "approval_decision_id": "app-2",
            "tenant_id": "tenant-dev",
        },
    )
    assert rev_r2.status_code == 200

    app_r2 = evo_client.post(
        "/api/evolution/proposals/dec-2/approve",
        headers=evo_h,
        json={
            "actor_role": "governance_committee",
            "actor_id": "gov-2",
            "tenant_id": "tenant-dev",
        },
    )
    assert app_r2.status_code == 200

    # Default BFF composition with OwnerReadContextMiddleware
    ports = create_read_surface_ports()
    app = FastAPI()
    app.add_middleware(OwnerReadContextMiddleware)
    common = dict(
        read_surface=ports,
        extract_identity=extract_identity_jwt,
        require_read_role=require_read_role,
        require_operator_role=require_operator_role,
        bff_error=bff_error,
        utc_now=utc_now,
    )
    app.include_router(create_evolution_programs_router(**common))
    app.include_router(create_evolution_router(**common))
    client = TestClient(app)

    # 1. Unauthenticated request to real BFF routes fails admission with 401
    assert client.get("/api/v1/evolution-decisions").status_code == 401
    assert client.get("/bff/evolution-programs").status_code == 401

    # Caller without read role rejected with 403
    no_role_headers = {"Authorization": f"Bearer {_tok('tenant-dev', roles=[])}", "X-Tenant-Id": "tenant-dev"}
    assert client.get("/api/v1/evolution-decisions", headers=no_role_headers).status_code == 403

    # Direct port call without authorization context raises ActionUnavailableError
    with pytest.raises(ActionUnavailableError) as exc_info:
        ports.list_evolution_decisions()
    assert exc_info.value.downstream_status == 503
    assert ports.dataset_source("evolution_decisions") == "unavailable"

    with pytest.raises(ActionUnavailableError) as exc_info:
        ports.list_evolution_programs()
    assert exc_info.value.downstream_status == 503
    assert ports.dataset_source("evolution_programs") == "unavailable"

    # 2. Populated reads with valid signed token and matching tenant
    valid_headers = {"Authorization": f"Bearer {_tok('tenant-dev')}", "X-Tenant-Id": "tenant-dev"}

    # Evolution decisions list through real route
    dec_resp = client.get("/api/v1/evolution-decisions", headers=valid_headers)
    assert dec_resp.status_code == 200
    dec_items = dec_resp.json()["items"]
    assert len(dec_items) == 2
    # Verify static service token and caller tenant were delivered to Evolution owner
    assert calls[-1]["auth"] == f"Bearer {service_token}"
    assert calls[-1]["tenant"] == "tenant-dev"
    assert calls[-1]["service"] == "control-plane-bff"

    # Verify decision_state is projected to status via DomainGovernancePort
    assert dec_items[0]["decision_id"] == "dec-1"
    assert dec_items[0]["status"] == "proposed"
    assert dec_items[0]["decision_state"] == "proposed"
    assert dec_items[0]["resolved_at"] == "2026-10-16T03:30:00Z"
    assert dec_items[1]["decision_id"] == "dec-2"
    assert dec_items[1]["status"] == "approved"
    assert dec_items[1]["decision_state"] == "approved"

    # Verify 3 route-level filters: action_type, risk_level, status
    r_action = client.get("/api/v1/evolution-decisions?action_type=retrain", headers=valid_headers)
    assert r_action.status_code == 200
    assert len(r_action.json()["items"]) == 1
    assert r_action.json()["items"][0]["decision_id"] == "dec-1"

    r_risk = client.get("/api/v1/evolution-decisions?risk_level=low", headers=valid_headers)
    assert r_risk.status_code == 200
    assert len(r_risk.json()["items"]) == 1
    assert r_risk.json()["items"][0]["decision_id"] == "dec-1"

    r_status = client.get("/api/v1/evolution-decisions?status=approved", headers=valid_headers)
    assert r_status.status_code == 200
    assert len(r_status.json()["items"]) == 1
    assert r_status.json()["items"][0]["decision_id"] == "dec-2"

    # Evolution programs list through real route
    prog_resp = client.get("/bff/evolution-programs", headers=valid_headers)
    assert prog_resp.status_code == 200
    prog_items = prog_resp.json()["items"]
    assert len(prog_items) == 1
    assert prog_items[0]["program_id"] == prog_id

    # Derived program runs projection preserves resolved_at
    token = authorization.set(f"Bearer {_tok('tenant-dev')}")
    t_tok = selected_tenant.set("tenant-dev")
    try:
        runs = ports.list_evolution_program_runs(prog_id)
        assert len(runs) == 2
        assert runs[0]["run_id"] == "dec-1"
        assert runs[0]["completed_at"] == "2026-10-16T03:30:00Z"
    finally:
        selected_tenant.reset(t_tok)
        authorization.reset(token)

    # 3. Valid empty collection remains distinct from failure
    empty_tmp = tmp_path / "empty_evolution"
    empty_tmp.mkdir()
    evo_main.store = evo_main.EvolutionDecisionStore(storage_path=str(empty_tmp / "empty_decisions.json"))
    empty_resp = client.get("/api/v1/evolution-decisions", headers=valid_headers)
    assert empty_resp.status_code == 200
    assert empty_resp.json()["items"] == []

    token = authorization.set(f"Bearer {_tok('tenant-dev')}")
    t_tok = selected_tenant.set("tenant-dev")
    try:
        assert ports.list_evolution_decisions() == []
        assert ports.dataset_source("evolution_decisions") == "service"
    finally:
        selected_tenant.reset(t_tok)
        authorization.reset(token)

    # 4. Foreign tenant is rejected before network I/O
    calls_before = len(calls)
    foreign_headers = {"Authorization": f"Bearer {_tok('tenant-dev')}", "X-Tenant-Id": "foreign-tenant"}
    foreign_resp = client.get("/api/v1/evolution-decisions", headers=foreign_headers)
    assert foreign_resp.status_code == 403
    assert len(calls) == calls_before  # zero network packets sent to owner!

    # 5. Owner 403 (unauthorized caller tenant) is not swallowed into fake empty []
    other_headers = {"Authorization": f"Bearer {_tok('tenant-other')}", "X-Tenant-Id": "tenant-other"}
    denied_resp = client.get("/api/v1/evolution-decisions", headers=other_headers)
    assert denied_resp.status_code == 403
    assert denied_resp.json()["detail"]["error"]["code"] == "ACTION_UNAVAILABLE"
    assert "tenant-other" in denied_resp.json()["detail"]["error"]["message"]

    token = authorization.set(f"Bearer {_tok('tenant-other')}")
    t_tok = selected_tenant.set("tenant-other")
    try:
        with pytest.raises(ActionUnavailableError) as err_403:
            ports.list_evolution_decisions()
        assert err_403.value.downstream_status == 403
    finally:
        selected_tenant.reset(t_tok)
        authorization.reset(token)

    # 6. Owner 503 is not swallowed into fake empty []
    server_state["mode"] = "503"
    unavail_resp = client.get("/api/v1/evolution-decisions", headers=valid_headers)
    assert unavail_resp.status_code == 503
    assert unavail_resp.json()["detail"]["error"]["code"] == "ACTION_UNAVAILABLE"
    assert "503" in unavail_resp.json()["detail"]["error"]["message"]

    token = authorization.set(f"Bearer {_tok('tenant-dev')}")
    t_tok = selected_tenant.set("tenant-dev")
    try:
        with pytest.raises(ActionUnavailableError) as err_503:
            ports.list_evolution_decisions()
        assert err_503.value.downstream_status == 503
    finally:
        selected_tenant.reset(t_tok)
        authorization.reset(token)


def test_rankings_read_the_same_injected_owner_store():
    rows = [SimpleNamespace(to_dict=lambda: {"ranking_id": "r1", "status": "published"})]
    ports = create_read_surface_ports(ranking_store=SimpleNamespace(list_rankings=lambda: rows))
    assert ports.get_ranking("r1")["status"] == "published"
    rows.clear()
    assert ports.list_rankings() == []
    assert ports.dataset_source("rankings") != "missing"


@pytest.mark.parametrize("dataset", [
    "capital_pools",
    "bindings",
    "deployment_plans",
    "runtime_bindings",
    "evolution_programs",
    "evolution_decisions",
])
def test_unconfigured_owner_reports_missing(monkeypatch, dataset):
    for env in ("PANTHEON_CAPITAL_API_URL", "PANTHEON_CAPITAL_SERVICE_URL", "PANTHEON_DEPLOYMENT_API_URL", "PANTHEON_RUNTIME_MANAGER_URL", "PANTHEON_EVOLUTION_API_URL"):
        monkeypatch.delenv(env, raising=False)
    token = owner_reads.authorization.set("Bearer caller-jwt")
    try:
        ports = create_read_surface_ports()
        assert ports.dataset_source(dataset) == "missing"
    finally:
        owner_reads.authorization.reset(token)


def test_failing_pools_read_does_not_mask_readable_bindings():
    from services.control_plane.bff.ports.persona_capital_runtime import CapitalPoolPort, PersonaCapitalRuntimeDomainPort
    def boom():
        raise RuntimeError("pools service down")
    port = CapitalPoolPort(pools_provider=boom, bindings_provider=lambda: [{"binding_id": "b1"}])
    status = port.get_surface_status()
    assert status["source"] == "unavailable"
    assert status["bindings_source"] == "service"
    domain_port = PersonaCapitalRuntimeDomainPort(capital_port=port)
    read_ports = create_read_surface_ports(persona_capital_runtime=domain_port)
    assert read_ports.dataset_source("capital_pools") == "unavailable"
    assert read_ports.dataset_source("bindings") == "service"


def test_ranking_datasets_owner_down_vs_unconfigured():
    from services.control_plane.bff.ports.persona_capital_runtime import RankingProjectionPort, PersonaCapitalRuntimeDomainPort
    def boom():
        raise RuntimeError("ranking service down")
    port_down = RankingProjectionPort(rankings_reader=boom, rebalances_reader=boom)
    domain_down = PersonaCapitalRuntimeDomainPort(ranking_port=port_down)
    ports_down = create_read_surface_ports(persona_capital_runtime=domain_down)
    assert ports_down.dataset_source("rankings") == "unavailable"
    assert ports_down.dataset_source("rebalances") == "unavailable"

    port_unconf = RankingProjectionPort()
    domain_unconf = PersonaCapitalRuntimeDomainPort(ranking_port=port_unconf)
    ports_unconf = create_read_surface_ports(persona_capital_runtime=domain_unconf)
    assert ports_unconf.dataset_source("rankings") == "missing"
    assert ports_unconf.dataset_source("rebalances") == "missing"

