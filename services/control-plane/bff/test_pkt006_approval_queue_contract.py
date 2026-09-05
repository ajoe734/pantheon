from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


APPROVER_TOKEN = "Bearer op-6:approver"


def _approval_queue_items(*, decision_types=None, risk_levels=None, decision_states=None):
    items = [
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
                "governance_chain": {"linked_review_item_id": "gov-review-001"},
                "required_approvals": 1,
            },
        },
        {
            "decision_id": "appr-002",
            "decision_type": "PersonaBinding",
            "risk_level": "low",
            "submitted_at": "2026-04-16T09:00:00Z",
            "submitted_by": "governance-review-queue",
            "decision_state": "pending",
            "allowedActions": {
                "canApprove": True,
                "canReject": True,
                "canRequestRevision": False,
            },
            "decision_context": {
                "risk_summary": "Low risk — new persona binding within established capital pool; no open incidents.",
                "evidence_refs": [],
                "governance_chain": {"linked_review_item_id": "gov-review-003"},
                "required_approvals": 1,
            },
        },
    ]
    if decision_types:
        items = [item for item in items if item["decision_type"] in decision_types]
    if risk_levels:
        items = [item for item in items if item["risk_level"] in risk_levels]
    if decision_states:
        items = [item for item in items if item["decision_state"] in decision_states]
    return items


def test_pkt006_approval_queue_filters_and_pagination_follow_contract() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = create_in_memory_read_surface_ports()
        store.list_approval_queue_items = _approval_queue_items
        store.dataset_source = lambda dataset: "local_snapshot" if dataset == "approval_queue_items" else "missing"
        bff_main.read_store = store
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
        store = create_in_memory_read_surface_ports()
        store.dataset_source = lambda dataset: "missing"
        bff_main.read_store = store
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
