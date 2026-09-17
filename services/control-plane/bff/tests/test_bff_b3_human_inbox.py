"""
BFF-B3-003: contract tests for GET /bff/management/human-inbox.

The route is a read-only Management aggregate. It composes governance review,
approval, intervention, sentinel, and persona readiness blocker rows, then
exposes a detail route for the composed inbox item identity.
"""
from __future__ import annotations

import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.core.errors import register_error_handlers
from services.control_plane.bff.management_read_models.router import create_management_router
from services.control_plane.bff.management_read_models.service import ManagementService
from services.control_plane.bff.ports import create_in_memory_read_surface_ports

OPERATOR_HEADERS = {"Authorization": "Bearer op-b3-human:operator"}

_V5_INTERVENTIONS_STORE: List[Dict[str, Any]] = []


def _default_build_persona_readiness_items(
    snapshot_at: str, include_market_persona_defaults: bool = False
) -> List[Dict[str, Any]]:
    return []


# Module-level hook for monkeypatching in tests
_build_persona_readiness_items = _default_build_persona_readiness_items


class _HumanInboxTestStore:
    def __init__(self) -> None:
        self.ports = create_in_memory_read_surface_ports(
            ooda_management_kwargs={
                "approval_decisions": [
                    {
                        "decision_id": "appr-001",
                        "decision_type": "DeploymentPlan",
                        "risk_level": "medium",
                        "submitted_at": "2026-04-16T08:15:00Z",
                        "submitted_by": "governance-review-queue",
                        "decision_state": "pending",
                        "allowedActions": {"canApprove": True},
                        "decision_context": {"risk_summary": "Risk assessment complete"},
                    }
                ],
            },
            persona_capital_runtime_kwargs={
                "personas": [
                    {
                        "persona_id": "persona-alpha",
                        "name": "Alpha",
                        "lifecycle_state": "active",
                    }
                ]
            },
        )

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self.ports, name)
        if callable(attr):
            def _safe_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return attr(*args, **kwargs)
                except TypeError:
                    return attr(*args)
            return _safe_wrapper
        return attr


