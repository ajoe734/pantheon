from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest
from flask import Flask, jsonify, request
from werkzeug.serving import make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from services.telemetry.auth import (  # noqa: E402
    request_tenant_id,
    require_telemetry_authority,
)
from telemetry_client import (  # noqa: E402
    TelemetryAuthError,
    TelemetryUnavailable,
    fetch_runtime_summaries,
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
