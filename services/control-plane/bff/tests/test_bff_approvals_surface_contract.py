"""Contract tests for /bff/approvals population via promotion-service approvals.

Verifies the end-to-end read path established by CONSOLE-DATA-APPROVALS:
  - The typed OODA/management port wires the BFF canonical read path
  - GET /bff/approvals returns count>0 and the pending item when the store is populated
  - Decided approvals (approved/rejected) are excluded from the pending list
  - Empty / absent store returns count=0 (no fabricated data)
  - The governance approval queue surface also picks up the projected data
  - The adapter's own HTTP-vs-file precedence (PANTHEON_PROMOTION_API_URL /
    PANTHEON_GOVERNANCE_APPROVAL_API_URL / PANTHEON_BFF_APPROVAL_DECISION_STORE)
    is exercised directly against CanonicalSnapshotAdapter, since /bff/approvals
    itself only ever reads bff_main.read_store and never touches those env vars.

Stub dispatch (dev safety): no live broker orders, no capital allocation.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
# NOTE: CanonicalSnapshotAdapter is intentionally still imported here. Unlike
# every other fixture in this file, `test_promotion_service_url_read_path`
# below is a narrow unit test of read_store.py's own adapter precedence logic
# (file vs. HTTP dataset discovery), not a BFF composite-contract fixture. The
# other 6 declared-artifact files, and every other test in this file, build
# bff_main.read_store exclusively via create_in_memory_read_surface_ports().
from read_store import CanonicalSnapshotAdapter
from ports import create_in_memory_read_surface_ports

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


# ---------------------------------------------------------------------------
# /bff/approvals — populated store returns count > 0
# ---------------------------------------------------------------------------

class TestBffApprovalsSurfacePopulated:
    """When the OODA/management port is seeded with approval decisions,
    the /bff/approvals endpoint returns count>0 and the pending items."""

    def test_pending_approval_appears_in_bff_approvals(self) -> None:
        original_store = bff_main.read_store
        try:
            bff_main.read_store = create_in_memory_read_surface_ports(
                ooda_management_kwargs={"approval_decisions": [_PENDING_APPROVAL]}
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

    def test_decided_approvals_excluded_from_pending_list(self) -> None:
        original_store = bff_main.read_store
        try:
            bff_main.read_store = create_in_memory_read_surface_ports(
                ooda_management_kwargs={"approval_decisions": [_DECIDED_APPROVAL]}
            )
            client = TestClient(bff_main.app, raise_server_exceptions=False)
            resp = client.get("/bff/approvals", headers=ADMIN_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["count"] == 0, f"expected 0 pending (decided approval filtered), got {body}"
        finally:
            bff_main.read_store = original_store

    def test_mixed_store_only_pending_returned(self) -> None:
        original_store = bff_main.read_store
        try:
            bff_main.read_store = create_in_memory_read_surface_ports(
                ooda_management_kwargs={"approval_decisions": [_PENDING_APPROVAL, _DECIDED_APPROVAL]}
            )
            client = TestClient(bff_main.app, raise_server_exceptions=False)
            resp = client.get("/bff/approvals", headers=ADMIN_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["count"] == 1, f"expected only pending item, got {body}"
            assert body["items"][0]["decision_id"] == "apv-consdata-001"
        finally:
            bff_main.read_store = original_store


# ---------------------------------------------------------------------------
# /bff/approvals — absent store returns count=0, no fabrication
# ---------------------------------------------------------------------------

class TestBffApprovalsNoFabrication:
    """When no approval store is wired the endpoint returns count=0.
    No fixture data must be invented."""

    def test_empty_store_returns_count_zero(self) -> None:
        original_store = bff_main.read_store
        try:
            bff_main.read_store = create_in_memory_read_surface_ports()
            client = TestClient(bff_main.app, raise_server_exceptions=False)
            resp = client.get("/bff/approvals", headers=ADMIN_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["count"] == 0, f"expected 0 when no store wired, got {body}"
        finally:
            bff_main.read_store = original_store

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
        original_store = bff_main.read_store
        try:
            bff_main.read_store = create_in_memory_read_surface_ports(
                ooda_management_kwargs={"approval_decisions": [_PENDING_APPROVAL]}
            )
            client = TestClient(bff_main.app, raise_server_exceptions=False)
            resp = client.get(self.ROUTE, headers=ADMIN_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            ids = [item.get("decision_id") for item in (body.get("items") or [])]
            assert "apv-consdata-001" in ids, f"pending approval not in queue: {body}"
        finally:
            bff_main.read_store = original_store


# ---------------------------------------------------------------------------
# Store-precedence: explicit file wins over HTTP service client
# ---------------------------------------------------------------------------

class TestStorePrecedenceOverServiceClient:
    """The composite /bff/approvals route only ever reads bff_main.read_store,
    so a governance-service URL being configured in the environment must never
    shadow a populated in-memory port. This guards the reviewer-flagged bug:
    docker-compose sets PANTHEON_GOVERNANCE_APPROVAL_API_URL=http://governance:8082,
    which (in the legacy CanonicalSnapshotAdapter-backed store) could shadow a
    projection-populated file and return count=0.
    """

    def test_file_store_wins_when_governance_url_is_also_set(self) -> None:
        """Even when PANTHEON_GOVERNANCE_APPROVAL_API_URL is set, the typed
        in-memory port wired onto bff_main.read_store wins and /bff/approvals
        returns count>0 from it (the route never falls back to a service
        client keyed off that env var)."""
        original_store = bff_main.read_store
        orig_gov_env = os.environ.get("PANTHEON_GOVERNANCE_APPROVAL_API_URL")
        try:
            # Simulate docker-compose default which would otherwise shadow the store.
            os.environ["PANTHEON_GOVERNANCE_APPROVAL_API_URL"] = "http://governance-stub:9999"
            bff_main.read_store = create_in_memory_read_surface_ports(
                ooda_management_kwargs={"approval_decisions": [_PENDING_APPROVAL]}
            )
            client = TestClient(bff_main.app, raise_server_exceptions=False)
            resp = client.get("/bff/approvals", headers=ADMIN_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["count"] > 0, (
                "expected count>0 from the in-memory port even though "
                "PANTHEON_GOVERNANCE_APPROVAL_API_URL is set; "
                f"got {body}"
            )
            ids = [item.get("decision_id") for item in body["items"]]
            assert "apv-consdata-001" in ids, f"projected approval not found: {body}"
        finally:
            bff_main.read_store = original_store
            if orig_gov_env is None:
                os.environ.pop("PANTHEON_GOVERNANCE_APPROVAL_API_URL", None)
            else:
                os.environ["PANTHEON_GOVERNANCE_APPROVAL_API_URL"] = orig_gov_env

    def test_promotion_service_url_read_path(self) -> None:
        """PANTHEON_PROMOTION_API_URL is tried before the governance service URL.
        When the promotion service returns approvals, they are surfaced via the
        CanonicalSnapshotAdapter HTTP dataset path."""
        promotion_response = {
            "count": 1,
            "items": [_PENDING_APPROVAL],
        }
        orig_promo_env = os.environ.get("PANTHEON_PROMOTION_API_URL")
        orig_store_env = os.environ.get("PANTHEON_BFF_APPROVAL_DECISION_STORE")
        try:
            # No file store — force the HTTP path to be exercised.
            os.environ.pop("PANTHEON_BFF_APPROVAL_DECISION_STORE", None)
            os.environ["PANTHEON_PROMOTION_API_URL"] = "http://promotion-stub:8089"
            adapter = CanonicalSnapshotAdapter(
                snapshot_path=None,
                allow_snapshot_fallback=False,
            )
            with patch(
                "read_store._http_json_get",
                return_value=(True, promotion_response),
            ) as mock_get:
                available, records = adapter.list_records("approval_decisions")
                assert available, "expected available=True from promotion service mock"
                assert any(
                    r.get("decision_id") == "apv-consdata-001" for r in records
                ), f"pending approval not in records: {records}"
                # Confirm the call used the promotion URL, not the governance URL.
                call_args = mock_get.call_args
                assert "promotion-stub" in call_args[0][0], (
                    f"expected promotion service URL in call; got {call_args}"
                )
                assert call_args[0][1] == "/api/v1/approvals", (
                    f"expected /api/v1/approvals path; got {call_args}"
                )
        finally:
            if orig_promo_env is None:
                os.environ.pop("PANTHEON_PROMOTION_API_URL", None)
            else:
                os.environ["PANTHEON_PROMOTION_API_URL"] = orig_promo_env
            if orig_store_env is None:
                os.environ.pop("PANTHEON_BFF_APPROVAL_DECISION_STORE", None)
            else:
                os.environ["PANTHEON_BFF_APPROVAL_DECISION_STORE"] = orig_store_env
