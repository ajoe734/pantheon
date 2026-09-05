"""Contract tests for /bff/approvals population via promotion-service approvals.

Verifies the end-to-end read path established by CONSOLE-DATA-APPROVALS:
  - The typed OODA/management port wires the BFF canonical read path
  - GET /bff/approvals returns count>0 and the pending item when the store is populated
  - Decided approvals (approved/rejected) are excluded from the pending list
  - Empty / absent store returns count=0 (no fabricated data)
  - The governance approval queue surface also picks up the projected data
  - The in-memory port takes precedence over governance service environment configuration

Stub dispatch (dev safety): no live broker orders, no capital allocation.
"""
from __future__ import annotations

import os
import sys

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.ports import create_in_memory_read_surface_ports

ADMIN_HEADERS = {"Authorization": "Bearer op-dev:admin:mfa"}
OPERATOR_HEADERS = {"Authorization": "Bearer op-dev:operator"}


def _extract_identity(authorization: str | None) -> OperatorIdentity:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    raw = authorization[len("Bearer "):].strip()
    parts = raw.split(":")
    operator_id = parts[0] if parts else "op"
    roles = parts[1].split(",") if len(parts) > 1 else []
    claims = {"mfa": True} if len(parts) > 2 and "mfa" in parts[2] else {}
    return OperatorIdentity(operator_id=operator_id, roles=roles, claims=claims)


def _require_read_role(identity: OperatorIdentity) -> None:
    if not identity or not identity.roles:
        raise HTTPException(status_code=403, detail="Forbidden")


def _make_client(store: Any = None) -> TestClient:
    app = FastAPI(title="Approvals Surface Contract")
    router = create_governance_router(
        get_read_store=lambda: store,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_operator_role=_require_read_role,
    )
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


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
        store = create_in_memory_read_surface_ports(
            ooda_management_kwargs={"approval_decisions": [_PENDING_APPROVAL]}
        )
        client = _make_client(store)
        resp = client.get("/bff/approvals", headers=ADMIN_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] > 0, f"expected count>0, got {body}"
        ids = [item.get("decision_id") for item in body["items"]]
        assert "apv-consdata-001" in ids

    def test_decided_approvals_excluded_from_pending_list(self) -> None:
        store = create_in_memory_read_surface_ports(
            ooda_management_kwargs={"approval_decisions": [_DECIDED_APPROVAL]}
        )
        client = _make_client(store)
        resp = client.get("/bff/approvals", headers=ADMIN_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] == 0, f"expected 0 pending (decided approval filtered), got {body}"

    def test_mixed_store_only_pending_returned(self) -> None:
        store = create_in_memory_read_surface_ports(
            ooda_management_kwargs={"approval_decisions": [_PENDING_APPROVAL, _DECIDED_APPROVAL]}
        )
        client = _make_client(store)
        resp = client.get("/bff/approvals", headers=ADMIN_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] == 1, f"expected only pending item, got {body}"
        assert body["items"][0]["decision_id"] == "apv-consdata-001"


# ---------------------------------------------------------------------------
# /bff/approvals — absent store returns count=0, no fabrication
# ---------------------------------------------------------------------------

class TestBffApprovalsNoFabrication:
    """When no approval store is wired the endpoint returns count=0.
    No fixture data must be invented."""

    def test_empty_store_returns_count_zero(self) -> None:
        store = create_in_memory_read_surface_ports()
        client = _make_client(store)
        resp = client.get("/bff/approvals", headers=ADMIN_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] == 0, f"expected 0 when no store wired, got {body}"

    def test_unauthenticated_rejected(self) -> None:
        client = _make_client()
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
        store = create_in_memory_read_surface_ports(
            ooda_management_kwargs={"approval_decisions": [_PENDING_APPROVAL]}
        )
        client = _make_client(store)
        resp = client.get(self.ROUTE, headers=ADMIN_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        ids = [item.get("decision_id") for item in (body.get("items") or [])]
        assert "apv-consdata-001" in ids, f"pending approval not in queue: {body}"


# ---------------------------------------------------------------------------
# Store-precedence: explicit file wins over HTTP service client
# ---------------------------------------------------------------------------

class TestStorePrecedenceOverServiceClient:
    """The composite /bff/approvals route reads the provided store,
    so a governance-service URL being configured in the environment must never
    shadow a populated in-memory port."""

    def test_file_store_wins_when_governance_url_is_also_set(self) -> None:
        orig_gov_env = os.environ.get("PANTHEON_GOVERNANCE_APPROVAL_API_URL")
        try:
            os.environ["PANTHEON_GOVERNANCE_APPROVAL_API_URL"] = "http://governance-stub:9999"
            store = create_in_memory_read_surface_ports(
                ooda_management_kwargs={"approval_decisions": [_PENDING_APPROVAL]}
            )
            client = _make_client(store)
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
            if orig_gov_env is None:
                os.environ.pop("PANTHEON_GOVERNANCE_APPROVAL_API_URL", None)
            else:
                os.environ["PANTHEON_GOVERNANCE_APPROVAL_API_URL"] = orig_gov_env
