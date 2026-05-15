from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore


OPERATOR_TOKEN = "Bearer op-2:operator"


def test_pkt007_deployment_diff_returns_contract_payload() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/deployment-diff/plan-dp-001",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["plan_id"] == "plan-dp-001"
            assert payload["artifact_id"] == "artifact-abc123"
            assert payload["previous_plan_id"] == "plan-dp-000"
            assert payload["first_deployment"] is False
            assert len(payload["changes"]) == 4
            assert payload["change_summary"]["total_changes"] == 4
            assert payload["change_summary"]["by_category"]["bindings"]["highest_risk_tier"] == "high"
            assert payload["allowedActions"]["canProceedToApproval"] is True
            assert payload["allowedActions"]["canEscalateDiff"] is True
            assert payload["meta"]["surfaces"]["deployment_diff"]["status"] == "degraded"
            assert payload["meta"]["surfaces"]["deployment_diff"]["source"] == "local_snapshot"
            assert payload["meta"]["surfaces"]["allowedActions"]["status"] == "degraded"
        finally:
            bff_main.read_store = original_store


def test_pkt007_deployment_diff_supports_first_deployment_shape() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/deployment-diff/plan-dp-002",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["plan_id"] == "plan-dp-002"
            assert payload["previous_plan_id"] is None
            assert payload["first_deployment"] is True
            assert payload["changes"] == []
            assert payload["change_summary"]["total_changes"] == 0
            assert payload["allowedActions"]["canProceedToApproval"] is False
            assert payload["allowedActions"]["canEscalateDiff"] is True
        finally:
            bff_main.read_store = original_store


def test_pkt007_deployment_diff_returns_unavailable_payload_in_honest_mode() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/deployment-diff/plan-dp-001",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["plan_id"] == "plan-dp-001"
            assert payload["artifact_id"] is None
            assert payload["changes"] == []
            assert payload["allowedActions"]["canProceedToApproval"] is False
            assert payload["meta"]["surfaces"]["deployment_diff"]["status"] == "unavailable"
            assert payload["meta"]["surfaces"]["deployment_diff"]["source"] == "missing"
            assert payload["meta"]["surfaces"]["allowedActions"]["status"] == "unavailable"
        finally:
            bff_main.read_store = original_store
