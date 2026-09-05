from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


OPERATOR_TOKEN = "Bearer op-2:operator"


def test_pkt001_deployment_review_console_list_route_matches_contract() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = create_in_memory_read_surface_ports()
        plans = [
            {
                "id": "plan-approved-001",
                "plan_id": "plan-approved-001",
                "artifact_id": "artifact-approved-001",
                "target_stage": "paper",
                "stage": "paper",
                "status": "approved",
                "approval_decision_id": "approval-approved-001",
                "submitted_at": "2026-04-17T08:00:00Z",
            },
            {
                "id": "plan-pending-002",
                "plan_id": "plan-pending-002",
                "artifact_id": "artifact-pending-002",
                "target_stage": "live",
                "stage": "live",
                "status": "pending_review",
                "approval_decision_id": "approval-pending-002",
                "submitted_at": "2026-04-18T09:30:00Z",
            },
        ]
        decisions = {
            "approval-approved-001": {
                "id": "approval-approved-001",
                "outcome": "approved",
                "state": "decided",
                "risk_level": "low",
            },
            "approval-pending-002": {
                "id": "approval-pending-002",
                "outcome": "pending",
                "state": "pending",
                "risk_level": "medium",
            },
        }
        reviews = {
            "plan-approved-001": {"governanceOutcome": "approved"},
            "plan-pending-002": {"governanceOutcome": "pending"},
        }
        allowed_actions = {
            "plan-approved-001": {
                "canApprove": False,
                "canReject": False,
                "canPromoteToPaper": True,
            },
            "plan-pending-002": {
                "canApprove": True,
                "canReject": True,
                "canPromoteToPaper": False,
            },
        }

        store.list_deployment_plans = lambda status=None, capital_pool_id=None: list(plans)
        store.get_approval_decision = lambda decision_id: decisions.get(decision_id)
        store.get_review_summary = lambda plan_id: reviews.get(plan_id)
        store.get_allowed_actions = lambda plan_id: allowed_actions.get(plan_id, {})
        store.dataset_source = lambda dataset: "service_store"
        bff_main.read_store = store
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/deployment-plans",
                params={"page_size": 1},
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["items"] == [
                {
                    "plan_id": "plan-pending-002",
                    "artifact_id": "artifact-pending-002",
                    "target_stage": "live",
                    "risk_level": "medium",
                    "governance_outcome": "pending",
                    "submitted_at": "2026-04-18T09:30:00Z",
                }
            ]
            assert payload["page_info"]["next_page_token"] == "1"
            assert payload["meta"]["surfaces"]["deployment_plans"]["status"] == "ok"
            assert payload["meta"]["surfaces"]["allowedActions"]["status"] == "ok"
            assert "degradation" not in payload["meta"]

            page_2 = client.get(
                "/api/v1/operator/deployment-plans",
                params={
                    "status": "approved,pending_review",
                    "page_size": 1,
                    "page_token": "1",
                },
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert page_2.status_code == 200, page_2.text
            next_payload = page_2.json()
            assert next_payload["items"] == [
                {
                    "plan_id": "plan-approved-001",
                    "artifact_id": "artifact-approved-001",
                    "target_stage": "paper",
                    "risk_level": "low",
                    "governance_outcome": "approved",
                    "submitted_at": "2026-04-17T08:00:00Z",
                }
            ]
            assert next_payload["page_info"]["next_page_token"] is None
        finally:
            bff_main.read_store = original_store


def test_pkt001_deployment_review_console_list_route_honest_mode_returns_unavailable_surfaces() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = create_in_memory_read_surface_ports()
        store.dataset_source = lambda dataset: "missing"
        bff_main.read_store = store
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/deployment-plans",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["items"] == []
            assert payload["page_info"]["next_page_token"] is None
            assert payload["meta"]["surfaces"]["deployment_plans"]["status"] == "unavailable"
            assert payload["meta"]["surfaces"]["allowedActions"]["status"] == "unavailable"
            assert payload["meta"]["degradation"]["disable_ctas"] is True
        finally:
            bff_main.read_store = original_store


def test_pkt001_deployment_review_detail_includes_full_allowed_actions_shape() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = create_in_memory_read_surface_ports()
        store.get_deployment_plan = lambda plan_id: {
            "plan_id": "plan-F-042",
            "capital_pool_id": "pool-main",
            "approval_decision_id": "approval-042",
        } if plan_id == "plan-F-042" else None
        store.get_allowed_actions = lambda plan_id: {
            "canApprove": False,
            "canReject": False,
            "canPromoteToPaper": True,
        } if plan_id == "plan-F-042" else {}
        store.dataset_source = lambda dataset: "local_snapshot"
        bff_main.read_store = store
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/deployment-review/plan-F-042",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["data"]["allowedActions"] == {
                "canApprove": False,
                "canReject": False,
                "canPromoteToPaper": True,
            }
            assert payload["meta"]["surfaces"]["allowedActions"]["status"] in {"ok", "degraded"}
            if payload["meta"]["surfaces"]["allowedActions"]["status"] != "ok":
                assert payload["meta"]["degradation"]["disable_ctas"] is True
        finally:
            bff_main.read_store = original_store
