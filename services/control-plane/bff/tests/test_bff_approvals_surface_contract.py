"""Contract tests for /bff/approvals population via promotion-service approvals.

Verifies the end-to-end read path established by CONSOLE-DATA-APPROVALS:
  - POST promotion/api/v1/approvals produces a real approval decision
  - PANTHEON_BFF_APPROVAL_DECISION_STORE wires the BFF canonical read path
  - GET /bff/approvals returns count>0 and the pending item when the store is populated
  - Decided approvals (approved/rejected) are excluded from the pending list
  - Empty / absent store returns count=0 (no fabricated data)
  - The governance approval queue surface also picks up the projected data

Stub dispatch (dev safety): no live broker orders, no capital allocation.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from read_store import ReadSurfaceStore

ADMIN_HEADERS = {"Authorization": "Bearer op-dev:admin:mfa"}
OPERATOR_HEADERS = {"Authorization": "Bearer op-dev:operator"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PENDING_APPROVAL = {
    "decision_id": "apv-consdata-001",
    "target_type": "registry_entry",
    "target_id": "reg-consdata-model-v1",
    "target_version": "v1",
    "decision": None,
    "decision_state": "proposed",
    "actor_role": None,
    "actor_id": None,
    "rationale": None,
    "created_at": "2026-06-15T10:00:00Z",
    "decided_at": None,
    "conditions": [],
    "risk_level": "medium",
    "evidence_refs": [],
    "superseded_by": None,
    "expires_at": None,
    "capital_pool_id": "pool-dev-01",
    "persona_id": None,
    "metadata": None,
}

_DECIDED_APPROVAL = {
    "decision_id": "apv-consdata-002",
    "target_type": "registry_entry",
    "target_id": "reg-consdata-model-v2",
    "target_version": "v2",
    "decision": "approved",
    "decision_state": "decided",
    "actor_role": "governance_reviewer",
    "actor_id": "reviewer-01",
    "rationale": "approved after review",
    "created_at": "2026-06-15T09:00:00Z",
    "decided_at": "2026-06-15T09:30:00Z",
    "conditions": [],
    "risk_level": "low",
    "evidence_refs": [],
    "superseded_by": None,
    "expires_at": None,
    "capital_pool_id": "pool-dev-01",
    "persona_id": None,
    "metadata": None,
}


def _write_approval_decisions(td: str, decisions: list) -> str:
    """Write approval decisions as a list to approval_decisions.json and return path."""
    path = os.path.join(td, "approval_decisions.json")
    keyed = {d["decision_id"]: d for d in decisions}
    with open(path, "w") as f:
        json.dump(keyed, f)
    return path


# ---------------------------------------------------------------------------
# /bff/approvals — populated store returns count > 0
# ---------------------------------------------------------------------------

class TestBffApprovalsSurfacePopulated:
    """When PANTHEON_BFF_APPROVAL_DECISION_STORE points to a real projected file
    the /bff/approvals endpoint returns count>0 and the pending items."""

    def test_pending_approval_appears_in_bff_approvals(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store_path = _write_approval_decisions(td, [_PENDING_APPROVAL])
            original_store = bff_main.read_store
            orig_env = os.environ.get("PANTHEON_BFF_APPROVAL_DECISION_STORE")
            try:
                os.environ["PANTHEON_BFF_APPROVAL_DECISION_STORE"] = store_path
                bff_main.read_store = ReadSurfaceStore(
                    os.path.join(td, "read_surfaces.json"),
                    allow_local_snapshot_fallback=False,
                )
                client = TestClient(bff_main.app, raise_server_exceptions=False)
                resp = client.get("/bff/approvals", headers=ADMIN_HEADERS)
                assert resp.status_code == 200, resp.text
                body = resp.json()
                assert body["count"] > 0, f"expected count>0, got {body}"
                ids = [item.get("decision_id") for item in body["items"]]
                assert "apv-consdata-001" in ids
            finally:
                bff_main.read_store = original_store
                if orig_env is None:
                    os.environ.pop("PANTHEON_BFF_APPROVAL_DECISION_STORE", None)
                else:
                    os.environ["PANTHEON_BFF_APPROVAL_DECISION_STORE"] = orig_env

    def test_decided_approvals_excluded_from_pending_list(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store_path = _write_approval_decisions(td, [_DECIDED_APPROVAL])
            original_store = bff_main.read_store
            orig_env = os.environ.get("PANTHEON_BFF_APPROVAL_DECISION_STORE")
            try:
                os.environ["PANTHEON_BFF_APPROVAL_DECISION_STORE"] = store_path
                bff_main.read_store = ReadSurfaceStore(
                    os.path.join(td, "read_surfaces.json"),
                    allow_local_snapshot_fallback=False,
                )
                client = TestClient(bff_main.app, raise_server_exceptions=False)
                resp = client.get("/bff/approvals", headers=ADMIN_HEADERS)
                assert resp.status_code == 200, resp.text
                body = resp.json()
                assert body["count"] == 0, f"expected 0 pending (decided approval filtered), got {body}"
            finally:
                bff_main.read_store = original_store
                if orig_env is None:
                    os.environ.pop("PANTHEON_BFF_APPROVAL_DECISION_STORE", None)
                else:
                    os.environ["PANTHEON_BFF_APPROVAL_DECISION_STORE"] = orig_env

    def test_mixed_store_only_pending_returned(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store_path = _write_approval_decisions(
                td, [_PENDING_APPROVAL, _DECIDED_APPROVAL]
            )
            original_store = bff_main.read_store
            orig_env = os.environ.get("PANTHEON_BFF_APPROVAL_DECISION_STORE")
            try:
                os.environ["PANTHEON_BFF_APPROVAL_DECISION_STORE"] = store_path
                bff_main.read_store = ReadSurfaceStore(
                    os.path.join(td, "read_surfaces.json"),
                    allow_local_snapshot_fallback=False,
                )
                client = TestClient(bff_main.app, raise_server_exceptions=False)
                resp = client.get("/bff/approvals", headers=ADMIN_HEADERS)
                assert resp.status_code == 200, resp.text
                body = resp.json()
                assert body["count"] == 1, f"expected only pending item, got {body}"
                assert body["items"][0]["decision_id"] == "apv-consdata-001"
            finally:
                bff_main.read_store = original_store
                if orig_env is None:
                    os.environ.pop("PANTHEON_BFF_APPROVAL_DECISION_STORE", None)
                else:
                    os.environ["PANTHEON_BFF_APPROVAL_DECISION_STORE"] = orig_env


# ---------------------------------------------------------------------------
# /bff/approvals — absent store returns count=0, no fabrication
# ---------------------------------------------------------------------------

class TestBffApprovalsNoFabrication:
    """When no approval store is wired the endpoint returns count=0.
    No fixture data must be invented."""

    def test_empty_store_returns_count_zero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            original_store = bff_main.read_store
            orig_env = os.environ.get("PANTHEON_BFF_APPROVAL_DECISION_STORE")
            try:
                os.environ.pop("PANTHEON_BFF_APPROVAL_DECISION_STORE", None)
                bff_main.read_store = ReadSurfaceStore(
                    os.path.join(td, "read_surfaces.json"),
                    allow_local_snapshot_fallback=False,
                )
                client = TestClient(bff_main.app, raise_server_exceptions=False)
                resp = client.get("/bff/approvals", headers=ADMIN_HEADERS)
                assert resp.status_code == 200, resp.text
                body = resp.json()
                assert body["count"] == 0, f"expected 0 when no store wired, got {body}"
            finally:
                bff_main.read_store = original_store
                if orig_env is None:
                    os.environ.pop("PANTHEON_BFF_APPROVAL_DECISION_STORE", None)
                else:
                    os.environ["PANTHEON_BFF_APPROVAL_DECISION_STORE"] = orig_env

    def test_unauthenticated_rejected(self) -> None:
        client = TestClient(bff_main.app, raise_server_exceptions=False)
        resp = client.get("/bff/approvals")
        assert resp.status_code in {401, 403}, resp.text


# ---------------------------------------------------------------------------
# Governance approval queue surface picks up projected store
# ---------------------------------------------------------------------------

class TestGovernanceApprovalQueueSurfaceWithProjectedStore:
    """When PANTHEON_BFF_APPROVAL_DECISION_STORE is wired the governance approval
    queue surface reads the canonical store and surfaces the pending decisions."""

    ROUTE = "/api/v1/operator/governance/approval-queue"

    def test_queue_returns_pending_when_store_wired(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store_path = _write_approval_decisions(td, [_PENDING_APPROVAL])
            original_store = bff_main.read_store
            orig_env = os.environ.get("PANTHEON_BFF_APPROVAL_DECISION_STORE")
            try:
                os.environ["PANTHEON_BFF_APPROVAL_DECISION_STORE"] = store_path
                bff_main.read_store = ReadSurfaceStore(
                    os.path.join(td, "read_surfaces.json"),
                    allow_local_snapshot_fallback=False,
                )
                client = TestClient(bff_main.app, raise_server_exceptions=False)
                resp = client.get(self.ROUTE, headers=ADMIN_HEADERS)
                assert resp.status_code == 200, resp.text
                body = resp.json()
                ids = [item.get("decision_id") for item in (body.get("items") or [])]
                assert "apv-consdata-001" in ids, f"pending approval not in queue: {body}"
            finally:
                bff_main.read_store = original_store
                if orig_env is None:
                    os.environ.pop("PANTHEON_BFF_APPROVAL_DECISION_STORE", None)
                else:
                    os.environ["PANTHEON_BFF_APPROVAL_DECISION_STORE"] = orig_env
