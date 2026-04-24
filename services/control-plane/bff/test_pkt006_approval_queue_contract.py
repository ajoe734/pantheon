from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore


APPROVER_TOKEN = "Bearer op-6:approver"


def test_pkt006_approval_queue_filters_and_pagination_follow_contract() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/governance/approval-queue",
                params={
                    "decision_type": "DeploymentPlan,PersonaBinding",
                    "risk_level": "low,medium",
                    "decision_state": "pending,in_review",
                    "page_size": 1,
                },
                headers={"Authorization": APPROVER_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["items"] == [
                {
                    "decision_id": "appr-001",
                    "decision_type": "DeploymentPlan",
                    "risk_level": "medium",
                    "submitted_at": "2026-04-16T08:15:00Z",
                    "submitted_by": "governance-review-queue",
                    "decision_state": "pending",
                    "allowedActions": {
                        "canApprove": True,
                        "canReject": True,
                        "canRequestRevision": True,
                    },
                    "decision_context": {
                        "risk_summary": "Medium risk — parameter drift within acceptable bounds; no open severity-1 or severity-2 incidents. Review queue forwarded after passing risk threshold check.",
                        "evidence_refs": [
                            {"ref_id": "ev-101", "type": "BacktestResult", "url": None},
                            {"ref_id": "ev-102", "type": "IncidentReport", "url": None},
                        ],
                        "governance_chain": {
                            "linked_review_item_id": "gov-review-001",
                        },
                        "required_approvals": 1,
                    },
                },
            ]
            assert payload["page_info"]["next_page_token"] == "1"
            assert payload["meta"]["surfaces"]["approval_queue"]["status"] == "degraded"
            assert payload["meta"]["surfaces"]["approval_queue"]["source"] == "local_snapshot"
            assert payload["meta"]["surfaces"]["allowedActions"]["status"] == "degraded"
        finally:
            bff_main.read_store = original_store


def test_pkt006_approval_queue_returns_unavailable_surface_in_honest_mode() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/governance/approval-queue",
                headers={"Authorization": APPROVER_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["items"] == []
            assert payload["page_info"]["next_page_token"] is None
            assert payload["meta"]["surfaces"]["approval_queue"]["status"] == "unavailable"
            assert payload["meta"]["surfaces"]["approval_queue"]["source"] == "missing"
            assert payload["meta"]["surfaces"]["allowedActions"]["status"] == "unavailable"
        finally:
            bff_main.read_store = original_store
