"""BFF telemetry summaries read the authenticated Telemetry owner projection."""
from __future__ import annotations

import json
import time
import urllib.error

import pytest

from services.control_plane.bff.core import owner_reads
from services.control_plane.bff.ports.lifecycle_telemetry_governance import DomainTelemetryPort
from services.control_plane.bff.ports.read_surface_ports import create_read_surface_ports
from services.runtime_auth_inbound import encode_jwt_hs256


@pytest.fixture()
def telemetry_owner(monkeypatch):
    from services.telemetry import main as telemetry_main

    secret = "telemetry-owner-test-secret"
    monkeypatch.setenv("PANTHEON_TELEMETRY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_TELEMETRY_JWT_SECRET", secret)
    monkeypatch.setenv("PANTHEON_TELEMETRY_DEFAULT_ROLE", "")
    monkeypatch.setenv("PANTHEON_TELEMETRY_ALLOWED_TENANTS", "tenant-a,tenant-b")
    summaries = [
        {"runtime_id": "runtime-a", "tenant_id": "tenant-a", "total_trades": 4},
        {"runtime_id": "runtime-b", "tenant_id": "tenant-b", "total_trades": 8},
    ]
    calls = []

    class SummaryService:
        def list_runtime_summaries(self, *, tenant_id):
            calls.append(tenant_id)
            return [row for row in summaries if row["tenant_id"] == tenant_id]

    monkeypatch.setattr(telemetry_main, "_get_service", lambda: SummaryService())
    token = encode_jwt_hs256(
        {"sub": "bff-owner-reader", "roles": ["operator"], "tenant_id": "tenant-a",
         "allowed_tenants": ["tenant-a", "tenant-b"], "exp": int(time.time()) + 3600},
        secret=secret,
    )
    client = telemetry_main.app.test_client()

    def transport(url, *, auth_token=None, tenant_id=None, **_kwargs):
        response = client.get(
            url.removeprefix("http://telemetry-owner"),
            headers={
                **({"Authorization": auth_token if auth_token.startswith("Bearer ") else f"Bearer {auth_token}"} if auth_token else {}),
                **({"X-Tenant-Id": tenant_id} if tenant_id else {}),
            },
        )
        if response.status_code >= 400:
            raise urllib.error.HTTPError(url, response.status_code, response.text, {}, None)
        return response.get_json()

    monkeypatch.setattr(owner_reads, "http_request_json", transport)
    monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", "http://telemetry-owner")
    monkeypatch.delenv("PANTHEON_TELEMETRY_URL", raising=False)
    return token, calls


def _bind_request(token, tenant):
    auth_token = owner_reads.authorization.set(f"Bearer {token}" if token else None)
    tenant_token = owner_reads.selected_tenant.set(tenant)
    return auth_token, tenant_token


def _reset_request(tokens):
    auth_token, tenant_token = tokens
    owner_reads.selected_tenant.reset(tenant_token)
    owner_reads.authorization.reset(auth_token)


def test_production_factory_reads_real_telemetry_route_with_caller_and_selected_tenant(telemetry_owner):
    token, calls = telemetry_owner
    ports = create_read_surface_ports()
    context = _bind_request(token, "tenant-b")
    try:
        summaries = ports.list_telemetry_summaries()
        assert summaries == [{"runtime_id": "runtime-b", "tenant_id": "tenant-b", "total_trades": 8}]
        assert ports.get_telemetry_summary("runtime-b")["tenant_id"] == "tenant-b"
        assert calls == ["tenant-b", "tenant-b"]
        assert ports.dataset_source("telemetry_summaries") == "service"
    finally:
        _reset_request(context)


def test_cookie_session_context_reaches_telemetry_owner_when_authorization_header_is_absent(telemetry_owner):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware

    token, calls = telemetry_owner
    ports = create_read_surface_ports()
    app = FastAPI()
    app.add_middleware(OwnerReadContextMiddleware)

    @app.get("/telemetry-summaries")
    def summaries():
        return {"items": ports.list_telemetry_summaries()}

    response = TestClient(app).get(
        "/telemetry-summaries",
        cookies={"pantheon_session": token},
        headers={"X-Tenant-Id": "tenant-a"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"] == [{"runtime_id": "runtime-a", "tenant_id": "tenant-a", "total_trades": 4}]
    assert calls == ["tenant-a"]


def test_owner_summary_reads_require_request_authorization(telemetry_owner):
    _token, _calls = telemetry_owner
    port = DomainTelemetryPort(telemetry_summaries_reader=owner_reads.telemetry_summaries)
    context = _bind_request(None, "tenant-a")
    try:
        with pytest.raises(RuntimeError, match="caller's authorization"):
            port.list_telemetry_summaries()
        assert port.dataset_source() == "unavailable"
    finally:
        _reset_request(context)


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_are_unavailable_not_empty(monkeypatch, status):
    port = DomainTelemetryPort(telemetry_summaries_reader=lambda: _raise_http(status))
    monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", "http://telemetry-owner")
    assert port.dataset_source() == "unavailable"
    with pytest.raises(urllib.error.HTTPError):
        port.list_telemetry_summaries()


def _raise_http(status):
    raise urllib.error.HTTPError("http://telemetry-owner/api/telemetry/runtime-summaries", status, "denied", {}, None)


def test_timeout_and_malformed_owner_payload_are_unavailable(monkeypatch):
    monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", "http://telemetry-owner")
    timed_out = DomainTelemetryPort(telemetry_summaries_reader=lambda: (_ for _ in ()).throw(TimeoutError()))
    malformed = DomainTelemetryPort(telemetry_summaries_reader=lambda: {"summaries": []})
    assert timed_out.dataset_source() == "unavailable"
    assert malformed.dataset_source() == "unavailable"
    with pytest.raises(TimeoutError):
        timed_out.list_telemetry_summaries()
    with pytest.raises(RuntimeError, match="Invalid Telemetry owner"):
        malformed.list_telemetry_summaries()


def test_unconfigured_owner_is_missing_and_explicit_injection_stays_local(monkeypatch):
    monkeypatch.delenv("PANTHEON_TELEMETRY_API_URL", raising=False)
    monkeypatch.delenv("PANTHEON_TELEMETRY_URL", raising=False)
    production = DomainTelemetryPort(telemetry_summaries_reader=owner_reads.telemetry_summaries)
    injected = DomainTelemetryPort(telemetry_summaries=[])
    assert production.dataset_source() == "missing"
    assert injected.dataset_source() == "typed_store"
    assert injected.list_telemetry_summaries() == []


def test_in_memory_factory_preserves_explicit_telemetry_fixture():
    port = DomainTelemetryPort(telemetry_summaries=[{"runtime_id": "fixture-runtime"}])
    assert port.dataset_source() == "typed_store"
    assert json.loads(json.dumps(port.list_telemetry_summaries())) == [{"runtime_id": "fixture-runtime"}]
