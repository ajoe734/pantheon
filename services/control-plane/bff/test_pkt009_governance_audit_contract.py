from __future__ import annotations

import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from read_store import ReadSurfaceStore


OPERATOR_TOKEN = "Bearer op-2:operator"


def test_pkt009_governance_audit_filters_and_pagination_follow_contract() -> None:
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
                "/api/v1/operator/governance/audit",
                params={
                    "actor": "operator-jane",
                    "action_type": "ApproveDecision,ForwardToApprovalQueue,RequestGovernanceChanges",
                    "target_type": "GovernanceReviewItem",
                    "from": "2026-04-16T09:00:00Z",
                    "to": "2026-04-16T10:00:00Z",
                    "page_size": 1,
                },
                headers=headers,
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["entries"] == [
                {
                    "entry_id": "audit-002",
                    "actor": "operator-jane",
                    "action_type": "ForwardToApprovalQueue",
                    "target_type": "GovernanceReviewItem",
                    "target_id": "gov-review-001",
                    "timestamp": "2026-04-16T09:58:00Z",
                    "outcome": "success",
                    "audit_context": {
                        "reason": "Review complete; forwarding to approval.",
                    },
                    "evidence_refs": [],
                },
            ]
            assert payload["page_info"]["next_page_token"] == "1"
            assert payload["meta"]["surfaces"]["audit_trail"]["status"] == "degraded"
            assert payload["meta"]["surfaces"]["audit_trail"]["source"] == "local_snapshot"

            page_2 = client.get(
                "/api/v1/operator/governance/audit",
                params={
                    "actor": "operator-jane",
                    "action_type": "ApproveDecision,ForwardToApprovalQueue,RequestGovernanceChanges",
                    "target_type": "GovernanceReviewItem",
                    "from": "2026-04-16T09:00:00Z",
                    "to": "2026-04-16T10:00:00Z",
                    "page_size": 1,
                    "page_token": "1",
                },
                headers=headers,
            )
            assert page_2.status_code == 200, page_2.text
            next_payload = page_2.json()
            assert next_payload["entries"][0]["entry_id"] == "audit-005"
            assert next_payload["page_info"]["next_page_token"] is None
        finally:
            bff_main.read_store = original_store


def test_pkt009_governance_audit_keeps_entries_when_read_surface_is_degraded() -> None:
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
                "/api/v1/operator/governance/audit",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert len(payload["entries"]) >= 5
            assert payload["meta"]["surfaces"]["audit_trail"]["status"] == "degraded"
        finally:
            bff_main.read_store = original_store
            if original_state is None:
                os.environ.pop("BFF_READ_SURFACE_STATE", None)
            else:
                os.environ["BFF_READ_SURFACE_STATE"] = original_state


def test_pkt009_governance_audit_returns_unavailable_surface_in_honest_mode() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=False,
        )
        client = TestClient(bff_main.app)

        try:
            response = client.get(
                "/api/v1/operator/governance/audit",
                headers={"Authorization": OPERATOR_TOKEN},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["entries"] == []
            assert payload["page_info"]["next_page_token"] is None
            assert payload["meta"]["surfaces"]["audit_trail"]["status"] == "unavailable"
            assert payload["meta"]["surfaces"]["audit_trail"]["source"] == "missing"
        finally:
            bff_main.read_store = original_store