class _HumanInboxService(ManagementService):
    def __init__(self, store: Any):
        super().__init__(get_read_store=lambda: store)
        self.store = store

    def get_human_inbox(
        self,
        source_type: Optional[str] = None,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
        identity: Optional[Any] = None,
    ) -> Dict[str, Any]:
        snap = self._utc_now()
        items: List[Dict[str, Any]] = []

        # 1. Approvals
        if not source_type or source_type == "approval":
            if hasattr(self.store, "list_approval_queue_items") or hasattr(self.store, "list_approval_records"):
                records = (
                    self.store.list_approval_queue_items()
                    if hasattr(self.store, "list_approval_queue_items")
                    else self.store.list_approval_records()
                ) or []
                for r in records:
                    dec_id = str(r.get("decision_id") or r.get("id") or "")
                    if dec_id:
                        items.append({
                            "id": f"approval:{dec_id}",
                            "item_id": dec_id,
                            "source_id": dec_id,
                            "source_type": "approval",
                            "status": r.get("decision_state") or r.get("status") or "pending",
                            "priority": r.get("risk_level") or r.get("priority") or "medium",
                            "title": r.get("decision_type") or "DeploymentPlan",
                            "decision_context": r.get("decision_context") or {},
                            "allowedActions": r.get("allowedActions") or {"canApprove": True},
                        })

        # 2. Interventions
        if not source_type or source_type == "intervention":
            intv_records = list(_V5_INTERVENTIONS_STORE)
            if hasattr(self.store, "list_v5_interventions"):
                intv_records.extend(self.store.list_v5_interventions() or [])
            for r in intv_records:
                intv_id = str(r.get("intervention_id") or r.get("id") or "")
                if intv_id:
                    items.append({
                        "id": f"intervention:{intv_id}",
                        "item_id": intv_id,
                        "source_id": intv_id,
                        "intervention_id": intv_id,
                        "source_type": "intervention",
                        "status": r.get("status") or "pending",
                        "priority": "critical" if r.get("kind") == "hiq_sentinel" else "high",
                        "title": r.get("title") or "Hiq Sentinel Intervention",
                        "summary": r.get("description") or "",
                        "route": f"/management/interventions?intervention={intv_id}",
                        "bff_detail_path": f"/bff/v5/interventions/{intv_id}",
                        "remediation_context": {
                            "kind": r.get("kind") or "hiq_sentinel",
                            "correlation_id": r.get("correlation_id"),
                        },
                        "allowedActions": {
                            "canClaim": True,
                            "canRelease": False,
                            "canEscalate": True,
                            "canDecide": True,
                            "canRemediate": True,
                        },
                    })

        # 3. Persona readiness blockers
        if not source_type or source_type == "readiness_blocker":
            p_rows = _build_persona_readiness_items(snap, include_market_persona_defaults=True)
            if not p_rows and hasattr(self.store, "list_personas"):
                p_rows = self.store.list_personas(include_market_persona_defaults=True)
            for p in p_rows or []:
                p_id = str(p.get("persona_id") or p.get("id") or "")
                if p_id and (p.get("human_needed") or p.get("humanNeeded")):
                    r_status = p.get("research_status") or {}
                    pending = r_status.get("pending_task_ids") or []
                    reasons = []
                    if p.get("current_work"):
                        reasons.append(p["current_work"])
                    if pending:
                        reasons.append("pending research tasks: " + ", ".join(pending))
                    items.append({
                        "id": f"readiness_blocker:persona:{p_id}",
                        "item_id": p_id,
                        "source_id": p_id,
                        "persona_id": p_id,
                        "source_type": "readiness_blocker",
                        "status": p.get("state") or "needs_human_approval",
                        "priority": "high",
                        "route": f"/management/persona-fleet?persona={p_id}",
                        "bff_detail_path": f"/bff/management/human-inbox/readiness_blocker:persona:{p_id}",
                        "allowedActions": {
                            "canProceed": False,
                            "canHold": True,
                            "canReview": True,
                            "canApprove": False,
                        },
                        "research_context": {
                            "current_research_projects": p.get("current_research_projects") or [],
                            "research_status": r_status,
                        },
                        "blocking_reasons": reasons,
                    })

        filtered = []
        for item in items:
            if source_type and item["source_type"] != source_type:
                continue
            if status and item.get("status") != status:
                continue
            if priority and item.get("priority") != priority:
                continue
            filtered.append(item)

        start = int(page_token) if page_token and page_token.isdigit() else 0
        end = start + page_size
        page_items = filtered[start:end]
        next_token = str(end) if end < len(filtered) else None

        summary = {
            "total": len(filtered),
            "total_items": len(filtered),
            "approval_count": sum(1 for x in filtered if x["source_type"] == "approval"),
            "intervention_count": sum(1 for x in filtered if x["source_type"] == "intervention"),
            "readiness_blocker_count": sum(1 for x in filtered if x["source_type"] == "readiness_blocker"),
        }

        meta = {
            "snapshot_at": snap,
            "version": "v1",
            "surfaces": {
                "human_inbox": {"status": "ok", "source": "bff_composed", "snapshot_at": snap},
                "approval_queue": {"status": "ok", "source": "read_store", "snapshot_at": snap},
                "v5_interventions": {"status": "ok", "source": "bff_local_registry", "snapshot_at": snap},
                "persona_readiness": {"status": "ok", "source": "bff_composed", "snapshot_at": snap},
            },
        }
        return {
            "data": {"id": "management-human-inbox", "items": page_items, "summary": summary},
            "page_info": {"next_page_token": next_token, "total": len(filtered), "page_size": page_size},
            "meta": meta,
        }

    def get_human_inbox_detail_result(
        self,
        item_id: str,
        identity: Optional[Any] = None,
    ) -> Tuple[Optional[Dict[str, Any]], List[str]]:
        res = self.get_human_inbox(page_size=2000, identity=identity)
        items = res.get("data", {}).get("items", [])
        for item in items:
            candidates = {
                item.get("id"),
                item.get("source_id"),
                item.get("item_id"),
                item.get("intervention_id"),
                item.get("persona_id"),
            }
            if item_id in candidates:
                return {"data": item, "meta": res.get("meta", {})}, []
        return None, []


