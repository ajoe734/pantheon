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


def test_evolution_readers_preserve_static_auth_and_tenant_contract(monkeypatch):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
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
    monkeypatch.setenv("EVOLUTION_AUTH_TOKEN", service_token)

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

    server_state = {
        "mode": "200",
        "allowed_tenants": ["tenant-dev"],
        "programs": [
            {"program_id": "prog-1", "status": "active", "created_at": "2026-10-16T01:00:00Z", "tenant_id": "tenant-dev"}
        ],
        "proposals": [
            {
                "id": "dec-1",
                "decision_id": "dec-1",
                "program_id": "prog-1",
                "decision_state": "proposed",
                "action_type": "rollback",
                "risk_level": "low",
                "created_at": "2026-10-16T03:00:00Z",
                "resolved_at": "2026-10-16T03:30:00Z",
                "tenant_id": "tenant-dev",
            },
            {
                "id": "dec-2",
                "decision_id": "dec-2",
                "program_id": "prog-1",
                "decision_state": "approved",
                "action_type": "redeploy",
                "risk_level": "high",
                "created_at": "2026-10-16T02:00:00Z",
                "tenant_id": "tenant-dev",
            },
        ],
    }
    calls = []

    class EvolutionOwnerHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            mode = server_state.get("mode", "200")
            if mode == "503":
                self.send_json(503, {"detail": "Evolution owner unavailable"})
                return

            auth = self.headers.get("Authorization", "")
            tenant = self.headers.get("X-Tenant-Id", "")
            service_hdr = self.headers.get("X-Pantheon-Service", "")

            # Static service token check
            if auth != f"Bearer {service_token}":
                self.send_json(401, {"detail": "invalid Evolution bearer token"})
                return

            # Caller tenant header check
            if not tenant:
                self.send_json(400, {"detail": "X-Tenant-Id is required"})
                return

            # Owner tenant allowlist check
            allowed = server_state.get("allowed_tenants", [])
            if "*" not in allowed and tenant not in allowed:
                self.send_json(403, {"detail": "authenticated caller is not authorized for this tenant"})
                return

            path = urlsplit(self.path).path
            calls.append({
                "path": path,
                "auth": auth,
                "tenant": tenant,
                "service": service_hdr,
            })

            if path == "/api/evolution/programs":
                items = [p for p in server_state.get("programs", []) if p.get("tenant_id") == tenant]
                self.send_json(200, {"items": items})
            elif path == "/api/evolution/proposals":
                items = [d for d in server_state.get("proposals", []) if d.get("tenant_id") == tenant]
                self.send_json(200, items)
            else:
                self.send_json(404, {"detail": "not found"})

        def send_json(self, status, body):
            raw = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = HTTPServer(("127.0.0.1", 0), EvolutionOwnerHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    monkeypatch.setenv("PANTHEON_EVOLUTION_API_URL", f"http://127.0.0.1:{server.server_port}")

    try:
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
        r_action = client.get("/api/v1/evolution-decisions?action_type=rollback", headers=valid_headers)
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
        assert prog_items[0]["program_id"] == "prog-1"

        # Derived program runs projection preserves resolved_at
        token = authorization.set(f"Bearer {_tok('tenant-dev')}")
        t_tok = selected_tenant.set("tenant-dev")
        try:
            runs = ports.list_evolution_program_runs("prog-1")
            assert len(runs) == 2
            assert runs[0]["run_id"] == "dec-1"
            assert runs[0]["completed_at"] == "2026-10-16T03:30:00Z"
        finally:
            selected_tenant.reset(t_tok)
            authorization.reset(token)

        # 3. Valid empty collection remains distinct from failure
        server_state["proposals"] = []
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

        # 5. Owner 403 is not swallowed into fake empty []
        server_state["allowed_tenants"] = ["some-other-tenant"]
        denied_resp = client.get("/api/v1/evolution-decisions", headers=valid_headers)
        assert denied_resp.status_code in (403, 500, 503)
        assert denied_resp.status_code != 200

        token = authorization.set(f"Bearer {_tok('tenant-dev')}")
        t_tok = selected_tenant.set("tenant-dev")
        try:
            with pytest.raises(ActionUnavailableError) as err_403:
                ports.list_evolution_decisions()
            assert err_403.value.downstream_status == 403
        finally:
            selected_tenant.reset(t_tok)
            authorization.reset(token)

        # 6. Owner 503 is not swallowed into fake empty []
        server_state["mode"] = "503"
        server_state["allowed_tenants"] = ["tenant-dev"]
        unavail_resp = client.get("/api/v1/evolution-decisions", headers=valid_headers)
        assert unavail_resp.status_code in (500, 503)
        assert unavail_resp.status_code != 200

        token = authorization.set(f"Bearer {_tok('tenant-dev')}")
        t_tok = selected_tenant.set("tenant-dev")
        try:
            with pytest.raises(ActionUnavailableError) as err_503:
                ports.list_evolution_decisions()
            assert err_503.value.downstream_status == 503
        finally:
            selected_tenant.reset(t_tok)
            authorization.reset(token)

    finally:
        server.shutdown()
        server_thread.join(timeout=5)
        server.server_close()


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

