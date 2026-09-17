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
from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import (
    bff_error,
    extract_identity_stub,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.ports import ReadSurfacePorts, create_in_memory_read_surface_ports

ADMIN_HEADERS = {"Authorization": "Bearer op-dev:admin:mfa"}
OPERATOR_HEADERS = {"Authorization": "Bearer op-dev:operator"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PENDING_APPROVAL: Dict[str, Any] = {
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

_DECIDED_APPROVAL: Dict[str, Any] = {
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

_PLAN_APPROVAL_UNDER_REVIEW: Dict[str, Any] = {
    "decision_id": "apv-filter-002",
    "target_type": "DeploymentPlan",
    "target_id": "plan-filter-002",
    "target_version": "v1",
    "decision": None,
    "decision_state": "under_review",
    "actor_role": None,
    "actor_id": "planner-01",
    "rationale": "High-risk deployment plan under review",
    "created_at": "2026-06-15T11:00:00Z",
    "decided_at": None,
    "conditions": [],
    "risk_level": "high",
    "evidence_refs": [],
    "superseded_by": None,
    "expires_at": None,
    "capital_pool_id": "pool-dev-01",
    "persona_id": None,
    "metadata": None,
}

_BINDING_APPROVAL_PENDING: Dict[str, Any] = {
    "decision_id": "apv-filter-003",
    "target_type": "PersonaBinding",
    "target_id": "binding-filter-003",
    "target_version": "v1",
    "decision": None,
    "decision_state": "pending",
    "actor_role": None,
    "actor_id": "persona-ops",
    "rationale": "Low-risk persona binding pending",
    "created_at": "2026-06-15T12:00:00Z",
    "decided_at": None,
    "conditions": [],
    "risk_level": "low",
    "evidence_refs": [],
    "superseded_by": None,
    "expires_at": None,
    "capital_pool_id": "pool-dev-02",
    "persona_id": "persona-01",
    "metadata": None,
}

_KILLSWITCH_APPROVAL_REVIEWED: Dict[str, Any] = {
    "decision_id": "apv-filter-004",
    "target_type": "KillSwitch",
    "target_id": "ks-filter-004",
    "target_version": "v1",
    "decision": None,
    "decision_state": "reviewed",
    "actor_role": None,
    "actor_id": "risk-lead",
    "rationale": "Critical kill-switch request reviewed",
    "created_at": "2026-06-15T13:00:00Z",
    "decided_at": None,
    "conditions": [],
    "risk_level": "critical",
    "evidence_refs": [],
    "superseded_by": None,
    "expires_at": None,
    "capital_pool_id": "pool-dev-01",
    "persona_id": None,
    "metadata": None,
}

_HETEROGENEOUS_APPROVAL_DECISIONS: List[Dict[str, Any]] = [
    _PENDING_APPROVAL,
    _PLAN_APPROVAL_UNDER_REVIEW,
    _BINDING_APPROVAL_PENDING,
    _KILLSWITCH_APPROVAL_REVIEWED,
    _DECIDED_APPROVAL,
]



def _client_for(store: ReadSurfacePorts) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_governance_router(
            read_surface=store,
            extract_identity=extract_identity_stub,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
        )
    )
    return TestClient(app, raise_server_exceptions=False)


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
        client = _client_for(store)
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
        client = _client_for(store)
        resp = client.get("/bff/approvals", headers=ADMIN_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] == 0, f"expected 0 pending (decided approval filtered), got {body}"

    def test_mixed_store_only_pending_returned(self) -> None:
        store = create_in_memory_read_surface_ports(
            ooda_management_kwargs={"approval_decisions": [_PENDING_APPROVAL, _DECIDED_APPROVAL]}
        )
        client = _client_for(store)
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
        client = _client_for(store)
        resp = client.get("/bff/approvals", headers=ADMIN_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] == 0, f"expected 0 when no store wired, got {body}"

    def test_unauthenticated_rejected(self) -> None:
        store = create_in_memory_read_surface_ports()
        client = _client_for(store)
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
        client = _client_for(store)
        resp = client.get(self.ROUTE, headers=ADMIN_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        ids = [item.get("decision_id") for item in (body.get("items") or [])]
        assert "apv-consdata-001" in ids, f"pending approval not in queue: {body}"

    def test_evidence_json_nine_filter_scenarios(self) -> None:
        """Verifies the 9 end-to-end filter scenarios recorded in evidence.json
        under the real router and authentic in-memory ports (no test-local lambdas)."""
        store_with_item = create_in_memory_read_surface_ports(
            ooda_management_kwargs={"approval_decisions": [_PENDING_APPROVAL]}
        )
        client_with_item = _client_for(store_with_item)

        store_empty = create_in_memory_read_surface_ports(
            ooda_management_kwargs={"approval_decisions": []}
        )
        client_empty = _client_for(store_empty)

        scenarios = [
            ("1. No filter", client_with_item, {}, ["apv-consdata-001"]),
            ("2. decision_state=proposed", client_with_item, {"decision_state": "proposed"}, ["apv-consdata-001"]),
            ("3. state=proposed (alias)", client_with_item, {"state": "proposed"}, ["apv-consdata-001"]),
            ("4. decision_state=decided", client_with_item, {"decision_state": "decided"}, []),
            ("5. risk_level=medium", client_with_item, {"risk_level": "medium"}, ["apv-consdata-001"]),
            ("6. risk_level=high (empty set)", client_with_item, {"risk_level": "high"}, []),
            ("7. decision_type=registry_entry", client_with_item, {"decision_type": "registry_entry"}, ["apv-consdata-001"]),
            ("8. decision_type=nonexistent (empty set)", client_with_item, {"decision_type": "nonexistent"}, []),
            ("9. empty store", client_empty, {}, []),
        ]

        for name, client, params, expected_ids in scenarios:
            resp = client.get(self.ROUTE, params=params, headers=ADMIN_HEADERS)
            assert resp.status_code == 200, f"Scenario '{name}' returned {resp.status_code}: {resp.text}"
            body = resp.json()
            ids = [item.get("decision_id") for item in (body.get("items") or [])]
            assert ids == expected_ids, f"Scenario '{name}' expected {expected_ids} but got {ids}"
            assert "meta" in body, f"Scenario '{name}' missing meta"
            assert "page_info" in body, f"Scenario '{name}' missing page_info"

    def test_filter_by_decision_state_and_csv(self) -> None:
        """Filter by decision_state accepts single values and comma-separated lists."""
        store = create_in_memory_read_surface_ports(
            ooda_management_kwargs={"approval_decisions": _HETEROGENEOUS_APPROVAL_DECISIONS}
        )
        client = _client_for(store)

        for state, expected in [
            ("proposed", ["apv-consdata-001"]),
            ("under_review", ["apv-filter-002"]),
            ("pending", ["apv-filter-003"]),
            ("reviewed", ["apv-filter-004"]),
            ("proposed,pending", ["apv-consdata-001", "apv-filter-003"]),
            ("under_review,reviewed", ["apv-filter-002", "apv-filter-004"]),
        ]:
            resp = client.get(self.ROUTE, params={"decision_state": state}, headers=ADMIN_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            ids = [item.get("decision_id") for item in (body.get("items") or [])]
            assert sorted(ids) == sorted(expected), f"Filtering decision_state={state} expected {expected} got {ids}"

    def test_filter_by_state_alias_backward_compatible(self) -> None:
        """Filter by legacy state alias resolves correctly across router, service, and port."""
        store = create_in_memory_read_surface_ports(
            ooda_management_kwargs={"approval_decisions": _HETEROGENEOUS_APPROVAL_DECISIONS}
        )
        client = _client_for(store)

        for state, expected in [
            ("proposed", ["apv-consdata-001"]),
            ("under_review", ["apv-filter-002"]),
            ("pending", ["apv-filter-003"]),
            ("reviewed", ["apv-filter-004"]),
            ("proposed,reviewed", ["apv-consdata-001", "apv-filter-004"]),
        ]:
            resp = client.get(self.ROUTE, params={"state": state}, headers=ADMIN_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            ids = [item.get("decision_id") for item in (body.get("items") or [])]
            assert sorted(ids) == sorted(expected), f"Filtering state={state} expected {expected} got {ids}"

    def test_decision_state_takes_precedence_over_state_alias(self) -> None:
        """When both decision_state and state query parameters are passed, decision_state wins."""
        store = create_in_memory_read_surface_ports(
            ooda_management_kwargs={"approval_decisions": _HETEROGENEOUS_APPROVAL_DECISIONS}
        )
        client = _client_for(store)
        resp = client.get(
            self.ROUTE,
            params={"decision_state": "proposed", "state": "under_review"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        ids = [item.get("decision_id") for item in (body.get("items") or [])]
        assert ids == ["apv-consdata-001"], f"expected decision_state to override state alias, got {ids}"

    def test_filter_by_risk_level_and_csv(self) -> None:
        """Filter by risk_level accepts single values and comma-separated lists."""
        store = create_in_memory_read_surface_ports(
            ooda_management_kwargs={"approval_decisions": _HETEROGENEOUS_APPROVAL_DECISIONS}
        )
        client = _client_for(store)

        for risk, expected in [
            ("medium", ["apv-consdata-001"]),
            ("high", ["apv-filter-002"]),
            ("low", ["apv-filter-003"]),
            ("critical", ["apv-filter-004"]),
            ("low,critical", ["apv-filter-003", "apv-filter-004"]),
            ("medium,high", ["apv-consdata-001", "apv-filter-002"]),
        ]:
            resp = client.get(self.ROUTE, params={"risk_level": risk}, headers=ADMIN_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            ids = [item.get("decision_id") for item in (body.get("items") or [])]
            assert sorted(ids) == sorted(expected), f"Filtering risk_level={risk} expected {expected} got {ids}"

    def test_filter_by_decision_type_and_csv(self) -> None:
        """Filter by decision_type accepts single values and comma-separated lists."""
        store = create_in_memory_read_surface_ports(
            ooda_management_kwargs={"approval_decisions": _HETEROGENEOUS_APPROVAL_DECISIONS}
        )
        client = _client_for(store)

        for dtype, expected in [
            ("registry_entry", ["apv-consdata-001"]),
            ("DeploymentPlan", ["apv-filter-002"]),
            ("PersonaBinding", ["apv-filter-003"]),
            ("KillSwitch", ["apv-filter-004"]),
            ("DeploymentPlan,PersonaBinding", ["apv-filter-002", "apv-filter-003"]),
            ("registry_entry,KillSwitch", ["apv-consdata-001", "apv-filter-004"]),
        ]:
            resp = client.get(self.ROUTE, params={"decision_type": dtype}, headers=ADMIN_HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            ids = [item.get("decision_id") for item in (body.get("items") or [])]
            assert sorted(ids) == sorted(expected), f"Filtering decision_type={dtype} expected {expected} got {ids}"

    def test_combined_multi_criteria_filters(self) -> None:
        """Cross-dimensional filters combine conjunctively (AND logic)."""
        store = create_in_memory_read_surface_ports(
            ooda_management_kwargs={"approval_decisions": _HETEROGENEOUS_APPROVAL_DECISIONS}
        )
        client = _client_for(store)

        # Match all three criteria
        resp = client.get(
            self.ROUTE,
            params={
                "decision_type": "DeploymentPlan",
                "risk_level": "high",
                "decision_state": "under_review",
            },
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        ids = [item.get("decision_id") for item in (body.get("items") or [])]
        assert ids == ["apv-filter-002"]

        # Mismatch on risk_level
        resp = client.get(
            self.ROUTE,
            params={
                "decision_type": "DeploymentPlan",
                "risk_level": "low",
                "decision_state": "under_review",
            },
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("items") == []

    def test_healthy_empty_set_structure(self) -> None:
        """A healthy empty set produces HTTP 200 with standard pagination and metadata,
        and is never shaped like an error or exception."""
        store = create_in_memory_read_surface_ports(
            ooda_management_kwargs={"approval_decisions": _HETEROGENEOUS_APPROVAL_DECISIONS}
        )
        client = _client_for(store)

        resp = client.get(
            self.ROUTE,
            params={"decision_type": "NoSuchType", "risk_level": "none"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200, f"expected 200 for empty set, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["items"] == []
        assert body["page_info"]["next_page_token"] is None
        assert body["page_info"]["page_size"] == 20
        surfaces = body["meta"]["surfaces"]
        assert surfaces["approval_queue"]["status"] == "ok"
        assert surfaces["allowedActions"]["status"] == "ok"
        assert surfaces["allowedActions"]["available"] is True

    def test_terminal_decisions_never_surfaced_in_queue(self) -> None:
        """Decided, approved, or rejected items are terminal and must never appear
        in the approval queue regardless of filter arguments."""
        store = create_in_memory_read_surface_ports(
            ooda_management_kwargs={"approval_decisions": _HETEROGENEOUS_APPROVAL_DECISIONS}
        )
        client = _client_for(store)

        # Unfiltered query
        resp = client.get(self.ROUTE, headers=ADMIN_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        ids = [item.get("decision_id") for item in (body.get("items") or [])]
        assert "apv-consdata-002" not in ids, f"decided approval leaked into queue: {ids}"

        # Query targeting decided item's risk_level
        resp = client.get(self.ROUTE, params={"risk_level": "low"}, headers=ADMIN_HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        ids = [item.get("decision_id") for item in (body.get("items") or [])]
        assert "apv-consdata-002" not in ids, f"decided approval leaked into filtered queue: {ids}"
        assert ids == ["apv-filter-003"]


# ---------------------------------------------------------------------------
# Store-precedence: explicit file wins over HTTP service client
# ---------------------------------------------------------------------------

class TestStorePrecedenceOverServiceClient:
    """The composite /bff/approvals route only ever reads the wired in-memory
    read-surface port, so a governance-service URL being configured in the
    environment must never shadow a populated in-memory port. This guards the
    reviewer-flagged bug: docker-compose sets
    PANTHEON_GOVERNANCE_APPROVAL_API_URL=http://governance:8082, which (in the
    legacy CanonicalSnapshotAdapter-backed store) could shadow a
    projection-populated file and return count=0.
    """

    def test_file_store_wins_when_governance_url_is_also_set(self) -> None:
        """Even when PANTHEON_GOVERNANCE_APPROVAL_API_URL is set, the typed
        in-memory port wired onto the app's read surface wins and
        /bff/approvals returns count>0 from it (the route never falls back to
        a service client keyed off that env var)."""
        orig_gov_env = os.environ.get("PANTHEON_GOVERNANCE_APPROVAL_API_URL")
        try:
            # Simulate docker-compose default which would otherwise shadow the store.
            os.environ["PANTHEON_GOVERNANCE_APPROVAL_API_URL"] = "http://governance-stub:9999"
            store = create_in_memory_read_surface_ports(
                ooda_management_kwargs={"approval_decisions": [_PENDING_APPROVAL]}
            )
            client = _client_for(store)
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
