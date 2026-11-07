"""HUMAN-INBOX-OWNER-FILTERED-READ-20261107: filtered owner reads for Human Inbox.

Verifies all seven acceptance criteria at the hosted scale of 575 approval
decisions (572 decided, 3 revoked; 568 registry_entry, 2 persona_lifecycle_transition,
1 each of evolution_proposal, evolution_execute, strategy_workshop,
capital_pool_activation, capital_binding_activation) and 446 evolution decisions
(443 proposed, 1 approved, 1 executed, 1 canceled; all candidate_artifact):
1. Human Inbox asks the governance owner only for pending decisions
   (?decision_state=proposed,under_review) and never receives decided, revoked,
   or superseded decisions; both Postgres and JSON stores filter before
   building ApprovalDecision objects.
2. Human Inbox asks the evolution owner only for decidable decisions
   (?decision_state=reviewed,under_review,in_review) and never receives proposed
   or terminal evolution decisions; the owner filters before serializing.
3. The prewarm reads in get_human_inbox use the same filtered reads; no full
   approval or evolution decision list is read on behalf of an inbox read.
4. Reviewable deployment plans whose linked approval decisions are terminal keep
   today's allowedActions and review_summary via one bounded owner read per
   inbox read (?decision_id=...) and never from the full list or one read per plan.
5. For the same owner data, inbox items, summary, and surfaces equal the legacy
   unfiltered composition at the 575 / 446 scale, both when 0 items are decidable
   and when a decidable pending row of each kind is present.
6. Every other caller of list_governance_review_queue_items and
   list_approval_queue_items, plus GET /api/governance/approvals and
   GET /api/evolution/proposals without the new filter, behaves as today.
7. Contributor budget (1.0s) and route deadline (2.5s) remain unchanged, with
   dramatically smaller owner response bytes on the filtered path.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import urllib.parse
from typing import Any, Dict, List, Optional
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.core.owner_reads import (
    DECIDABLE_EVOLUTION_QUERY_PATH,
    OwnerReadContextMiddleware,
    authorization,
    selected_tenant,
)
from services.control_plane.bff.governance import approval_owner
from services.control_plane.bff.governance.service import human_inbox_surface_timeout_seconds
from services.control_plane.bff.management_read_models import create_management_router
from services.control_plane.bff.management_read_models import router as inbox_router
from services.control_plane.bff.management_read_models.service import ManagementService
from services.control_plane.bff.personas.routes.common import run_management_read
from services.control_plane.bff.ports import create_read_surface_ports
from services.control_plane.bff.ports.ooda_management import ManagementReviewQueuePort


HTTP_HEADERS = {
    "Authorization": "Bearer op-mgmt-cap-001:operator,admin,governance_reviewer:mfa::tenant-a",
    "X-Tenant-Id": "tenant-a",
}


def build_hosted_scale_approval_decisions(
    *,
    include_pending: bool = False,
) -> List[Dict[str, Any]]:
    """Build the exact hosted distribution of 575 terminal approval decisions:
    - 572 decided + 3 revoked
    - target_types:
      568 registry_entry,
      2 persona_lifecycle_transition,
      1 evolution_proposal,
      1 evolution_execute,
      1 strategy_workshop,
      1 capital_pool_activation,
      1 capital_binding_activation
    """
    target_types = (
        ["registry_entry"] * 568
        + ["persona_lifecycle_transition"] * 2
        + [
            "evolution_proposal",
            "evolution_execute",
            "strategy_workshop",
            "capital_pool_activation",
            "capital_binding_activation",
        ]
    )
    assert len(target_types) == 575
    rows: List[Dict[str, Any]] = []
    for idx, target_type in enumerate(target_types):
        is_revoked = idx >= 572
        state = "revoked" if is_revoked else "decided"
        outcome = None if is_revoked else ("approved" if idx % 2 == 0 else "rejected")
        rows.append(
            {
                "decision_id": f"apv-hosted-{idx:04d}",
                "tenant_id": "tenant-a",
                "target_type": target_type,
                "target_id": f"{target_type}-{idx:04d}",
                "target_version": "1.0.0",
                "decision_state": state,
                "state": state,
                "decision": outcome,
                "outcome": outcome,
                "actor_id": "persona-agent",
                "actor_role": "governance_reviewer",
                "reviewer": "gov-reviewer",
                "rationale": f"Historical {state} decision {idx:04d} for {target_type} with audit payload.",
                "conditions": [],
                "risk_level": "high" if idx % 5 == 0 else "medium",
                "evidence_refs": [f"evidence://governance/{target_type}/{idx:04d}"],
                "created_at": "2026-10-15T08:00:00Z",
                "updated_at": "2026-10-15T09:00:00Z",
                "decided_at": None if is_revoked else "2026-10-15T09:00:00Z",
                "revoked_at": "2026-10-16T09:00:00Z" if is_revoked else None,
            }
        )
    if include_pending:
        rows.append(
            {
                "decision_id": "apv-pending-live-0001",
                "tenant_id": "tenant-a",
                "target_type": "registry_entry",
                "target_id": "reg-pending-0001",
                "target_version": "2.0.0",
                "decision_state": "under_review",
                "state": "under_review",
                "decision": None,
                "outcome": None,
                "actor_id": "op-submitter",
                "actor_role": "governance_reviewer",
                "rationale": "Pending registry entry approval requiring human decision.",
                "conditions": [],
                "risk_level": "high",
                "evidence_refs": ["evidence://governance/pending/0001"],
                "created_at": "2026-11-07T10:00:00Z",
                "updated_at": "2026-11-07T10:05:00Z",
            }
        )
    return rows


def build_hosted_scale_evolution_decisions(
    *,
    include_decidable: bool = False,
) -> List[Dict[str, Any]]:
    """Build the exact hosted distribution of 446 non-decidable evolution decisions:
    - 443 proposed + 1 approved + 1 executed + 1 canceled
    - all target_type == candidate_artifact
    """
    states = ["proposed"] * 443 + ["approved", "executed", "canceled"]
    assert len(states) == 446
    rows: List[Dict[str, Any]] = []
    for idx, state in enumerate(states):
        rows.append(
            {
                "decision_id": f"evo-hosted-{idx:04d}",
                "tenant_id": "tenant-a",
                "target_type": "candidate_artifact",
                "target_id": f"cand-hosted-{idx:04d}",
                "target_version": "v1",
                "action_type": "promote",
                "decision_state": state,
                "status": state,
                "risk_level": "medium",
                "rationale": f"Evolution candidate {idx:04d} in {state} state.",
                "created_by_id": "evolution-service",
                "created_at": "2026-10-20T12:00:00Z",
                "updated_at": "2026-10-20T12:05:00Z",
                "evidence_refs": [f"evidence://evolution/cand/{idx:04d}"],
                "metadata": {"program_id": "prog-hosted-01"},
            }
        )
    if include_decidable:
        rows.append(
            {
                "decision_id": "evo-decidable-live-0001",
                "tenant_id": "tenant-a",
                "target_type": "candidate_artifact",
                "target_id": "cand-decidable-0001",
                "target_version": "v2",
                "action_type": "promote",
                "decision_state": "under_review",
                "status": "under_review",
                "risk_level": "high",
                "rationale": "Candidate artifact ready for human approve/reject.",
                "created_by_id": "evolution-service",
                "created_at": "2026-11-07T11:00:00Z",
                "updated_at": "2026-11-07T11:05:00Z",
                "evidence_refs": ["evidence://evolution/cand/decidable-0001"],
                "metadata": {"program_id": "prog-hosted-01"},
            }
        )
    return rows


class _HostedScaleOwnerServer:
    """Real HTTP server emulating Governance, Evolution, Deployment, and Incidents owners."""

    def __init__(
        self,
        *,
        approvals: List[Dict[str, Any]],
        evolution: List[Dict[str, Any]],
        plans: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.approvals = list(approvals)
        self.evolution = list(evolution)
        self.plans = list(plans or [])
        self.lock = threading.Lock()
        self.requests: List[Dict[str, Any]] = []
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def reset_metrics(self) -> None:
        with self.lock:
            self.requests.clear()

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_port}"

    def __enter__(self) -> "_HostedScaleOwnerServer":
        parent = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlsplit(self.path)
                path = parsed.path
                qs = urllib.parse.parse_qs(parsed.query)
                if path == "/api/governance/approvals":
                    rows = list(parent.approvals)
                    if "decision_state" in qs:
                        wanted_states = {
                            part.strip().lower()
                            for raw in qs["decision_state"]
                            for part in raw.split(",")
                            if part.strip()
                        }
                        rows = [
                            r
                            for r in rows
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
                            r
                            for r in rows
                            if str(r.get("decision_id") or r.get("id") or "") in wanted_ids
                        ]
                    payload = json.dumps(rows).encode("utf-8")
                elif path == "/api/evolution/proposals":
                    rows = list(parent.evolution)
                    if "decision_state" in qs:
                        wanted_states = {
                            part.strip().lower()
                            for raw in qs["decision_state"]
                            for part in raw.split(",")
                            if part.strip()
                        }
                        rows = [
                            r
                            for r in rows
                            if str(r.get("decision_state") or r.get("status") or "").lower() in wanted_states
                        ]
                    payload = json.dumps(rows).encode("utf-8")
                elif path == "/api/deployment/plans":
                    rows = list(parent.plans)
                    payload = json.dumps(rows).encode("utf-8")
                else:
                    rows = []
                    payload = b"[]"

                with parent.lock:
                    parent.requests.append(
                        {
                            "full_path": self.path,
                            "path": path,
                            "query": dict(qs),
                            "row_count": len(rows),
                            "response_bytes": len(payload),
                        }
                    )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


def _build_client(base_url: str) -> tuple[TestClient, Any]:
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
    return TestClient(app), store


def test_human_inbox_zero_items_at_hosted_scale_uses_only_filtered_owner_reads() -> None:
    """Acceptance 1, 2, 3, 5, 7:
    At the real hosted scale of 575 terminal approval decisions (572 decided +
    3 revoked) and 446 non-decidable evolution decisions (443 proposed + 1 approved +
    1 executed + 1 canceled):
    - Human Inbox requests only ?decision_state=proposed,under_review from Governance
      and ?decision_state=reviewed,under_review,in_review from Evolution.
    - Both owners return 0 rows (2 bytes '[]') instead of ~350KB+ of non-actionable JSON.
    - Prewarm and contributor reads share the exact same filtered request cache keys
      (1 GET per owner per inbox read, 0 unfiltered GETs).
    - Items, summary, and surfaces match legacy unfiltered composition.
    """
    assert human_inbox_surface_timeout_seconds() == 1.0
    assert inbox_router._get_human_inbox_read_timeout() == 2.5

    approvals = build_hosted_scale_approval_decisions(include_pending=False)
    evolution = build_hosted_scale_evolution_decisions(include_decidable=False)
    assert len(approvals) == 575
    assert len(evolution) == 446

    with _HostedScaleOwnerServer(approvals=approvals, evolution=evolution, plans=[]) as server:
        env = {
            "PANTHEON_GOVERNANCE_APPROVAL_API_URL": server.url,
            "PANTHEON_DEPLOYMENT_API_URL": server.url,
            "PANTHEON_EVOLUTION_API_URL": server.url,
            "PANTHEON_INCIDENTS_API_URL": server.url,
            "EVOLUTION_AUTH_TOKEN": "evo-service-secret",
        }
        with patch.dict(os.environ, env):
            client, store = _build_client(server.url)
            resp = client.get("/bff/management/human-inbox", headers=HTTP_HEADERS, params={"page_size": 50})
            assert resp.status_code == 200, resp.text
            filtered_body = resp.json()

            recorded = list(server.requests)
            full_paths = [r["full_path"] for r in recorded]
            assert "/api/governance/approvals?decision_state=proposed,under_review" in full_paths
            assert DECIDABLE_EVOLUTION_QUERY_PATH in full_paths
            assert "/api/governance/approvals" not in full_paths
            assert "/api/evolution/proposals" not in full_paths

            gov_reqs = [r for r in recorded if r["path"] == "/api/governance/approvals"]
            evo_reqs = [r for r in recorded if r["path"] == "/api/evolution/proposals"]
            assert len(gov_reqs) == 1
            assert len(evo_reqs) == 1
            assert gov_reqs[0]["row_count"] == 0
            assert gov_reqs[0]["response_bytes"] == 2
            assert evo_reqs[0]["row_count"] == 0
            assert evo_reqs[0]["response_bytes"] == 2

            # Compare against legacy unfiltered composition over the exact same data.
            legacy_port = ManagementReviewQueuePort(
                deployment_plans_reader=lambda: [],
                approval_decisions_reader=lambda: [approval_owner.project(a) for a in approvals],
                evolution_decisions_reader=lambda: evolution,
            )
            assert legacy_port.list_approval_queue_items() == []
            tok_auth = authorization.set(HTTP_HEADERS["Authorization"])
            tok_tenant = selected_tenant.set(HTTP_HEADERS["X-Tenant-Id"])
            try:
                assert store.list_approval_queue_items(pending_only=True) == []
            finally:
                selected_tenant.reset(tok_tenant)
                authorization.reset(tok_auth)
            assert filtered_body["data"]["items"] == []
            assert filtered_body["data"]["summary"]["total_items"] == 0
            assert filtered_body["data"]["summary"]["pending_items"] == 0
            assert filtered_body["meta"]["surfaces"]["human_inbox"]["status"] == "ok"
            assert filtered_body["meta"]["surfaces"]["approval_queue"]["status"] == "ok"
            assert filtered_body["meta"]["surfaces"]["governance_review_queue"]["status"] == "ok"


def test_human_inbox_at_hosted_scale_with_pending_items_and_terminal_plan_decisions_matches_legacy() -> None:
    """Acceptance 4, 5, 6:
    When 1 decidable pending approval decision, 1 decidable under_review evolution
    decision, and 2 reviewable deployment plans linked to terminal approval decisions
    are present alongside the 575 terminal approvals and 446 non-decidable evolution
    decisions:
    - Human Inbox receives only the 1 pending approval from ?decision_state=proposed,under_review
      and 1 decidable evolution decision from ?decision_state=reviewed,under_review,in_review.
    - The 2 terminal approval decisions linked to the 2 reviewable deployment plans
      are fetched in ONE bounded owner read (?decision_id=apv-hosted-0000,apv-hosted-0001)
      and never from the full list or one read per plan.
    - Inbox items, allowedActions, review_summary, summary counts, and surfaces
      equal the legacy unfiltered path.
    - Non-inbox callers (store.list_governance_review_queue_items() without
      decidable_only) still return proposed evolution decisions as before.
    """
    approvals = build_hosted_scale_approval_decisions(include_pending=True)
    evolution = build_hosted_scale_evolution_decisions(include_decidable=True)
    plans = [
        {
            "plan_id": "plan-terminal-approved",
            "status": "pending_review",
            "target_stage": "paper",
            "approval_decision_id": "apv-hosted-0000",  # decided / approved
            "submitted_at": "2026-11-06T09:00:00Z",
            "created_by": "deployment-service",
            "risk_summary": "Paper rollout with approved decision",
        },
        {
            "plan_id": "plan-terminal-rejected",
            "status": "under_review",
            "target_stage": "paper",
            "approval_decision_id": "apv-hosted-0001",  # decided / rejected
            "submitted_at": "2026-11-06T09:30:00Z",
            "created_by": "deployment-service",
            "risk_summary": "Paper rollout with rejected decision",
        },
    ]

    with _HostedScaleOwnerServer(approvals=approvals, evolution=evolution, plans=plans) as server:
        env = {
            "PANTHEON_GOVERNANCE_APPROVAL_API_URL": server.url,
            "PANTHEON_DEPLOYMENT_API_URL": server.url,
            "PANTHEON_EVOLUTION_API_URL": server.url,
            "PANTHEON_INCIDENTS_API_URL": server.url,
            "EVOLUTION_AUTH_TOKEN": "evo-service-secret",
        }
        with patch.dict(os.environ, env):
            client, store = _build_client(server.url)
            resp = client.get("/bff/management/human-inbox", headers=HTTP_HEADERS, params={"page_size": 50})
            assert resp.status_code == 200, resp.text
            filtered_body = resp.json()

            recorded = list(server.requests)
            gov_reqs = [r for r in recorded if r["path"] == "/api/governance/approvals"]
            evo_reqs = [r for r in recorded if r["path"] == "/api/evolution/proposals"]

            # Exactly 2 Governance owner reads: 1 pending-state read + 1 batched decision_id read for the 2 plans
            assert len(gov_reqs) == 2, gov_reqs
            assert gov_reqs[0]["full_path"] == "/api/governance/approvals?decision_state=proposed,under_review"
            assert gov_reqs[0]["row_count"] == 1
            assert gov_reqs[1]["full_path"] == "/api/governance/approvals?decision_id=apv-hosted-0000,apv-hosted-0001"
            assert gov_reqs[1]["row_count"] == 2

            # Exactly 1 Evolution owner read: decidable states only
            assert len(evo_reqs) == 1, evo_reqs
            assert evo_reqs[0]["full_path"] == DECIDABLE_EVOLUTION_QUERY_PATH
            assert evo_reqs[0]["row_count"] == 1

            # Build legacy unfiltered store and verify identical Human Inbox output
            legacy_store = create_read_surface_ports(
                ooda_management=store.ooda_management.__class__(
                    review_queue_port=ManagementReviewQueuePort(
                        deployment_plans_reader=lambda: plans,
                        approval_decisions_reader=lambda: [approval_owner.project(a) for a in approvals],
                        evolution_decisions_reader=lambda: evolution,
                    )
                )
            )
            legacy_store.list_personas = lambda **_k: []  # type: ignore[method-assign]
            legacy_store.list_incidents = lambda **_k: []  # type: ignore[method-assign]
            legacy_service = ManagementService(get_read_store=lambda: legacy_store)
            legacy_body = legacy_service.get_human_inbox(page_size=50)

            assert filtered_body["data"]["items"] == legacy_body["data"]["items"]
            assert filtered_body["data"]["summary"] == legacy_body["data"]["summary"]

            item_ids = {item["item_id"] for item in filtered_body["data"]["items"]}
            assert item_ids == {
                "apv-pending-live-0001",
                "review-evo-decidable-live-0001",
                "review-plan-terminal-approved",
                "review-plan-terminal-rejected",
            }

            plan_approved = next(
                i for i in filtered_body["data"]["items"] if i["item_id"] == "review-plan-terminal-approved"
            )
            assert plan_approved["allowedActions"] == {
                "canApprove": False,
                "canReject": False,
                "canPromoteToPaper": True,
            }
            assert plan_approved["details"]["review_summary"]["governanceOutcome"] == "approved"
            assert plan_approved["details"]["review_summary"]["reviewer"] == "gov-reviewer"

            # Acceptance 6: non-inbox callers of list_governance_review_queue_items()
            # (decidable_only=False default) still receive proposed evolution items as today.
            server.reset_metrics()
            tok_auth = authorization.set(HTTP_HEADERS["Authorization"])
            tok_tenant = selected_tenant.set(HTTP_HEADERS["X-Tenant-Id"])
            try:
                all_review_items = store.list_governance_review_queue_items()
            finally:
                selected_tenant.reset(tok_tenant)
                authorization.reset(tok_auth)
            evo_review_items = [i for i in all_review_items if i["item_type"] == "EvolutionDecision"]
            assert len(evo_review_items) == 444  # 443 proposed + 1 under_review
            unfiltered_paths = [r["full_path"] for r in server.requests]
            assert "/api/governance/approvals" in unfiltered_paths
            assert "/api/evolution/proposals" in unfiltered_paths


def test_old_vs_new_hosted_scale_call_counts_bytes_and_timing_metrics() -> None:
    """Acceptance 7: compare old (unfiltered) and new (filtered) owner call counts,
    response bytes, and timings at the 575 approvals / 446 evolution decisions scale.
    """
    import time
    from services.control_plane.bff.core.owner_reads import (
        approval_records,
        create_owner_domain_ports,
    )
    from services.control_plane.bff.ports.ooda_management import OodaManagementDomainPort

    approvals = build_hosted_scale_approval_decisions(include_pending=False)
    evolution = build_hosted_scale_evolution_decisions(include_decidable=False)

    with _HostedScaleOwnerServer(approvals=approvals, evolution=evolution, plans=[]) as server:
        env = {
            "PANTHEON_GOVERNANCE_APPROVAL_API_URL": server.url,
            "PANTHEON_DEPLOYMENT_API_URL": server.url,
            "PANTHEON_EVOLUTION_API_URL": server.url,
            "PANTHEON_INCIDENTS_API_URL": server.url,
            "EVOLUTION_AUTH_TOKEN": "evo-service-secret",
        }
        with patch.dict(os.environ, env):
            client_new, _ = _build_client(server.url)
            client_new.get("/bff/management/human-inbox", headers=HTTP_HEADERS, params={"page_size": 50})
            server.reset_metrics()
            t0 = time.perf_counter()
            resp_new = client_new.get("/bff/management/human-inbox", headers=HTTP_HEADERS, params={"page_size": 50})
            new_elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            assert resp_new.status_code == 200
            new_reqs = list(server.requests)

            pcr_old = create_owner_domain_ports()
            store_old = create_read_surface_ports(
                persona_capital_runtime=pcr_old,
                ooda_management=OodaManagementDomainPort(
                    review_queue_port=ManagementReviewQueuePort(
                        deployment_plans_reader=pcr_old.list_deployment_plans,
                        evolution_decisions_reader=pcr_old.list_evolution_decisions,
                        approval_decisions_reader=approval_records,
                    )
                ),
            )
            store_old.list_personas = lambda **_k: []  # type: ignore[method-assign]
            app_old = FastAPI()
            app_old.add_middleware(OwnerReadContextMiddleware)
            app_old.include_router(
                create_management_router(
                    get_read_store=lambda: store_old,
                    service=ManagementService(get_read_store=lambda: store_old),
                    run_management_read=run_management_read,
                )
            )
            client_old = TestClient(app_old)
            client_old.get("/bff/management/human-inbox", headers=HTTP_HEADERS, params={"page_size": 50})
            server.reset_metrics()
            t0 = time.perf_counter()
            resp_old = client_old.get("/bff/management/human-inbox", headers=HTTP_HEADERS, params={"page_size": 50})
            old_elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            assert resp_old.status_code == 200
            old_reqs = list(server.requests)

            new_bytes = sum(r["response_bytes"] for r in new_reqs)
            old_bytes = sum(r["response_bytes"] for r in old_reqs)
            assert len(new_reqs) == 4
            assert len(old_reqs) == 4
            assert new_bytes == 8  # four empty JSON arrays "[]"
            assert old_bytes > 500_000
            print(
                "EVIDENCE_METRICS="
                + json.dumps(
                    {
                        "new_filtered_path": {
                            "owner_call_count": len(new_reqs),
                            "total_response_bytes": new_bytes,
                            "elapsed_ms": new_elapsed_ms,
                            "requests": new_reqs,
                        },
                        "old_unfiltered_path": {
                            "owner_call_count": len(old_reqs),
                            "total_response_bytes": old_bytes,
                            "elapsed_ms": old_elapsed_ms,
                            "requests": old_reqs,
                        },
                    }
                )
            )

