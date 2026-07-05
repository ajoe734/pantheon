"""Backport guard for the management shell-summary contract used by FE gates."""
from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient

os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")
os.environ.setdefault("PANTHEON_BFF_AUTH_MODE", "permissive")
sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main  # noqa: E402


AUTH_HEADERS = {"Authorization": "Bearer op-shell-summary:operator,reviewer"}
NO_ROLE_HEADERS = {"Authorization": "Bearer op-bogus-session:none"}


def test_shell_summary_returns_frontend_contract_shape() -> None:
    bff_main._SHELL_SUMMARY_COUNT_CACHE.clear()
    client = TestClient(bff_main.app, raise_server_exceptions=False)

    response = client.get("/bff/management/shell-summary", headers=AUTH_HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    data = payload["data"]
    counts = data["counts"]
    assert set(counts) == {"pending_approvals", "open_alerts", "running_jobs"}
    assert all(isinstance(value, int) for value in counts.values())
    assert data["session"]["operator_id"] == "op-shell-summary"
    assert data["transport"]["bff_status"] == "ok"
    surfaces = payload["meta"]["surfaces"]
    assert surfaces["shell_summary"]["status"] in {"ok", "degraded", "unavailable"}
    assert "/bff/management/shell-summary" in bff_main.app.openapi()["paths"]


def test_shell_summary_fails_closed_for_no_role_stub_token() -> None:
    client = TestClient(bff_main.app, raise_server_exceptions=False)

    response = client.get("/bff/management/shell-summary", headers=NO_ROLE_HEADERS)

    assert response.status_code == 403, response.text