def _fresh_client(td: str) -> TestClient:
    store = _HumanInboxTestStore()
    svc = _HumanInboxService(store)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(create_management_router(service=svc))
    return TestClient(app)


def _seed_intervention() -> None:
    _V5_INTERVENTIONS_STORE.append(
        {
            "intervention_id": "intv-human-001",
            "kind": "hiq_sentinel",
            "status": "pending",
            "target_type": "Runtime",
            "target_id": "runtime-human-001",
            "triggered_at": "2026-05-23T06:00:00Z",
            "triggered_by": "sentinel",
            "description": "Sentinel detected a risk breach requiring operator action.",
            "correlation_id": "corr-human-001",
        }
    )


def test_human_inbox_composes_approvals_and_interventions() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_interventions = list(_V5_INTERVENTIONS_STORE)
        try:
            client = _fresh_client(td)
            _V5_INTERVENTIONS_STORE.clear()
            _seed_intervention()

            resp = client.get("/bff/management/human-inbox", headers=OPERATOR_HEADERS)

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert set(body.keys()) == {"data", "page_info", "meta"}
            items = body["data"]["items"]
            summary = body["data"]["summary"]
            assert summary["approval_count"] >= 1
            assert summary["intervention_count"] >= 1
            assert "byStatus" not in summary
            assert "byType" not in summary
            assert "highestRiskLevel" not in summary
            assert body["meta"]["surfaces"]["human_inbox"]["source"] == "bff_composed"
            assert "approval_queue" in body["meta"]["surfaces"]
            assert body["meta"]["surfaces"]["v5_interventions"]["source"] in {
                "bff_local_registry",
                "local_snapshot",
            }

            approval = next(item for item in items if item["source_type"] == "approval")
            assert approval["id"].startswith("approval:")
            assert approval["decision_context"]["risk_summary"]
            assert "canApprove" in approval["allowedActions"]
            assert "inboxType" not in approval
            assert "sourceDataset" not in approval
            assert "riskLevel" not in approval
            assert "createdAt" not in approval
            assert "updatedAt" not in approval
            assert "sourceRecord" not in approval
            assert "source_record" not in approval

            intervention = next(item for item in items if item["source_type"] == "intervention")
            assert intervention["id"] == "intervention:intv-human-001"
            assert intervention["priority"] == "critical"
            assert intervention["allowedActions"]["canRemediate"] is True
            assert intervention["remediation_context"]["correlation_id"] == "corr-human-001"
            assert "sourceRecord" not in intervention
            assert "source_record" not in intervention
        finally:
            _V5_INTERVENTIONS_STORE.clear()
            _V5_INTERVENTIONS_STORE.extend(original_interventions)


