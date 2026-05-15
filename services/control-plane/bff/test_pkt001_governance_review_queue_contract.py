from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore


OPERATOR_TOKEN = "Bearer op-2:operator"


def test_pkt001_governance_review_queue_filters_and_pagination_follow_contract() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        client = TestClient(bff_main.app)

        try:
            headers = {"Authorization": OPERATOR_TOKEN}
            response = client.get(
                "/api/v1/operator/governance/review-queue",
                params={
                    "item_type": "DeploymentPlan,PersonaBinding",
                    "risk_level": "low,medium",
                    "status": "pending,in_review",
                    "page_size": 1,
                },
                headers=headers,
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["items"] == [
                {
                    "item_id": "gov-review-001",
                    "item_type": "DeploymentPlan",
                    "risk_level": "medium",
                    "submitted_at": "2026-04-14T06:15:00Z",
                    "submitted_by": "orchestrator",
                    "governance_outcome": "pending",
                    "allowedActions": {
                        "canReview": True,
                        "canForwardToApproval": True,
                        "canRequestChanges": True,
                        "canEscalate": False,
                    },
                    "review_summary": {
                        "risk_assessment": "Medium risk - parameter drift within acceptable bounds; no open severity-1 or severity-2 incidents.",
                        "evidence_refs": [
                            {"ref_id": "ev-001", "type": "IncidentReport", "url": None},
                            {"ref_id": "ev-002", "type": "BacktestResult", "url": None},
                        ],
                        "linked_approval_decision_id": None,
                    },
                },
            ]
            assert payload["page_info"]["next_page_token"] == "1"
            assert payload["meta"]["surfaces"]["review_queue"]["status"] == "degraded"
            assert payload["meta"]["surfaces"]["review_queue"]["source"] == "local_snapshot"
            assert payload["meta"]["surfaces"]["allowedActions"]["status"] == "degraded"

            page_2 = client.get(
                "/api/v1/operator/governance/review-queue",
                params={
                    "item_type": "DeploymentPlan,PersonaBinding",
                    "risk_level": "low,medium",
                    "status": "pending,in_review",
                    "page_size": 1,
                    "page_token": "1",
                },
                headers=headers,
            )
            assert page_2.status_code == 200, page_2.text
            next_payload = page_2.json()
            assert next_payload["items"][0]["item_id"] == "gov-review-003"
            assert next_payload["page_info"]["next_page_token"] is None
        finally:
            bff_main.read_store = original_store


def test_pkt001_governance_review_queue_keeps_items_when_read_surface_is_degraded() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_state = os.environ.get("BFF_READ_SURFACE_STATE")
        os.environ["BFF_READ_SURFACE_STATE"] = "degraded"
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/governance/review-queue",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert len(payload["items"]) == 3
            assert payload["meta"]["surfaces"]["review_queue"]["status"] == "degraded"
            assert payload["meta"]["surfaces"]["allowedActions"]["status"] == "degraded"
        finally:
            bff_main.read_store = original_store
            if original_state is None:
                os.environ.pop("BFF_READ_SURFACE_STATE", None)
            else:
                os.environ["BFF_READ_SURFACE_STATE"] = original_state


def test_pkt001_governance_review_queue_returns_unavailable_surface_in_honest_mode() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/governance/review-queue",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["items"] == []
            assert payload["page_info"]["next_page_token"] is None
            assert payload["meta"]["surfaces"]["review_queue"]["status"] == "unavailable"
            assert payload["meta"]["surfaces"]["review_queue"]["source"] == "missing"
            assert payload["meta"]["surfaces"]["allowedActions"]["status"] == "unavailable"
        finally:
            bff_main.read_store = original_store
