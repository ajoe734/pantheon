"""Production service-boundary regressions for the Agora servant owner."""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Iterator

import pytest
import uvicorn
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient



from services.control_plane.bff import main as bff_main
from services.control_plane.bff.agora.servant import router as servant_router
from services.control_plane.bff.ports import (
    PersonaRegistryHttpWritePort,
    PersonaWriteOwnerUnavailable,
    create_persona_registry_write_owner,
    create_read_surface_ports,
)
from services.persona.write_owner import (
    PersistentCapabilitySnapshotOwner,
    PersistentPersonaOwner,
    create_app as create_persona_owner_app,
)


_SERVICE_TOKEN = "test-persona-owner-service-token"
_SERVICE_ACTOR = "operator-bff"


class _OpenClawSessions:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}

    def create_session(
        self,
        *,
        agent_id: str,
        session_type: str,
        operator_id: str,
        idempotency_key: str,
        context_bundle: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_id = f"production-session-{len(self.sessions) + 1}"
        record = {
            "session_id": session_id,
            "agent_id": agent_id,
            "session_type": session_type,
            "operator_id": operator_id,
            "idempotency_key": idempotency_key,
            "state": "active",
            "context_bundle": dict(context_bundle or {}),
        }
        self.sessions[session_id] = record
        return {"session": record}

    def get_session(
        self,
        *,
        session_id: str,
        operator_id: str | None = None,
    ) -> dict[str, Any]:
        del operator_id
        return {"session": dict(self.sessions[session_id])}


def _unused_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@contextlib.contextmanager
def _running_persona_service(
    store_dir: Path,
    *,
    fail_capability_owner: bool = False,
) -> Iterator[str]:
    """Run the real Persona owner API over TCP, with fresh process stores."""

    app = create_persona_owner_app(
        PersistentPersonaOwner.from_json_path(store_dir / "personas.json"),
        capability_owner=PersistentCapabilitySnapshotOwner.from_json_path(
            store_dir / "capability_snapshots.json"
        ),
    )
    if fail_capability_owner:

        @app.middleware("http")
        async def fail_capability_request(request, call_next):
            if request.method == "PUT" and "/capability-snapshots/" in request.url.path:
                return JSONResponse(
                    status_code=503,
                    content={"detail": "capability owner offline"},
                )
            return await call_next(request)

    port = _unused_tcp_port()
    base_url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=0.2) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.01)
    else:
        server.should_exit = True
        thread.join(timeout=5.0)
        raise RuntimeError("Persona owner test service did not start")
    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        if thread.is_alive():
            raise RuntimeError("Persona owner test service did not stop")


def _configure_service_env(monkeypatch: pytest.MonkeyPatch, base_url: str) -> None:
    monkeypatch.setenv("PERSONA_URL", base_url)
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_TOKEN", _SERVICE_TOKEN)
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_ACTOR_ID", _SERVICE_ACTOR)
    monkeypatch.setenv("PERSONA_AUTH_MODE", "strict")


def _install_production_wiring(
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
) -> tuple[TestClient, PersonaRegistryHttpWritePort, Any, _OpenClawSessions]:
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "tenant-alpha,tenant-beta")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-alpha")
    _configure_service_env(monkeypatch, base_url)

    write_owner = create_persona_registry_write_owner()
    read_ports = create_read_surface_ports(persona_registry_store=write_owner)
    monkeypatch.setattr(bff_main, "persona_write_owner", write_owner)
    monkeypatch.setattr(bff_main, "read_store", read_ports)
    monkeypatch.setattr(
        bff_main,
        "_ensure_agora_servant_openclaw_agent",
        lambda persona: {
            "status": "created",
            "agent_id": persona["persona_id"],
            "model_id": f"openclaw/{persona['persona_id']}",
            "workspace_ref": f"workspace://{persona['persona_id']}",
        },
    )
    openclaw = _OpenClawSessions()
    monkeypatch.setattr(servant_router, "OpenClawOpsClient", lambda: openclaw)
    return (
        TestClient(bff_main.app, raise_server_exceptions=False),
        write_owner,
        read_ports,
        openclaw,
    )


