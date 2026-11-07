"""BFF-HUMAN-INBOX-GOVERNANCE-READ-20261106: bound governance review contributor reads.

Verifies that:
1. The three governance review sources (approval_decisions, deployment_plans,
   evolution_decisions) are prewarmed concurrently on the existing owner-read
   executor and deduplicated per-request/tenant/caller via request_read_scope,
   so three 0.45s sources (1.35s serial > 1.0s surface budget) with the hosted
   540 decided + 3 revoked approval shape complete within the 1.0s surface
   budget across repeated reloads with one call per source per reload.
2. Linked terminal approval decisions remain available for DeploymentPlan
   eligibility projection (canApprove/canReject/canPromoteToPaper) without
   polluting pending queues, and all governance/deployment/Evolution eligibility
   rules and counts are preserved.
3. Caller authorization and selected_tenant contextvars are preserved across
   concurrent owner reader threads and isolated across concurrent tenant
   requests.
4. Genuine slow sources degrade governance_review_queue with read_timeout under
   the contributor surface deadline (not the outer 2.5s route margin), and
   source errors (including unavailable default DeploymentPlanPort) degrade with
   contributor_read_error rather than returning a healthy empty inbox.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import threading
import time
from typing import Any, Dict, List, Tuple
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.core.owner_reads import (
    OwnerReadContextMiddleware,
    authorization,
    remaining_management_read_budget,
    selected_tenant,
)
from services.control_plane.bff.governance.service import human_inbox_surface_timeout_seconds
from services.control_plane.bff.management_read_models import create_management_router
from services.control_plane.bff.management_read_models import router as inbox_router
from services.control_plane.bff.management_read_models.service import ManagementService
from services.control_plane.bff.personas.routes.common import run_management_read
from services.control_plane.bff.ports import create_read_surface_ports
from services.control_plane.bff.ports.lifecycle_telemetry_governance import (
    DomainIncidentPort,
    create_lifecycle_telemetry_governance_port,
)
from services.control_plane.bff.ports.ooda_management import (
    ManagementReviewQueuePort,
    OodaManagementDomainPort,
)
from services.control_plane.bff.ports.persona_capital_runtime import (
    CapitalPoolPort,
    DeploymentPlanPort,
    EvolutionProjectionPort,
    PersonaCapitalRuntimeDomainPort,
    PersonaFleetPort,
    RankingProjectionPort,
    RuntimePort,
)
from services.control_plane.bff.tests.test_human_inbox_truth import HEADERS, IncidentOwner


def _hosted_terminal_approvals(decided_count: int = 540, revoked_count: int = 3) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for idx in range(decided_count):
        rows.append(
            {
                "decision_id": f"apv-decided-{idx:04d}",
                "decision_state": "decided",
                "state": "decided",
                "outcome": "approved" if idx % 2 == 0 else "rejected",
                "target_type": "deployment_plan",
                "target_id": f"plan-hist-{idx:04d}",
                "risk_level": "medium",
                "created_at": "2026-10-15T08:00:00Z",
                "decided_at": "2026-10-15T09:00:00Z",
                "actor_id": "persona-agent",
                "reviewer": "gov-reviewer",
            }
        )
    for idx in range(revoked_count):
        rows.append(
            {
                "decision_id": f"apv-revoked-{idx:04d}",
                "decision_state": "revoked",
                "state": "revoked",
                "outcome": "rejected",
                "target_type": "registry_entry",
                "target_id": f"reg-revoked-{idx:04d}",
                "risk_level": "high",
                "created_at": "2026-10-16T08:00:00Z",
                "actor_id": "persona-agent",
            }
        )
    return rows


def _build_inbox_client(
    owner: IncidentOwner,
    *,
    approvals: Any,
    plans: Any,
    evolution: Any,
    personas: Any = lambda **_k: [],
) -> Tuple[TestClient, Any]:
    store = create_read_surface_ports(
        lifecycle_telemetry_governance=create_lifecycle_telemetry_governance_port(
            incident_port=DomainIncidentPort(incidents_api_url=owner.url),
        ),
        ooda_management=OodaManagementDomainPort(
            review_queue_port=ManagementReviewQueuePort(
                deployment_plans_reader=plans,
                evolution_decisions_reader=evolution,
                approval_decisions_reader=approvals,
            )
        ),
    )
    store.list_personas = personas  # type: ignore[method-assign]
    app = FastAPI()
    app.add_middleware(OwnerReadContextMiddleware)
    app.include_router(
        create_management_router(
            get_read_store=lambda: store,
            service=ManagementService(get_read_store=lambda: store),
            run_management_read=run_management_read,
        )
    )
    return TestClient(app), store


def test_three_delayed_governance_sources_complete_within_one_second_budget_and_dedupe_approvals() -> None:
    """Reproduces the 3-source governance review queue read shape (540 decided +
    3 revoked approval decisions, deployment plans, and evolution proposals)
    where each source takes 0.42s (1.26s serial > 1.0s surface budget).
    Concurrent prewarming + request-scoped memoization completes under the 1.0s
    surface budget across 5 consecutive reloads with exactly 1 read per source
    per reload, while keeping original budgets unchanged.
    """
    assert human_inbox_surface_timeout_seconds() == 1.0
    assert inbox_router._get_human_inbox_read_timeout() == 2.5

    hosted_approvals = _hosted_terminal_approvals(540, 3)
    counts = {"approvals": 0, "plans": 0, "evolution": 0}
    lock = threading.Lock()
    observed_budgets: List[float] = []

    def read_approvals() -> List[Dict[str, Any]]:
        with lock:
            counts["approvals"] += 1
            rem = remaining_management_read_budget()
            if rem is not None:
                observed_budgets.append(rem)
        time.sleep(0.42)
        return hosted_approvals

    def read_plans() -> List[Dict[str, Any]]:
        with lock:
            counts["plans"] += 1
        time.sleep(0.42)
        return []

    def read_evolution() -> List[Dict[str, Any]]:
        with lock:
            counts["evolution"] += 1
        time.sleep(0.42)
        return [
            {
                "decision_id": f"evo-prop-{i}",
                "status": "proposed",
                "risk_level": "low",
                "created_at": "2026-10-21T00:00:00Z",
            }
            for i in range(40)
        ]

    with IncidentOwner([], delay=0.03) as owner:
        client, _store = _build_inbox_client(
            owner,
            approvals=read_approvals,
            plans=read_plans,
            evolution=read_evolution,
        )
        for reload_idx in range(1, 6):
            started = time.monotonic()
            resp = client.get("/bff/management/human-inbox", headers=HEADERS)
            elapsed = time.monotonic() - started
            assert resp.status_code == 200, resp.text
            body = resp.json()
            surfaces = body["meta"]["surfaces"]
            assert surfaces["governance_review_queue"]["status"] == "ok", (reload_idx, surfaces)
            assert surfaces["approval_queue"]["status"] == "ok", (reload_idx, surfaces)
            assert surfaces["incidents"]["status"] == "ok", (reload_idx, surfaces)
            assert surfaces["persona_readiness"]["status"] == "ok", (reload_idx, surfaces)
            assert surfaces["human_inbox"]["status"] == "ok", (reload_idx, surfaces)
            assert not body["meta"].get("partial"), (reload_idx, body["meta"])
            assert body["page_info"]["total"] == 0
            assert body["data"]["items"] == []
            assert elapsed < 0.95, f"reload {reload_idx} took {elapsed:.3f}s; expected concurrent ~0.42s read"
            assert counts == {
                "approvals": reload_idx,
                "plans": reload_idx,
                "evolution": reload_idx,
            }

    # Contributor/warmer deadline must be bounded by the 1.0s surface budget, not the 2.5s outer route budget.
    assert observed_budgets and all(0.0 < b <= 1.05 for b in observed_budgets), observed_budgets


def test_governance_eligibility_rules_and_linked_terminal_approvals_preserved() -> None:
    """Linked terminal approval decisions must still govern DeploymentPlan
    allowedActions (canApprove/canReject False when already approved, while
    canPromoteToPaper is True for paper stage), and pending unlinked approvals,
    reviewable deployment plans, and decidable evolution decisions keep exact
    eligibility and counts.
    """
    approvals = _hosted_terminal_approvals(10, 1) + [
        {
            "decision_id": "apv-pending-unlinked",
            "decision_state": "under_review",
            "state": "under_review",
            "target_type": "strategy_spec",
            "target_id": "strat-1",
            "risk_level": "high",
            "created_at": "2026-11-05T10:00:00Z",
            "actor_id": "op-1",
            "rationale": "Requires committee sign-off",
        }
    ]
    plans = [
        {
            "plan_id": "plan-linked-approved",
            "status": "pending_review",
            "target_stage": "paper",
            "approval_decision_id": "apv-decided-0000",  # outcome == "approved"
            "submitted_at": "2026-11-05T11:00:00Z",
            "created_by": "planner",
            "risk_summary": "Paper rollout plan",
        },
        {
            "plan_id": "plan-active-ignored",
            "status": "active",
            "target_stage": "paper",
            "submitted_at": "2026-11-05T11:00:00Z",
        },
    ]
    evolution = [
        {
            "decision_id": "evo-under-review",
            "status": "under_review",
            "risk_level": "medium",
            "created_at": "2026-11-05T12:00:00Z",
            "rationale": "Promote candidate weights",
        },
        {
            "decision_id": "evo-proposed-sweep",
            "status": "proposed",
            "risk_level": "low",
            "created_at": "2026-11-05T12:00:00Z",
            "rationale": "flag_for_review",
        },
    ]

    with IncidentOwner([]) as owner:
        client, store = _build_inbox_client(
            owner,
            approvals=lambda: approvals,
            plans=lambda: plans,
            evolution=lambda: evolution,
        )
        queue_items = store.list_governance_review_queue_items()
        plan_item = next(i for i in queue_items if i["item_id"] == "review-plan-linked-approved")
        assert plan_item["allowedActions"] == {
            "canApprove": False,
            "canReject": False,
            "canPromoteToPaper": True,
        }
        assert plan_item["governance_outcome"] == "approved"

        resp = client.get("/bff/management/human-inbox", headers=HEADERS, params={"page_size": 50})
        assert resp.status_code == 200
        body = resp.json()
        items = body["data"]["items"]
        item_ids = {i["item_id"] for i in items}
        assert "apv-pending-unlinked" in item_ids
        assert "review-plan-linked-approved" in item_ids
        assert "review-evo-under-review" in item_ids
        assert "review-evo-proposed-sweep" not in item_ids
        assert "review-plan-active-ignored" not in item_ids


def test_concurrent_requests_isolate_caller_authorization_and_tenant_context() -> None:
    """Concurrent Human Inbox requests for distinct tenants/callers preserve
    their own authorization and selected_tenant inside all three prewarmed
    governance review readers and never cross-contaminate results.
    """
    seen_contexts: Dict[str, List[Tuple[str, str]]] = {
        "approvals": [],
        "plans": [],
        "evolution": [],
    }
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def _record(kind: str) -> Tuple[str, str]:
        auth = str(authorization.get() or "")
        tenant = str(selected_tenant.get() or "")
        with lock:
            seen_contexts[kind].append((auth, tenant))
        return auth, tenant

    def read_approvals() -> List[Dict[str, Any]]:
        _auth, tenant = _record("approvals")
        time.sleep(0.05)
        return [
            {
                "decision_id": f"apv-{tenant}",
                "decision_state": "proposed",
                "target_type": "registry_entry",
                "target_id": f"target-{tenant}",
                "risk_level": "medium",
                "created_at": "2026-11-05T08:00:00Z",
                "rationale": f"approval for {tenant}",
            }
        ]

    def read_plans() -> List[Dict[str, Any]]:
        _auth, tenant = _record("plans")
        time.sleep(0.05)
        return [
            {
                "plan_id": f"plan-{tenant}",
                "status": "pending_review",
                "submitted_at": "2026-11-05T09:00:00Z",
                "risk_summary": f"plan for {tenant}",
            }
        ]

    def read_evolution() -> List[Dict[str, Any]]:
        _auth, tenant = _record("evolution")
        time.sleep(0.05)
        return [
            {
                "decision_id": f"evo-{tenant}",
                "status": "reviewed",
                "risk_level": "low",
                "created_at": "2026-11-05T10:00:00Z",
                "rationale": f"evo for {tenant}",
            }
        ]

    with IncidentOwner([]) as owner:
        client, _store = _build_inbox_client(
            owner,
            approvals=read_approvals,
            plans=read_plans,
            evolution=read_evolution,
        )

        def _call_for_tenant(op_id: str, tenant_id: str) -> Dict[str, Any]:
            barrier.wait(timeout=2.0)
            headers = {
                "Authorization": f"Bearer {op_id}:operator,admin:mfa",
                "X-Tenant-Id": tenant_id,
            }
            resp = client.get("/bff/management/human-inbox", headers=headers, params={"page_size": 50})
            assert resp.status_code == 200, resp.text
            return resp.json()

        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_a = pool.submit(_call_for_tenant, "op-alpha", "tenant-alpha")
            fut_b = pool.submit(_call_for_tenant, "op-beta", "tenant-beta")
            body_a = fut_a.result(timeout=5.0)
            body_b = fut_b.result(timeout=5.0)

    ids_a = {i["item_id"] for i in body_a["data"]["items"]}
    ids_b = {i["item_id"] for i in body_b["data"]["items"]}
    assert ids_a == {"apv-tenant-alpha", "review-plan-tenant-alpha", "review-evo-tenant-alpha"}
    assert ids_b == {"apv-tenant-beta", "review-plan-tenant-beta", "review-evo-tenant-beta"}

    expected_pairs = {
        ("Bearer op-alpha:operator,admin:mfa", "tenant-alpha"),
        ("Bearer op-beta:operator,admin:mfa", "tenant-beta"),
    }
    for kind, pairs in seen_contexts.items():
        assert set(pairs) == expected_pairs, (kind, pairs)
        assert len(pairs) == 2, (kind, pairs)


def test_slow_governance_source_degrades_governance_review_queue_with_read_timeout_alone() -> None:
    """When deployment_plans or evolution_decisions exceeds the surface timeout
    budget, governance_review_queue degrades with read_timeout while
    approval_queue, incidents, and persona_readiness remain ok.
    """
    def slow_evolution() -> List[Dict[str, Any]]:
        time.sleep(0.5)
        return [{"decision_id": "evo-late", "status": "reviewed"}]

    with patch.dict(os.environ, {"PANTHEON_BFF_HUMAN_INBOX_SURFACE_TIMEOUT_SECONDS": "0.1"}):
        with IncidentOwner([]) as owner:
            client, _store = _build_inbox_client(
                owner,
                approvals=lambda: _hosted_terminal_approvals(20, 1),
                plans=lambda: [],
                evolution=slow_evolution,
            )
            started = time.monotonic()
            resp = client.get("/bff/management/human-inbox", headers=HEADERS)
            elapsed = time.monotonic() - started

    assert resp.status_code == 200, resp.text
    assert elapsed < 0.45, f"expected degradation near 0.1s surface budget, took {elapsed:.3f}s"
    body = resp.json()
    assert body["meta"]["partial"] is True
    surfaces = body["meta"]["surfaces"]
    assert surfaces["governance_review_queue"]["status"] == "degraded"
    assert surfaces["governance_review_queue"]["source"] == "management_read_timeout"
    assert surfaces["governance_review_queue"]["reason"] == "read_timeout"
    assert surfaces["approval_queue"]["status"] == "ok"
    assert surfaces["incidents"]["status"] == "ok"
    assert surfaces["persona_readiness"]["status"] == "ok"
    assert surfaces["human_inbox"]["status"] == "degraded"


def test_default_deployment_plan_port_failure_never_returns_healthy_empty_governance_queue() -> None:
    """When DeploymentPlanPort's provider fails (returning source='unavailable'
    and [] from list_deployment_plans), Human Inbox must report
    governance_review_queue as degraded rather than ok/empty.
    """
    def failing_plans_provider() -> List[Dict[str, Any]]:
        raise ConnectionRefusedError("deployment owner connection refused")

    pcr = PersonaCapitalRuntimeDomainPort(
        persona_port=PersonaFleetPort(records_provider=lambda: []),
        capital_port=CapitalPoolPort(pools_provider=lambda: [], bindings_provider=lambda: []),
        deployment_port=DeploymentPlanPort(plans_provider=failing_plans_provider),
        runtime_port=RuntimePort(runtime_bindings_provider=lambda: []),
        ranking_port=RankingProjectionPort(),
        evolution_port=EvolutionProjectionPort(evolution_decisions_reader=lambda: []),
    )
    with IncidentOwner([]) as owner:
        store = create_read_surface_ports(
            lifecycle_telemetry_governance=create_lifecycle_telemetry_governance_port(
                incident_port=DomainIncidentPort(incidents_api_url=owner.url),
            ),
            persona_capital_runtime=pcr,
            ooda_management=OodaManagementDomainPort(
                review_queue_port=ManagementReviewQueuePort(
                    deployment_plans_reader=pcr.list_deployment_plans,
                    evolution_decisions_reader=pcr.list_evolution_decisions,
                    approval_decisions_reader=lambda: [],
                )
            ),
        )
        store.list_personas = lambda **_k: []  # type: ignore[method-assign]
        app = FastAPI()
        app.add_middleware(OwnerReadContextMiddleware)
        app.include_router(
            create_management_router(
                get_read_store=lambda: store,
                service=ManagementService(get_read_store=lambda: store),
                run_management_read=run_management_read,
            )
        )
        client = TestClient(app)
        resp = client.get("/bff/management/human-inbox", headers=HEADERS)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["meta"]["partial"] is True
    gov_surface = body["meta"]["surfaces"]["governance_review_queue"]
    assert gov_surface["status"] == "degraded"
    assert gov_surface["reason"] == "contributor_read_error"
    assert body["meta"]["surfaces"]["human_inbox"]["status"] == "degraded"


def test_real_http_owner_readers_propagate_deadline_dedupe_sources_and_preserve_nonmanagement_defaults() -> None:
    """Exercises default create_read_surface_ports() HTTP owner readers end-to-end:
    - approval_records -> approval_owner.list_decisions -> approval_owner.call_owner
    - DeploymentPlanPort -> read_records -> base.http_request_json -> command_executor._get_json
    - EvolutionProjectionPort -> read_evolution_records -> base.evolution_owner_request
    Verifies:
    1. Concurrent delayed HTTP GETs finish within 1.0s surface budget with 1 HTTP GET
       per source per reload (approval_decisions shared between approval_queue and
       governance_review_queue) and preserve caller Authorization + X-Tenant-Id.
    2. Real HTTP transport timeouts in any of the 3 owners bound urllib.request.urlopen
       to remaining_management_read_budget (not 15s/30s defaults) and degrade with
       read_timeout.
    3. Real HTTP 503 from default DeploymentPlanPort degrades governance_review_queue
       with contributor_read_error rather than returning a healthy empty queue.
    4. Default non-management / write calls without management_read_deadline keep
       their original 15s / 30s timeouts unchanged.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import json
    import urllib.request
    import pytest

    from services.control_plane.bff import command_executor
    from services.control_plane.bff.command_adapters import base as adapter_base
    from services.control_plane.bff.core.owner_reads import management_read_deadline
    from services.control_plane.bff.governance import approval_owner

    state: Dict[str, Any] = {
        "delays": {
            "/api/governance/approvals": 0.35,
            "/api/governance/approvals?decision_id=apv-decided-0000": 0.05,
            "/api/deployment/plans": 0.35,
            "/api/evolution/proposals": 0.35,
        },
        "status_codes": {},
        "counts": {"/api/governance/approvals": 0, "/api/deployment/plans": 0, "/api/evolution/proposals": 0, "/api/incidents": 0},
        "full_counts": {},
        "headers": [],
    }
    state_lock = threading.Lock()
    hosted_approvals = _hosted_terminal_approvals(540, 3)

    class _OwnerHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)
            with state_lock:
                state["counts"][path] = state["counts"].get(path, 0) + 1
                state["full_counts"][self.path] = state["full_counts"].get(self.path, 0) + 1
                state["headers"].append(
                    (
                        path,
                        self.headers.get("Authorization"),
                        self.headers.get("X-Tenant-Id"),
                    )
                )
                delay = float(state["delays"].get(self.path, state["delays"].get(path, 0.0)))
                code = int(state["status_codes"].get(path, 200))
            if delay > 0:
                time.sleep(delay)
            if code != 200:
                payload = json.dumps({"error": "owner failure"}).encode("utf-8")
            elif path == "/api/governance/approvals":
                rows = hosted_approvals
                if "decision_state" in qs:
                    wanted_states = {
                        part.strip().lower()
                        for raw in qs["decision_state"]
                        for part in raw.split(",")
                        if part.strip()
                    }
                    rows = [
                        r for r in rows
                        if str(r.get("decision_state") or r.get("state") or "").lower() in wanted_states
                    ]
                if "decision_id" in qs:
                    wanted_ids = {
                        part.strip()
                        for raw in qs["decision_id"]
                        for part in raw.split(",")
                        if part.strip()
                    }
                    rows = [
                        r for r in rows
                        if str(r.get("decision_id") or r.get("id") or "") in wanted_ids
                    ]
                payload = json.dumps(rows).encode("utf-8")
            elif path == "/api/deployment/plans":
                payload = json.dumps(
                    [
                        {
                            "plan_id": "plan-http-1",
                            "status": "pending_review",
                            "target_stage": "paper",
                            "approval_decision_id": "apv-decided-0000",
                            "submitted_at": "2026-11-05T11:00:00Z",
                            "risk_summary": "HTTP plan",
                        }
                    ]
                ).encode("utf-8")
            elif path == "/api/evolution/proposals":
                evo_rows = [
                    {
                        "decision_id": "evo-http-1",
                        "status": "under_review",
                        "decision_state": "under_review",
                        "risk_level": "medium",
                        "created_at": "2026-11-05T12:00:00Z",
                        "rationale": "HTTP evolution decision",
                    }
                ]
                if "decision_state" in qs:
                    wanted_evo = {
                        part.strip().lower()
                        for raw in qs["decision_state"]
                        for part in raw.split(",")
                        if part.strip()
                    }
                    evo_rows = [
                        r for r in evo_rows
                        if str(r.get("decision_state") or r.get("status") or "").lower() in wanted_evo
                    ]
                payload = json.dumps(evo_rows).encode("utf-8")
            else:
                payload = b"[]"
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except BrokenPipeError:
                pass

        def log_message(self, *_args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _OwnerHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    try:
        env = {
            "PANTHEON_GOVERNANCE_APPROVAL_API_URL": base_url,
            "PANTHEON_DEPLOYMENT_API_URL": base_url,
            "PANTHEON_EVOLUTION_API_URL": base_url,
            "PANTHEON_INCIDENTS_API_URL": base_url,
            "EVOLUTION_AUTH_TOKEN": "evo-service-secret",
        }
        with patch.dict(os.environ, env):
            store = create_read_surface_ports()
            store.list_personas = lambda **_k: []  # type: ignore[method-assign]
            app = FastAPI()
            app.add_middleware(OwnerReadContextMiddleware)
            app.include_router(
                create_management_router(
                    get_read_store=lambda: store,
                    service=ManagementService(get_read_store=lambda: store),
                    run_management_read=run_management_read,
                )
            )
            client = TestClient(app)

            http_headers = {
                "Authorization": "Bearer op-mgmt-cap-001:operator,admin,governance_reviewer:mfa::tenant-a",
                "X-Tenant-Id": "tenant-a",
            }

            # 1. Three 0.35s real HTTP GETs (1.05s serial > 1.0s surface budget) complete concurrently
            # within the 1.0s surface budget with 1 HTTP GET per owner per reload.
            for reload_idx in (1, 2):
                started = time.monotonic()
                resp = client.get("/bff/management/human-inbox", headers=http_headers, params={"page_size": 50})
                elapsed = time.monotonic() - started
                assert resp.status_code == 200, resp.text
                body = resp.json()
                assert not body["meta"].get("partial"), body["meta"]
                surfaces = body["meta"]["surfaces"]
                assert surfaces["governance_review_queue"]["status"] == "ok"
                assert surfaces["approval_queue"]["status"] == "ok"
                assert elapsed < 0.95, f"reload {reload_idx} took {elapsed:.3f}s"
                ids = {i["item_id"] for i in body["data"]["items"]}
                assert ids == {"review-plan-http-1", "review-evo-http-1"}
                assert state["full_counts"]["/api/governance/approvals?decision_state=proposed,under_review"] == reload_idx
                assert state["full_counts"]["/api/governance/approvals?decision_id=apv-decided-0000"] == reload_idx
                assert state["full_counts"].get("/api/governance/approvals", 0) == 0
                assert state["counts"]["/api/deployment/plans"] == reload_idx
                assert state["full_counts"]["/api/evolution/proposals?decision_state=reviewed,under_review,in_review"] == reload_idx
                assert state["full_counts"].get("/api/evolution/proposals", 0) == 0

            # Verify headers forwarded to each owner HTTP endpoint.
            seen_by_path = {path: (auth, tid) for path, auth, tid in state["headers"]}
            assert seen_by_path["/api/governance/approvals"][0] == http_headers["Authorization"]
            assert seen_by_path["/api/deployment/plans"] == (http_headers["Authorization"], http_headers["X-Tenant-Id"])
            assert seen_by_path["/api/evolution/proposals"] == ("Bearer evo-service-secret", http_headers["X-Tenant-Id"])

            # 2. Real transport timeouts on each of the 3 HTTP owners (Deployment, Evolution, Approval)
            # cut off at the surface budget (~0.12s, not 15s/30s) and report read_timeout.
            for slow_path in ("/api/deployment/plans", "/api/evolution/proposals", "/api/governance/approvals"):
                with state_lock:
                    state["delays"] = {
                        "/api/governance/approvals": 0.01,
                        "/api/deployment/plans": 0.01,
                        "/api/evolution/proposals": 0.01,
                        slow_path: 0.6,
                    }
                    before_slow_count = state["counts"][slow_path]
                with patch.dict(os.environ, {"PANTHEON_BFF_HUMAN_INBOX_SURFACE_TIMEOUT_SECONDS": "0.12"}):
                    started = time.monotonic()
                    resp_timeout = client.get("/bff/management/human-inbox", headers=http_headers)
                    elapsed_timeout = time.monotonic() - started
                assert resp_timeout.status_code == 200
                assert elapsed_timeout < 0.48, f"{slow_path} expected ~0.12s transport cutoff, took {elapsed_timeout:.3f}s"
                timeout_body = resp_timeout.json()
                assert timeout_body["meta"]["partial"] is True
                gov_timeout_surface = timeout_body["meta"]["surfaces"]["governance_review_queue"]
                assert gov_timeout_surface["status"] == "degraded"
                assert gov_timeout_surface["source"] == "management_read_timeout"
                assert gov_timeout_surface["reason"] == "read_timeout"
                if slow_path == "/api/governance/approvals":
                    apv_timeout_surface = timeout_body["meta"]["surfaces"]["approval_queue"]
                    assert apv_timeout_surface["status"] == "degraded"
                    assert apv_timeout_surface["reason"] == "read_timeout"
                    assert state["counts"][slow_path] - before_slow_count == 1
                else:
                    assert timeout_body["meta"]["surfaces"]["approval_queue"]["status"] == "ok"

            # 3. Real HTTP 503 on Deployment GET via default DeploymentPlanPort stays degraded (not healthy empty).
            with state_lock:
                state["delays"] = {
                    "/api/governance/approvals": 0.01,
                    "/api/deployment/plans": 0.01,
                    "/api/evolution/proposals": 0.01,
                }
                state["status_codes"] = {"/api/deployment/plans": 503}
            resp_503 = client.get("/bff/management/human-inbox", headers=http_headers)
            assert resp_503.status_code == 200
            body_503 = resp_503.json()
            assert body_503["meta"]["partial"] is True
            gov_503_surface = body_503["meta"]["surfaces"]["governance_review_queue"]
            assert gov_503_surface["status"] == "degraded"
            assert gov_503_surface["reason"] == "contributor_read_error"

            # 4. Non-management / write calls without management_read_deadline preserve default timeouts,
            # while exhausted management_read_deadline (0.0) fails fast with TimeoutError.
            observed_timeouts: List[Any] = []
            orig_urlopen = urllib.request.urlopen

            def _spy_urlopen(req: Any, timeout: Any = None, *a: Any, **kw: Any) -> Any:
                observed_timeouts.append(timeout)
                return orig_urlopen(req, timeout=timeout, *a, **kw)

            with state_lock:
                state["status_codes"] = {}
            with patch("urllib.request.urlopen", side_effect=_spy_urlopen):
                assert management_read_deadline.get() is None
                adapter_base.http_request_json(
                    f"{base_url}/api/deployment/plans",
                    auth_token=http_headers["Authorization"],
                    tenant_id=http_headers["X-Tenant-Id"],
                )
                adapter_base.evolution_owner_request(
                    f"{base_url}/api/evolution/proposals",
                    caller_token=http_headers["Authorization"],
                    tenant_id=http_headers["X-Tenant-Id"],
                )
                approval_owner.list_decisions(http_headers["Authorization"])
                assert observed_timeouts == [
                    command_executor._REQUEST_TIMEOUT,
                    adapter_base._DEFAULT_REQUEST_TIMEOUT,
                    approval_owner._TIMEOUT,
                ]

                tok = management_read_deadline.set(time.monotonic() - 0.01)
                try:
                    with pytest.raises(TimeoutError):
                        adapter_base.http_request_json(
                            f"{base_url}/api/deployment/plans",
                            auth_token=http_headers["Authorization"],
                            tenant_id=http_headers["X-Tenant-Id"],
                            timeout=0.0,
                        )
                    with pytest.raises(TimeoutError):
                        adapter_base.evolution_owner_request(
                            f"{base_url}/api/evolution/proposals",
                            caller_token=http_headers["Authorization"],
                            tenant_id=http_headers["X-Tenant-Id"],
                            timeout=0.0,
                        )
                    with pytest.raises(TimeoutError):
                        approval_owner.list_decisions(http_headers["Authorization"])
                finally:
                    management_read_deadline.reset(tok)
    finally:
        server.shutdown()
        server.server_close()
