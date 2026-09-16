from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from ports import create_in_memory_read_surface_ports


OPERATOR_TOKEN = "Bearer op-2:operator"


def _review_queue_items(*, item_types=None, risk_levels=None, statuses=None):
    items = [
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
        {
            "item_id": "gov-review-002",
            "item_type": "EvolutionProposal",
            "risk_level": "high",
            "submitted_at": "2026-04-14T05:45:00Z",
            "submitted_by": "orchestrator",
            "governance_outcome": "escalated",
            "allowedActions": {
                "canReview": False,
                "canForwardToApproval": False,
                "canRequestChanges": False,
                "canEscalate": False,
            },
            "review_summary": {"risk_assessment": "Escalated governance item.", "evidence_refs": []},
        },
        {
            "item_id": "gov-review-003",
            "item_type": "PersonaBinding",
            "risk_level": "low",
            "submitted_at": "2026-04-14T07:00:00Z",
            "submitted_by": "orchestrator",
            "governance_outcome": "pending",
            "allowedActions": {
                "canReview": True,
                "canForwardToApproval": True,
                "canRequestChanges": True,
                "canEscalate": True,
            },
            "review_summary": {"risk_assessment": "Low risk persona binding.", "evidence_refs": []},
        },
    ]
    if item_types:
        items = [item for item in items if item["item_type"] in item_types]
    if risk_levels:
        items = [item for item in items if item["risk_level"] in risk_levels]
    if statuses:
        items = [item for item in items if item["governance_outcome"] in statuses]
    return items


def test_pkt001_governance_review_queue_filters_and_pagination_follow_contract() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        store = create_in_memory_read_surface_ports()
        store.list_governance_review_queue_items = _review_queue_items
        store.dataset_source = lambda dataset: "local_snapshot" if dataset == "governance_review_queue_items" else "missing"
        bff_main.read_store = store
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
        store = create_in_memory_read_surface_ports()
        store.list_governance_review_queue_items = _review_queue_items
        store.dataset_source = lambda dataset: "local_snapshot" if dataset == "governance_review_queue_items" else "missing"
        bff_main.read_store = store
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
        store = create_in_memory_read_surface_ports()
        store.dataset_source = lambda dataset: "missing"
        bff_main.read_store = store
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