def _ensure_headers(
    *,
    tenant_id: str,
    idempotency_key: str,
    request_id: str,
) -> dict[str, str]:
    return {
        "Authorization": "Bearer production-write-owner:operator",
        "X-Tenant-Id": tenant_id,
        "Idempotency-Key": idempotency_key,
        "X-Request-Id": request_id,
    }


def test_production_router_uses_authenticated_http_owner_and_read_only_surface(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with _running_persona_service(tmp_path) as service_url:
        client, write_owner, read_ports, _openclaw = _install_production_wiring(
            monkeypatch,
            service_url,
        )
        headers = _ensure_headers(
            tenant_id="tenant-alpha",
            idempotency_key="production-servant-owner-001",
            request_id="req-production-servant-owner-001",
        )

        assert write_owner.configured is True
        assert not callable(getattr(read_ports, "create_persona", None))
        assert not callable(getattr(read_ports, "update_persona", None))
        assert not callable(
            getattr(read_ports, "upsert_persona_capability_snapshot", None)
        )

        created = client.post("/bff/agora/servant/ensure", headers=headers)
        replayed = client.post(
            "/bff/agora/servant/ensure",
            headers={**headers, "X-Request-Id": "req-production-servant-owner-002"},
        )

        assert created.status_code == replayed.status_code == 200, created.text
        persona_id = created.json()["data"]["persona_id"]
        assert replayed.json()["data"]["persona_id"] == persona_id
        assert created.json()["data"]["status"] == "paper_only"
        assert created.json()["data"]["policy"]["execution_authority"] == "none"
        assert len(write_owner.list_personas()) == 1

        stored = read_ports.get_persona(persona_id)
        assert stored is not None
        assert stored["lifecycle_state"] == "research_only"
        assert stored["metadata"]["owner_scope"] == "user_private"
        assert stored["metadata"]["environment_ceiling"] == "paper"
        assert stored["metadata"]["execution_authority"] == "none"
        snapshot = read_ports.get_capability_snapshot(
            stored["metadata"]["capability_snapshot_id"]
        )
        assert snapshot is not None
        assert snapshot["capabilities"] == ["persona_opinion"]
        assert snapshot["metadata"]["execution_authority"] == "none"


def test_service_restart_and_fresh_bff_port_preserve_identity_and_capability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    headers = _ensure_headers(
        tenant_id="tenant-alpha",
        idempotency_key="production-servant-restart-001",
        request_id="req-production-servant-restart-001",
    )
    with _running_persona_service(tmp_path) as first_url:
        client, first_owner, _read_ports, _openclaw = _install_production_wiring(
            monkeypatch,
            first_url,
        )
        created = client.post("/bff/agora/servant/ensure", headers=headers)
        assert created.status_code == 200, created.text
        persona_id = created.json()["data"]["persona_id"]

    # A fresh HTTP server and fresh BFF port read the same Persona-owned files.
    with _running_persona_service(tmp_path) as restarted_url:
        _configure_service_env(monkeypatch, restarted_url)
        fresh_owner = create_persona_registry_write_owner()
        fresh_read_ports = create_read_surface_ports(persona_registry_store=fresh_owner)
        assert fresh_owner is not first_owner
        stored = fresh_read_ports.get_persona(persona_id)
        assert stored is not None
        snapshot_id = stored["metadata"]["capability_snapshot_id"]
        assert fresh_read_ports.get_capability_snapshot(snapshot_id)["persona_id"] == persona_id

        monkeypatch.setattr(bff_main, "persona_write_owner", fresh_owner)
        monkeypatch.setattr(bff_main, "read_store", fresh_read_ports)
        replayed = client.post(
            "/bff/agora/servant/ensure",
            headers={**headers, "X-Request-Id": "req-production-servant-restart-002"},
        )
        assert replayed.status_code == 200, replayed.text
        assert replayed.json()["data"]["persona_id"] == persona_id
        assert len(fresh_owner.list_personas()) == 1


def test_http_owner_restart_preserves_fleet_to_detail_read_symmetry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A user-private servant remains navigable by same-tenant read roles.

    The Fleet row is the browser's navigation source, so its ID and display
    name must survive an HTTP Persona-owner restart and resolve through the
    canonical detail facade.  This uses the actual service boundary rather
    than an in-memory BFF overlay, and verifies that switching the caller's
    tenant never widens the detail lookup.
    """
    ensure_headers = _ensure_headers(
        tenant_id="tenant-alpha",
        idempotency_key="production-servant-fleet-detail-001",
        request_id="req-production-servant-fleet-detail-001",
    )
    with _running_persona_service(tmp_path) as first_url:
        client, _first_owner, _read_ports, _openclaw = _install_production_wiring(
            monkeypatch,
            first_url,
        )
        created = client.post("/bff/agora/servant/ensure", headers=ensure_headers)
        assert created.status_code == 200, created.text
        persona_id = created.json()["data"]["persona_id"]

    # A distinct service instance and distinct BFF read port prove that no
    # process-local overlay is required for either Fleet or detail readback.
    with _running_persona_service(tmp_path) as restarted_url:
        _configure_service_env(monkeypatch, restarted_url)
        fresh_owner = create_persona_registry_write_owner()
        fresh_read_ports = create_read_surface_ports(persona_registry_store=fresh_owner)
        monkeypatch.setattr(bff_main, "persona_write_owner", fresh_owner)
        monkeypatch.setattr(bff_main, "read_store", fresh_read_ports)
        fresh_client = TestClient(bff_main.app, raise_server_exceptions=False)

        for role in ("operator", "viewer"):
            headers = {"Authorization": f"Bearer production-fleet-readback:{role}"}
            fleet = fresh_client.get(
                "/bff/management/persona-fleet?page_size=100",
                headers=headers,
            )
            assert fleet.status_code == 200, fleet.text
            fleet_row = next(
                item
                for item in fleet.json()["data"]["items"]
                if item["id"] == persona_id
            )
            assert fleet_row["name"] == "Agora Servant"

            detail = fresh_client.get(
                f"/bff/personas/{persona_id}",
                headers=headers,
            )
            assert detail.status_code == 200, detail.text
            assert detail.json()["data"]["id"] == persona_id
            assert detail.json()["data"]["name"] == fleet_row["name"]

        # Remove the test's default tenant so this separate caller's signed
        # tenant claim is authoritative.  The private servant must disappear
        # from Fleet and remain undiscoverable by ID.
        monkeypatch.delenv("PANTHEON_BFF_TENANT_ID")
        foreign_headers = {
            "Authorization": "Bearer production-foreign:operator:tenant-beta"
        }
        foreign_fleet = fresh_client.get(
            "/bff/management/persona-fleet?page_size=100",
            headers=foreign_headers,
        )
        assert foreign_fleet.status_code == 200, foreign_fleet.text
        assert persona_id not in {
            item["id"] for item in foreign_fleet.json()["data"]["items"]
        }
        foreign_detail = fresh_client.get(
            f"/bff/personas/{persona_id}",
            headers=foreign_headers,
        )
        assert foreign_detail.status_code == 404, foreign_detail.text
        assert foreign_detail.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

        unauthenticated = fresh_client.get(f"/bff/personas/{persona_id}")
        assert unauthenticated.status_code == 401, unauthenticated.text


def test_servant_identity_and_session_access_are_tenant_isolated_over_http_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with _running_persona_service(tmp_path) as service_url:
        client, _owner, _read_ports, _openclaw = _install_production_wiring(
            monkeypatch,
            service_url,
        )
        alpha = client.post(
            "/bff/agora/servant/ensure",
            headers=_ensure_headers(
                tenant_id="tenant-alpha",
                idempotency_key="production-servant-alpha",
                request_id="req-production-servant-alpha",
            ),
        )
        beta = client.post(
            "/bff/agora/servant/ensure",
            headers=_ensure_headers(
                tenant_id="tenant-beta",
                idempotency_key="production-servant-beta",
                request_id="req-production-servant-beta",
            ),
        )
        assert alpha.status_code == beta.status_code == 200
        assert alpha.json()["data"]["persona_id"] != beta.json()["data"]["persona_id"]

        alpha_session = client.post(
            "/bff/agora/servant/sessions",
            headers={
                "Authorization": "Bearer production-write-owner:operator",
                "X-Tenant-Id": "tenant-alpha",
                "Idempotency-Key": "production-alpha-session",
                "X-Request-Id": "req-production-alpha-session",
            },
            json={"intent": "tenant alpha private thesis"},
        )
        assert alpha_session.status_code == 201, alpha_session.text
        session_id = alpha_session.json()["data"]["session_id"]

        cross_tenant = client.get(
            f"/bff/agora/servant/sessions/{session_id}",
            headers={
                "Authorization": "Bearer production-write-owner:operator",
                "X-Tenant-Id": "tenant-beta",
            },
        )
        assert cross_tenant.status_code == 403, cross_tenant.text
        assert cross_tenant.json()["error"]["details"]["reason"] == (
            "CROSS_USER_ACCESS_FORBIDDEN"
        )


def test_unavailable_http_persona_owner_returns_typed_dependency_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unreachable = f"http://127.0.0.1:{_unused_tcp_port()}"
    client, _owner, _read_ports, _openclaw = _install_production_wiring(
        monkeypatch,
        unreachable,
    )

    response = client.post(
        "/bff/agora/servant/ensure",
        headers=_ensure_headers(
            tenant_id="tenant-alpha",
            idempotency_key="production-persona-owner-offline",
            request_id="req-production-persona-owner-offline",
        ),
    )

    assert response.status_code == 503, response.text
    error = response.json()["error"]
    assert error["code"] == "DEPENDENCY_UNAVAILABLE"
    assert error["details"]["precondition_failed"] == (
        "persona_registry_write_owner"
    )


def test_unavailable_http_capability_owner_is_typed_and_persona_stays_draft(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with _running_persona_service(tmp_path, fail_capability_owner=True) as service_url:
        client, owner, _read_ports, _openclaw = _install_production_wiring(
            monkeypatch,
            service_url,
        )
        response = client.post(
            "/bff/agora/servant/ensure",
            headers=_ensure_headers(
                tenant_id="tenant-alpha",
                idempotency_key="production-capability-owner-offline",
                request_id="req-production-capability-owner-offline",
            ),
        )

        assert response.status_code == 503, response.text
        error = response.json()["error"]
        assert error["code"] == "DEPENDENCY_UNAVAILABLE"
        assert error["details"]["precondition_failed"] == (
            "persona_capability_write_owner"
        )
        personas = owner.list_personas()
        assert len(personas) == 1
        assert personas[0]["lifecycle_state"] == "draft"
        assert owner.get_capability_snapshot_for_persona(
            personas[0]["persona_id"]
        ) is None


def test_bff_owner_port_does_not_import_or_open_persona_application_stores() -> None:
    assert isinstance(bff_main.persona_write_owner, PersonaRegistryHttpWritePort)
    module_source = (
        Path(__file__).resolve().parents[1] / "ports" / "persona_write_owner.py"
    ).read_text(encoding="utf-8")
    assert "services.persona" not in module_source
    assert "build_persona_owner" not in module_source
    assert "build_capability_snapshot_owner" not in module_source


def test_http_port_sends_service_credential_and_bounded_timeout() -> None:
    captured: dict[str, Any] = {}

    class _Response:
        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "persona_id": "persona-http-proof",
                    "name": "HTTP Proof",
                    "mandate": "paper opinion",
                    "lifecycle_state": "draft",
                    "created_at": "2026-08-30T00:00:00Z",
                    "owner": "operator",
                    "status": "active",
                    "created_by": _SERVICE_ACTOR,
                    "required_data_sources": [],
                    "metadata": {},
                }
            ).encode("utf-8")

    def opener(request, timeout):
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return _Response()

    port = PersonaRegistryHttpWritePort(
        base_url="http://persona:8002",
        service_token=_SERVICE_TOKEN,
        service_actor_id=_SERVICE_ACTOR,
        timeout_seconds=0.75,
        opener=opener,
    )

    assert port.get_persona("persona-http-proof")["persona_id"] == (
        "persona-http-proof"
    )
    assert captured == {
        "authorization": f"Bearer {_SERVICE_TOKEN}",
        "timeout": 0.75,
    }


def test_http_port_missing_service_credential_fails_before_write_network() -> None:
    called = False

    def opener(_request, _timeout):
        nonlocal called
        called = True
        raise AssertionError("network must not be called")

    port = PersonaRegistryHttpWritePort(
        base_url="http://persona:8002",
        service_token="",
        opener=opener,
    )
    with pytest.raises(PersonaWriteOwnerUnavailable) as captured:
        port.create_persona(
            persona_id="persona-no-token",
            name="No Token",
            actor_id="operator",
            archetype="generalist",
        )

    assert captured.value.dependency == "persona_registry_write_owner"
    assert "credential" in captured.value.reason
    assert called is False


def test_servant_production_http_owner_included_in_interaction_eligibility_e2e(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with _running_persona_service(tmp_path) as service_url:
        client, write_owner, read_ports, _openclaw = _install_production_wiring(
            monkeypatch,
            service_url,
        )
        alpha_headers = _ensure_headers(
            tenant_id="tenant-alpha",
            idempotency_key="production-servant-e2e-alpha",
            request_id="req-production-servant-e2e-alpha",
        )

        # 1. Provision Agora servant through production HTTP owner
        created = client.post("/bff/agora/servant/ensure", headers=alpha_headers)
        assert created.status_code == 200, created.text
        servant_persona_id = created.json()["data"]["persona_id"]
        assert created.json()["data"]["status"] == "paper_only"
        assert created.json()["data"]["policy"]["execution_authority"] == "none"

        # Verify stored persona lifecycle in the real Persona owner service
        stored = read_ports.get_persona(servant_persona_id)
        assert stored is not None
        assert stored["lifecycle_state"] == "research_only"
        assert stored["metadata"]["owner_scope"] == "user_private"
        assert stored["metadata"]["environment_ceiling"] == "paper"
        assert stored["metadata"]["execution_authority"] == "none"

        # 2. Resolve context in workshop for tenant-alpha
        context_res = client.post(
            "/bff/agora/interactions/context:resolve",
            headers={
                "Authorization": "Bearer production-write-owner:operator",
                "X-Tenant-Id": "tenant-alpha",
                "Idempotency-Key": "e2e-context-resolve-1",
            },
            json={
                "environment": "paper",
                "context_refs": [
                    {"type": "decision_event", "id": "dec-evt-1"},
                ],
            },
        )
        assert context_res.status_code == 200, context_res.text
        workshop_id = context_res.json()["data"]["workshop_id"]

        # 3. Check participant eligibility for persona_opinion
        eligible_res = client.post(
            "/bff/agora/interactions/participants:eligible",
            headers={
                "Authorization": "Bearer production-write-owner:operator",
                "X-Tenant-Id": "tenant-alpha",
            },
            json={
                "workshop_id": workshop_id,
                "mode": "consult",
                "environment": "paper",
                "required_capability": "persona_opinion",
            },
        )
        assert eligible_res.status_code == 200, eligible_res.text
        eligible_data = eligible_res.json()["data"]
        included_ids = [p["persona_id"] for p in eligible_data["included"]]
        assert servant_persona_id in included_ids

        servant_entry = next(
            p for p in eligible_data["included"] if p["persona_id"] == servant_persona_id
        )
        assert servant_entry["eligible"] is True
        assert servant_entry["reasons"] == []
        assert servant_entry["capability_snapshot_id"] == stored["metadata"]["capability_snapshot_id"]
        assert servant_entry["participant_snapshot"] is not None
        assert servant_entry["participant_snapshot"]["persona_id"] == servant_persona_id
        assert servant_entry["participant_snapshot"]["environment_ceiling"] == "paper"
        assert servant_entry["participant_snapshot"]["capability_snapshot"] == ["persona_opinion"]

        # 4. Submit interaction with the servant persona
        interaction_res = client.post(
            "/bff/agora/interactions",
            headers={
                "Authorization": "Bearer production-write-owner:operator",
                "X-Tenant-Id": "tenant-alpha",
                "Idempotency-Key": "e2e-submit-interaction-1",
            },
            json={
                "workshop_id": workshop_id,
                "mode": "consult",
                "environment": "paper",
                "topic": "Servant opinion on paper risk",
                "participant_persona_ids": [servant_persona_id],
                "context_refs": [
                    {"type": "decision_event", "id": "dec-evt-1"},
                ],
            },
        )
        assert interaction_res.status_code == 202, interaction_res.text
        interaction_data = interaction_res.json()["data"]
        assert interaction_data["execution_authority"] == "none"
        assert servant_persona_id in [
            p["persona_id"] for p in interaction_data.get("participants", [])
        ]

        # 5. Verify tenant isolation: tenant-beta cannot see tenant-alpha servant
        beta_context_res = client.post(
            "/bff/agora/interactions/context:resolve",
            headers={
                "Authorization": "Bearer production-write-owner:operator",
                "X-Tenant-Id": "tenant-beta",
                "Idempotency-Key": "e2e-context-resolve-beta",
            },
            json={
                "environment": "paper",
                "context_refs": [
                    {"type": "decision_event", "id": "dec-evt-2"},
                ],
            },
        )
        assert beta_context_res.status_code == 200, beta_context_res.text
        beta_workshop_id = beta_context_res.json()["data"]["workshop_id"]

        beta_eligible_res = client.post(
            "/bff/agora/interactions/participants:eligible",
            headers={
                "Authorization": "Bearer production-write-owner:operator",
                "X-Tenant-Id": "tenant-beta",
            },
            json={
                "workshop_id": beta_workshop_id,
                "mode": "consult",
                "environment": "paper",
                "required_capability": "persona_opinion",
            },
        )
        assert beta_eligible_res.status_code == 200, beta_eligible_res.text
        beta_included_ids = [p["persona_id"] for p in beta_eligible_res.json()["data"]["included"]]
        assert servant_persona_id not in beta_included_ids
        beta_excluded = {
            p["persona_id"]: p["reasons"]
            for p in beta_eligible_res.json()["data"]["excluded"]
        }
        assert "tenant_mismatch" in beta_excluded.get(servant_persona_id, [])

        # 6. Verify environment ceiling gate: environment "live" is rejected for paper ceiling
        live_eligible_res = client.post(
            "/bff/agora/interactions/participants:eligible",
            headers={
                "Authorization": "Bearer production-write-owner:operator",
                "X-Tenant-Id": "tenant-alpha",
            },
            json={
                "workshop_id": workshop_id,
                "mode": "consult",
                "environment": "live",
                "required_capability": "persona_opinion",
            },
        )
        assert live_eligible_res.status_code == 200, live_eligible_res.text
        live_included_ids = [p["persona_id"] for p in live_eligible_res.json()["data"]["included"]]
        assert servant_persona_id not in live_included_ids
        live_excluded = {
            p["persona_id"]: p["reasons"]
            for p in live_eligible_res.json()["data"]["excluded"]
        }
        assert "environment_ceiling_exceeded" in live_excluded.get(servant_persona_id, [])