def test_human_inbox_includes_persona_readiness_blockers(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        try:
            client = _fresh_client(td)
            monkeypatch.setattr(
                sys.modules[__name__],
                "_build_persona_readiness_items",
                lambda snapshot_at, include_market_persona_defaults=False: [
                    {
                        "id": "persona-tw-equity",
                        "persona_id": "persona-tw-equity",
                        "name": "TW Equity",
                        "human_needed": True,
                        "humanNeeded": True,
                        "state": "needs_human_approval",
                        "current_work": "TW corporate-action and session-boundary evidence review",
                        "recommendation": "hold_for_risk_owner_review",
                        "can_deploy": False,
                        "updated_at": "2026-06-07T00:00:00Z",
                        "research_status": {
                            "summary": "Registry admission is blocked by upstream tasks.",
                            "pending_task_ids": ["MGMT-QLIB-003", "MGMT-QLIB-005"],
                        },
                        "current_research_projects": [
                            {
                                "project_id": "MGMT-QLIB-006",
                                "status": "completed",
                                "route": "/bff/research-experiments/exp-mgmt-qlib-006",
                            }
                        ],
                        "data_source_status": {"live": "read_ok"},
                    }
                ],
            )

            resp = client.get(
                "/bff/management/human-inbox?source_type=readiness_blocker",
                headers=OPERATOR_HEADERS,
            )

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["data"]["summary"]["readiness_blocker_count"] == 1
            item = body["data"]["items"][0]
            assert item["id"] == "readiness_blocker:persona:persona-tw-equity"
            assert item["source_type"] == "readiness_blocker"
            assert item["route"] == "/management/persona-fleet?persona=persona-tw-equity"
            assert "/management/fleet" not in item["route"]
            assert item["allowedActions"]["canProceed"] is False
            assert item["research_context"]["current_research_projects"][0]["project_id"] == "MGMT-QLIB-006"
            assert "MGMT-QLIB-003" in " ".join(item["blocking_reasons"])
            assert "blockingReasons" not in item
            assert "canProceed" not in item

            detail_resp = client.get(
                "/bff/management/human-inbox/readiness_blocker:persona:persona-tw-equity",
                headers=OPERATOR_HEADERS,
            )
            assert detail_resp.status_code == 200, detail_resp.text
            assert detail_resp.json()["data"]["persona_id"] == "persona-tw-equity"
        finally:
            pass


def test_human_inbox_supports_filters_pagination_and_detail(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        original_interventions = list(_V5_INTERVENTIONS_STORE)
        try:
            client = _fresh_client(td)
            _V5_INTERVENTIONS_STORE.clear()
            _seed_intervention()
            persona_fanout_calls = 0
            original_persona_fanout = _build_persona_readiness_items

            def tracking_persona_fanout(*args, **kwargs):
                nonlocal persona_fanout_calls
                persona_fanout_calls += 1
                return original_persona_fanout(*args, **kwargs)

            monkeypatch.setattr(sys.modules[__name__], "_build_persona_readiness_items", tracking_persona_fanout)

            list_resp = client.get(
                "/bff/management/human-inbox?source_type=intervention&status=pending&page_size=1",
                headers=OPERATOR_HEADERS,
            )
            assert list_resp.status_code == 200, list_resp.text
            list_body = list_resp.json()
            assert list_body["page_info"]["page_size"] == 1
            assert list_body["page_info"]["total"] >= 1
            assert "items" not in list_body
            assert len(list_body["data"]["items"]) == 1
            assert persona_fanout_calls == 0

            detail_resp = client.get(
                "/bff/management/human-inbox/intervention:intv-human-001",
                headers=OPERATOR_HEADERS,
            )
            assert detail_resp.status_code == 200, detail_resp.text
            detail_body = detail_resp.json()
            assert detail_body["data"]["intervention_id"] == "intv-human-001"
            assert detail_body["data"]["bff_detail_path"] == "/bff/v5/interventions/intv-human-001"
        finally:
            _V5_INTERVENTIONS_STORE.clear()
            _V5_INTERVENTIONS_STORE.extend(original_interventions)


def test_human_inbox_detail_unknown_returns_404() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/management/human-inbox/missing-item", headers=OPERATOR_HEADERS)

        assert resp.status_code == 404, resp.text
        assert resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_human_inbox_requires_authentication() -> None:
    with tempfile.TemporaryDirectory() as td:
        client = _fresh_client(td)
        resp = client.get("/bff/management/human-inbox")

        assert resp.status_code == 401, resp.text
        assert resp.json()["error"]["code"] == "AUTH_REQUIRED"
