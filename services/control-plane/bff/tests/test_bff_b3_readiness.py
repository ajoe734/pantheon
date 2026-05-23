"""
BFF-B3-008: contract tests for Management readiness aggregates.

The five routes are read-only BFF Management surfaces. They publish EP5,
broker-live, capital-binding-live, BFF HA, and strict-publish readiness without
enabling live broker, live capital, or production topology changes.
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from read_store import ReadSurfaceStore


OPERATOR_HEADERS = {"Authorization": "Bearer op-b3-readiness:operator"}


@contextmanager
def _readiness_client() -> Iterator[TestClient]:
    tracked_env = {
        "PANTHEON_LIVE_BROKER_ENABLED": os.environ.get("PANTHEON_LIVE_BROKER_ENABLED"),
        "OPENCLAW_CAPITAL_BINDING_ENABLED": os.environ.get("OPENCLAW_CAPITAL_BINDING_ENABLED"),
        "PANTHEON_CAPITAL_BINDING_LIVE_ENABLED": os.environ.get("PANTHEON_CAPITAL_BINDING_LIVE_ENABLED"),
    }
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        os.environ["PANTHEON_LIVE_BROKER_ENABLED"] = "false"
        os.environ["OPENCLAW_CAPITAL_BINDING_ENABLED"] = "false"
        os.environ["PANTHEON_CAPITAL_BINDING_LIVE_ENABLED"] = "false"
        try:
            store = ReadSurfaceStore(
                os.path.join(td, "read_surfaces.json"),
                allow_local_snapshot_fallback=True,
            )
            store.get_openclaw_broker_adapter_readiness = lambda: {
                "surface": "openclaw_broker_adapter_readiness",
                "overall_status": "ok",
                "live_adapter_state": "fail_closed",
                "live_execution_enabled": False,
                "canary_execution_enabled": False,
                "is_real_capital": False,
                "is_real_order": False,
                "gate_reason": "fail_closed_explicit_gate_required",
                "service_status": {"status": "ok", "source": "test"},
            }
            bff_main.read_store = store
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def test_management_readiness_routes_publish_fail_closed_status() -> None:
    routes = {
        "ep5": "/bff/management/readiness/ep5",
        "broker-live": "/bff/management/readiness/broker-live",
        "capital-binding-live": "/bff/management/readiness/capital-binding-live",
        "bff-ha": "/bff/management/readiness/bff-ha",
        "strict-publish": "/bff/management/readiness/strict-publish",
    }
    with _readiness_client() as client:
        for readiness_id, path in routes.items():
            response = client.get(path, headers=OPERATOR_HEADERS)

            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["data"]["id"] == readiness_id
            assert payload["data"]["canProceed"] is False
            assert payload["summary"]["readinessStatus"] == "blocked"
            assert payload["summary"]["blockingReasonCount"] >= 1
            assert payload["checks"]
            assert payload["meta"]["surfaces"][f"management_readiness_{readiness_id.replace('-', '_')}"][
                "source"
            ] == "bff_composed"


def test_management_broker_live_readiness_uses_operator_read_auth_and_no_live_side_effects() -> None:
    with _readiness_client() as client:
        response = client.get("/bff/management/readiness/broker-live", headers=OPERATOR_HEADERS)

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["data"]["details"]["fail_closed"] is True
        assert payload["data"]["details"]["broker_readiness"]["live_execution_enabled"] is False
        assert payload["data"]["details"]["broker_readiness"]["is_real_capital"] is False
        gate_check = next(check for check in payload["checks"] if check["id"] == "live_broker_gate")
        assert gate_check["status"] == "blocked"
        assert gate_check["blocking"] is True


def test_management_strict_publish_readiness_exposes_audit_blocker() -> None:
    with _readiness_client() as client:
        response = client.get("/bff/management/readiness/strict-publish", headers=OPERATOR_HEADERS)

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["data"]["details"]["passed"] is False
        forbidden_scan = next(check for check in payload["checks"] if check["id"] == "forbidden_path_scan")
        assert forbidden_scan["status"] == "fail"
        assert forbidden_scan["blocking"] is True
        assert forbidden_scan["details"]["forbidden_signal_count"] >= 1


def test_management_readiness_requires_authentication() -> None:
    with _readiness_client() as client:
        response = client.get("/bff/management/readiness/ep5")

        assert response.status_code == 401, response.text
        assert response.json()["detail"]["error"]["code"] == "INVALID_TOKEN"
