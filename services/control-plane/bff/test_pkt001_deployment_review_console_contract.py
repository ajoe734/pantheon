from __future__ import annotations

from datetime import datetime, timezone
import tempfile
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.deployment.adapters import DeploymentReadSurfaceAdapter
from services.control_plane.bff.deployment.router import create_deployment_router
from services.control_plane.bff.governance.service import page_slice, split_csv, stable_json_hash
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


OPERATOR_TOKEN = "Bearer op-2:operator"


def _surface_degradation_reason(
    surface: Dict[str, Any],
    *,
    degraded_reason: str,
    unavailable_reason: str,
) -> Optional[str]:
    status = surface.get("status")
    if status == "ok":
        return None
    if status == "unavailable":
        return unavailable_reason
    if surface.get("message"):
        return str(surface["message"])
    if surface.get("note"):
        return str(surface["note"])
    return degraded_reason


def _make_client(store: Any) -> TestClient:
    def _extract_identity(auth: Optional[str]) -> Any:
        return {"roles": ["operator", "viewer"]}

    def _require_role(_identity: Any) -> None:
        return None

    def _bff_error(status_code: int, code: Any, message: str, detail: Optional[str] = None, **kwargs: Any) -> HTTPException:
        return HTTPException(status_code=status_code, detail={"code": str(code), "message": message, "detail": detail})

    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _dataset_surface_status(
        dataset: str,
        *,
        snapshot_at: Optional[str] = None,
        has_data: Optional[bool] = None,
        missing_message: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        source = store.dataset_source(dataset) if hasattr(store, "dataset_source") else "typed_store"
        surface = {"status": "ok", "source": source}
        if source == "local_snapshot":
            surface["status"] = "degraded"
            surface["note"] = "Served from local BFF snapshot fallback instead of a backend-owned read store."
            surface["staleness"] = {
                "served_from": "local_snapshot",
                "last_known_at": snapshot_at or _utc_now(),
            }
        elif source == "missing":
            surface["status"] = "unavailable"
            surface["staleness"] = {
                "served_from": "unverifiable",
                "last_known_at": snapshot_at or _utc_now(),
            }

        if has_data is False:
            if surface.get("status") == "ok":
                surface["status"] = "unavailable"
            if missing_message:
                surface["message"] = missing_message
            surface.setdefault(
                "staleness",
                {"served_from": "unverifiable", "last_known_at": snapshot_at or _utc_now()},
            )
        return surface

    def _aggregate_group_surface(
        surface_key: str,
        source_surfaces: List[Dict[str, Any]],
        *,
        snapshot_at: str,
        unavailable_message: str,
        degraded_message: str,
    ) -> Dict[str, Any]:
        surface = {"status": "ok", "snapshot_at": snapshot_at, "available": True, "source": "bff_composed"}
        statuses = [entry.get("status", "ok") for entry in source_surfaces]
        if statuses and all(status == "ok" for status in statuses):
            return surface
        if statuses and all(status == "unavailable" for status in statuses):
            surface["status"] = "unavailable"
            surface["message"] = unavailable_message
            return surface
        surface["status"] = "degraded"
        surface["message"] = degraded_message
        return surface

    router = create_deployment_router(
        queries=DeploymentReadSurfaceAdapter(store),
        commands=None,
        extract_identity=_extract_identity,
        require_read_role=_require_role,
        require_operator_role=_require_role,
        bff_error=_bff_error,
        utc_now=_utc_now,
        page_slice=page_slice,
        snapshot_meta=lambda _snapshot_at: {"snapshot_at": _snapshot_at},
        dataset_surface_status=_dataset_surface_status,
        composed_surface_status=lambda *_args, **_kwargs: {"status": "available"},
        read_surface_meta=lambda *_args, **_kwargs: {},
        raise_if_read_surface_unavailable=lambda *_args, **_kwargs: None,
        aggregate_group_surface=_aggregate_group_surface,
        split_csv_query=split_csv,
        meta_staleness=lambda: None,
        stable_json_hash=stable_json_hash,
        resolve_final_idempotency_key=lambda resolved, header: resolved or header or "key",
        reject_body_idempotency_key=lambda _payload: None,
        request_dry_run_requested=lambda *_args, **_kwargs: False,
        gov_bff_idempotency={},
        publish_event=lambda *_args, **_kwargs: "event-id",
        sse_buffers={},
        sse_subscribers={},
        gov_bff_action_command=lambda *_args, **_kwargs: {},
        deprecated_bff_path_response=lambda *_args, **_kwargs: None,
        sem_command_response=lambda *_args, **_kwargs: {},
        stream_generic_events=lambda *_args, **_kwargs: iter(()),
        surface_degradation_reason=_surface_degradation_reason,
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_pkt001_deployment_review_console_list_route_matches_contract() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = create_in_memory_read_surface_ports()
        plans = [
            {
                "id": "plan-approved-001",
                "plan_id": "plan-approved-001",
                "artifact_id": "artifact-approved-001",
                "target_stage": "paper",
                "stage": "paper",
                "status": "approved",
                "approval_decision_id": "approval-approved-001",
                "submitted_at": "2026-04-17T08:00:00Z",
            },
            {
                "id": "plan-pending-002",
                "plan_id": "plan-pending-002",
                "artifact_id": "artifact-pending-002",
                "target_stage": "live",
                "stage": "live",
                "status": "pending_review",
                "approval_decision_id": "approval-pending-002",
                "submitted_at": "2026-04-18T09:30:00Z",
            },
        ]
        decisions = {
            "approval-approved-001": {
                "id": "approval-approved-001",
                "outcome": "approved",
                "state": "decided",
                "risk_level": "low",
            },
            "approval-pending-002": {
                "id": "approval-pending-002",
                "outcome": "pending",
                "state": "pending",
                "risk_level": "medium",
            },
        }
        reviews = {
            "plan-approved-001": {"governanceOutcome": "approved"},
            "plan-pending-002": {"governanceOutcome": "pending"},
        }
        allowed_actions = {
            "plan-approved-001": {
                "canApprove": False,
                "canReject": False,
                "canPromoteToPaper": True,
            },
            "plan-pending-002": {
                "canApprove": True,
                "canReject": True,
                "canPromoteToPaper": False,
            },
        }

        store.list_deployment_plans = lambda status=None, capital_pool_id=None: list(plans)
        store.get_approval_decision = lambda decision_id: decisions.get(decision_id)
        store.get_review_summary = lambda plan_id: reviews.get(plan_id)
        store.get_allowed_actions = lambda plan_id: allowed_actions.get(plan_id, {})
        store.dataset_source = lambda dataset: "service_store"
        client = _make_client(store)

        response = client.get(
            "/api/v1/operator/deployment-plans",
            params={"page_size": 1},
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["items"] == [
            {
                "plan_id": "plan-pending-002",
                "artifact_id": "artifact-pending-002",
                "target_stage": "live",
                "risk_level": "medium",
                "governance_outcome": "pending",
                "submitted_at": "2026-04-18T09:30:00Z",
            }
        ]
        assert payload["page_info"]["next_page_token"] == "1"
        assert payload["meta"]["surfaces"]["deployment_plans"]["status"] == "ok"
        assert payload["meta"]["surfaces"]["allowedActions"]["status"] == "ok"
        assert "degradation" not in payload["meta"]

        page_2 = client.get(
            "/api/v1/operator/deployment-plans",
            params={
                "status": "approved,pending_review",
                "page_size": 1,
                "page_token": "1",
            },
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert page_2.status_code == 200, page_2.text
        next_payload = page_2.json()
        assert next_payload["items"] == [
            {
                "plan_id": "plan-approved-001",
                "artifact_id": "artifact-approved-001",
                "target_stage": "paper",
                "risk_level": "low",
                "governance_outcome": "approved",
                "submitted_at": "2026-04-17T08:00:00Z",
            }
        ]
        assert next_payload["page_info"]["next_page_token"] is None


def test_pkt001_deployment_review_console_list_route_honest_mode_returns_unavailable_surfaces() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = create_in_memory_read_surface_ports()
        store.dataset_source = lambda dataset: "missing"
        client = _make_client(store)

        response = client.get(
            "/api/v1/operator/deployment-plans",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["items"] == []
        assert payload["page_info"]["next_page_token"] is None
        assert payload["meta"]["surfaces"]["deployment_plans"]["status"] == "unavailable"
        assert payload["meta"]["surfaces"]["allowedActions"]["status"] == "unavailable"
        assert payload["meta"]["degradation"]["disable_ctas"] is True


def test_pkt001_deployment_review_detail_includes_full_allowed_actions_shape() -> None:
    with tempfile.TemporaryDirectory() as td:
        store = create_in_memory_read_surface_ports()
        store.get_deployment_plan = lambda plan_id: {
            "plan_id": "plan-F-042",
            "capital_pool_id": "pool-main",
            "approval_decision_id": "approval-042",
        } if plan_id == "plan-F-042" else None
        store.get_allowed_actions = lambda plan_id: {
            "canApprove": False,
            "canReject": False,
            "canPromoteToPaper": True,
        } if plan_id == "plan-F-042" else {}
        store.dataset_source = lambda dataset: "local_snapshot"
        client = _make_client(store)

        response = client.get(
            "/api/v1/operator/deployment-review/plan-F-042",
            headers={"Authorization": OPERATOR_TOKEN},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["data"]["allowedActions"] == {
            "canApprove": False,
            "canReject": False,
            "canPromoteToPaper": True,
        }
        assert payload["meta"]["surfaces"]["allowedActions"]["status"] in {"ok", "degraded"}
        if payload["meta"]["surfaces"]["allowedActions"]["status"] != "ok":
            assert payload["meta"]["degradation"]["disable_ctas"] is True
