from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from flask import Flask, jsonify, request
from werkzeug.serving import make_server

TESTS_DIR = Path(__file__).resolve().parent
SERVICE_DIR = TESTS_DIR.parent
REPO_ROOT = SERVICE_DIR.parents[1]

for path in (str(TESTS_DIR), str(SERVICE_DIR), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from services.telemetry.auth import (  # noqa: E402
    TelemetryAuthorityError,
    bind_event_tenant,
    request_tenant_id,
    require_telemetry_authority,
)
from telemetry_client import (  # noqa: E402
    TelemetryAuthError,
    TelemetryUnavailable,
    append_lifecycle_event,
    fetch_runtime_summaries,
)
from test_reconciliation_drift_scheduler import (  # noqa: E402
    _load_service_module,
    _paper_lifecycle_summary,
)

TOKEN = "recon-test-token"


@pytest.fixture()
def telemetry_url(monkeypatch):
    monkeypatch.setenv("PANTHEON_TELEMETRY_SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("PANTHEON_TELEMETRY_SERVICE_TENANTS", "tenant-a")
    app = Flask(__name__)

    @app.route("/api/telemetry/runtime-summaries")
    @require_telemetry_authority(("service",))
    def summaries():
        return jsonify({"summaries": [{"runtime_id": "r1", "tenant_id": request_tenant_id()}]})

    @app.route("/api/telemetry/ingest", methods=["POST"])
    @require_telemetry_authority(("service", "operator", "admin"))
    def ingest():
        body = request.get_json(force=True, silent=True)
        if not isinstance(body, dict):
            return jsonify({"status": "rejected", "error": "INVALID_BODY"}), 400
        try:
            body = bind_event_tenant(body, request_tenant_id())
        except TelemetryAuthorityError as exc:
            payload, status = exc.as_response()
            return jsonify(payload), status
        return jsonify({"status": "accepted", "event_id": body.get("event_id")}), 202

    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_credential_and_tenant_are_accepted(telemetry_url):
    result = fetch_runtime_summaries(telemetry_url, tenant_id="tenant-a", service_token=TOKEN)
    assert result == [{"runtime_id": "r1", "tenant_id": "tenant-a"}]


def test_missing_credential_is_auth_failure(telemetry_url):
    with pytest.raises(TelemetryAuthError):
        fetch_runtime_summaries(telemetry_url, tenant_id="tenant-a", service_token="")


def test_wrong_tenant_is_auth_failure(telemetry_url):
    with pytest.raises(TelemetryAuthError):
        fetch_runtime_summaries(telemetry_url, tenant_id="tenant-b", service_token=TOKEN)


def test_unreachable_telemetry_is_unavailable_not_auth():
    with pytest.raises(TelemetryUnavailable) as excinfo:
        fetch_runtime_summaries("http://127.0.0.1:1", tenant_id="t", service_token=TOKEN)
    assert not isinstance(excinfo.value, TelemetryAuthError)


def test_append_lifecycle_event_success(telemetry_url):
    event = {"event_id": "event-1", "tenant_id": "tenant-a"}
    result = append_lifecycle_event(
        telemetry_url,
        event,
        tenant_id="tenant-a",
        service_token=TOKEN,
    )
    assert result["status"] == "accepted"
    assert result["terminal"] is True
    assert result["retryable"] is False
    assert result["outcome"] == "accepted"
    assert result["http_status"] == 202
    assert result["response"] == {"status": "accepted", "event_id": "event-1"}
    assert result["error"] is None


def test_append_lifecycle_event_missing_credential_reproduction(telemetry_url):
    """Reproduces release stimulus failure: unauthenticated append yields 401 terminal rejection."""
    event = {"event_id": "event-1", "tenant_id": "tenant-a"}
    result = append_lifecycle_event(
        telemetry_url,
        event,
        tenant_id="tenant-a",
        service_token="",
    )
    assert result["status"] == "terminal_rejected"
    assert result["terminal"] is True
    assert result["retryable"] is False
    assert result["outcome"] == "failed"
    assert result["http_status"] == 401
    assert result["response"]["error"]["code"] in {"401", "AUTH_TOKEN_MISSING"}


def test_append_lifecycle_event_invalid_token(telemetry_url):
    event = {"event_id": "event-1", "tenant_id": "tenant-a"}
    result = append_lifecycle_event(
        telemetry_url,
        event,
        tenant_id="tenant-a",
        service_token="invalid-token",
    )
    assert result["status"] == "terminal_rejected"
    assert result["terminal"] is True
    assert result["retryable"] is False
    assert result["outcome"] == "failed"
    assert result["http_status"] == 401
    assert result["response"]["error"]["code"] in {"AUTH_TOKEN_FORMAT", "AUTH_TOKEN_INVALID"}


def test_append_lifecycle_event_forbidden_tenant(telemetry_url):
    event = {"event_id": "event-1", "tenant_id": "tenant-b"}
    result = append_lifecycle_event(
        telemetry_url,
        event,
        tenant_id="tenant-b",
        service_token=TOKEN,
    )
    assert result["status"] == "terminal_rejected"
    assert result["terminal"] is True
    assert result["retryable"] is False
    assert result["outcome"] == "failed"
    assert result["http_status"] == 403
    assert result["response"]["error"]["code"] == "TENANT_FORBIDDEN"


def test_append_lifecycle_event_tenant_mismatch(telemetry_url):
    event = {"event_id": "event-1", "tenant_id": "tenant-b"}
    result = append_lifecycle_event(
        telemetry_url,
        event,
        tenant_id="tenant-a",
        service_token=TOKEN,
    )
    assert result["status"] == "terminal_rejected"
    assert result["terminal"] is True
    assert result["retryable"] is False
    assert result["outcome"] == "failed"
    assert result["http_status"] == 403
    assert result["response"]["error"]["code"] in {"TENANT_PAYLOAD_MISMATCH", "TENANT_MISMATCH"}


def test_append_lifecycle_event_unreachable_is_retryable():
    event = {"event_id": "event-1", "tenant_id": "tenant-a"}
    result = append_lifecycle_event(
        "http://127.0.0.1:1",
        event,
        tenant_id="tenant-a",
        service_token=TOKEN,
    )
    assert result["status"] == "retryable_error"
    assert result["terminal"] is False
    assert result["retryable"] is True
    assert result["outcome"] == "ambiguous"
    assert result["http_status"] is None


def test_append_lifecycle_event_defaults_from_env(telemetry_url, monkeypatch):
    monkeypatch.setenv("PANTHEON_TELEMETRY_SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("PANTHEON_TENANT_ID", "tenant-a")
    event = {"event_id": "event-env-1", "tenant_id": "tenant-a"}
    result = append_lifecycle_event(telemetry_url, event)
    assert result["status"] == "accepted"
    assert result["terminal"] is True
    assert result["outcome"] == "accepted"
    assert result["http_status"] == 202


def test_scheduled_reconcile_with_authenticated_telemetry_success(telemetry_url, monkeypatch):
    monkeypatch.setenv("PANTHEON_TELEMETRY_SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("PANTHEON_TENANT_ID", "tenant-a")
    monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", telemetry_url)

    summary = _paper_lifecycle_summary()
    summary["last_lifecycle_identity"]["tenant_id"] = "tenant-a"
    summary["last_lifecycle_identity"]["correlation_envelope"]["tenant_id"] = "tenant-a"
    binding_id = summary["binding_id"]
    tick_id = "tick-auth-telemetry-001"

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        with (
            mock.patch.object(svc, "fetch_runtime_summaries", return_value=[summary]),
            mock.patch.object(
                svc,
                "_classify_drift_report_incident",
                side_effect=AssertionError("incident dispatch should be skipped"),
            ),
        ):
            response = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={
                    "tick_id": tick_id,
                    "binding_id": binding_id,
                    "dispatch_incidents": False,
                    "lifecycle_only": True,
                },
                headers={"X-Tenant-Id": "tenant-a"},
            )

        assert response.status_code == 201
        payload = response.json()
        assert payload["status"] == "ok"
        assert len(payload["lifecycle_accepted_event_ids"]) == 1
        assert payload["lifecycle_append_results"][0]["status"] == "accepted"
        assert payload["lifecycle_append_results"][0]["http_status"] == 202
        assert payload["lifecycle_terminal_rejections"] == []


def test_scheduled_reconcile_missing_token_reproduces_stimulus_failure(telemetry_url, monkeypatch):
    """Reproduces release stimulus failure: unauthenticated scheduled reconcile fails with terminal rejection."""
    monkeypatch.setenv("PANTHEON_TELEMETRY_SERVICE_TOKEN", "")
    monkeypatch.setenv("PANTHEON_TENANT_ID", "tenant-a")
    monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", telemetry_url)

    summary = _paper_lifecycle_summary()
    summary["last_lifecycle_identity"]["tenant_id"] = "tenant-a"
    summary["last_lifecycle_identity"]["correlation_envelope"]["tenant_id"] = "tenant-a"
    binding_id = summary["binding_id"]
    tick_id = "tick-reproduce-stimulus-001"

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        with (
            mock.patch.object(svc, "fetch_runtime_summaries", return_value=[summary]),
            mock.patch.object(
                svc,
                "_classify_drift_report_incident",
                side_effect=AssertionError("incident dispatch should be skipped"),
            ),
        ):
            response = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={
                    "tick_id": tick_id,
                    "binding_id": binding_id,
                    "dispatch_incidents": False,
                    "lifecycle_only": True,
                },
                headers={"X-Tenant-Id": "tenant-a"},
            )

        assert response.status_code == 201
        payload = response.json()
        assert payload["status"] == "failure"
        assert len(payload["lifecycle_terminal_rejections"]) == 1
        rejection = payload["lifecycle_terminal_rejections"][0]
        assert rejection["status"] == "terminal_rejected"
        assert rejection["outcome"] == "failed"
        assert rejection["http_status"] == 401
        assert rejection["retryable"] is False
        assert rejection["terminal"] is True
