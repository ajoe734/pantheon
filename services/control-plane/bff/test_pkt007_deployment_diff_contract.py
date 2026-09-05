from __future__ import annotations

import os
import sys
import tempfile

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.deployment.router import create_deployment_router
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


OPERATOR_TOKEN = "Bearer op-2:operator"


def _deployment_diff(plan_id: str):
    diffs = {
        "plan-dp-001": {
            "plan_id": "plan-dp-001",
            "artifact_id": "artifact-abc123",
            "previous_plan_id": "plan-dp-000",
            "first_deployment": False,
            "changes": [{"field_path": field} for field in ("parameters.max_drawdown", "parameters.position_size_limit", "bindings[0].capital_pool_id", "risk_controls.stop_loss_threshold")],
            "change_summary": {
                "total_changes": 4,
                "by_category": {"bindings": {"count": 1, "highest_risk_tier": "high"}},
            },
            "allowedActions": {"canProceedToApproval": True, "canEscalateDiff": True},
        },
        "plan-dp-002": {
            "plan_id": "plan-dp-002",
            "artifact_id": "artifact-def456",
            "previous_plan_id": None,
            "first_deployment": True,
            "changes": [],
            "change_summary": {"total_changes": 0},
            "allowedActions": {"canProceedToApproval": False, "canEscalateDiff": True},
        },
    }
    return diffs.get(plan_id)


def _make_client(store) -> TestClient:
    app = FastAPI()

    def _dataset_surface_status(dataset: str, *, snapshot_at=None, has_data=None, missing_message=None, source=None, **kwargs):
        src = source or (store.dataset_source(dataset) if hasattr(store, "dataset_source") else "local_snapshot")
        status = "degraded" if src == "local_snapshot" else ("unavailable" if src == "missing" else "ok")
        if has_data is False and status == "ok":
            status = "unavailable"
        return {
            "status": status,
            "source": src,
            "staleness": {"served_from": src, "last_known_at": snapshot_at or "2026-05-01T00:00:00Z"},
        }

    def _composed_surface_status(*, snapshot_at=None, available=True, missing_message=None, **kwargs):
        return {
            "status": "available" if available else "unavailable",
            "staleness": {"served_from": "composed", "last_known_at": snapshot_at or "2026-05-01T00:00:00Z"},
        }

    dep_router = create_deployment_router(
        queries=store,
        extract_identity=lambda auth: OperatorIdentity(operator_id="op-2", roles=["operator", "admin"]),
        require_read_role=lambda identity: None,
        require_operator_role=lambda identity: None,
        bff_error=lambda status, code, msg, *a, **kw: HTTPException(status, {"code": str(code), "message": msg}),
        utc_now=lambda: "2026-05-01T00:00:00Z",
        page_slice=lambda items, _token, _size: (items, None),
        snapshot_meta=lambda _snapshot_at: {"snapshot_at": _snapshot_at},
        dataset_surface_status=_dataset_surface_status,
        composed_surface_status=_composed_surface_status,
        read_surface_meta=lambda dataset, key, *, total=None, snapshot_at=None, **kwargs: {"total": total, "snapshot_at": snapshot_at, "surfaces": {}},
        raise_if_read_surface_unavailable=lambda *_args, **_kwargs: None,
        aggregate_group_surface=lambda *_args, **_kwargs: {"status": "available"},
        split_csv_query=lambda value: value.split(",") if value else None,
        meta_staleness=lambda: None,
        stable_json_hash=lambda payload: "hash",
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
        surface_degradation_reason=lambda *_args, **_kwargs: None,
    )
    app.include_router(dep_router)
    return TestClient(app)


def test_pkt007_deployment_diff_returns_contract_payload() -> None:
    store = create_in_memory_read_surface_ports()
    store.get_deployment_diff = _deployment_diff
    store.dataset_source = lambda dataset: "local_snapshot" if dataset == "deployment_diffs" else "missing"
    client = _make_client(store)

    response = client.get(
        "/api/v1/operator/deployment-diff/plan-dp-001",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plan_id"] == "plan-dp-001"
    assert payload["artifact_id"] == "artifact-abc123"
    assert payload["previous_plan_id"] == "plan-dp-000"
    assert payload["first_deployment"] is False
    assert len(payload["changes"]) == 4
    assert payload["change_summary"]["total_changes"] == 4
    assert payload["change_summary"]["by_category"]["bindings"]["highest_risk_tier"] == "high"
    assert payload["allowedActions"]["canProceedToApproval"] is True
    assert payload["allowedActions"]["canEscalateDiff"] is True
    assert payload["meta"]["surfaces"]["deployment_diff"]["status"] == "degraded"
    assert payload["meta"]["surfaces"]["deployment_diff"]["source"] == "local_snapshot"
    assert payload["meta"]["surfaces"]["allowedActions"]["status"] == "degraded"


def test_pkt007_deployment_diff_supports_first_deployment_shape() -> None:
    store = create_in_memory_read_surface_ports()
    store.get_deployment_diff = _deployment_diff
    store.dataset_source = lambda dataset: "local_snapshot" if dataset == "deployment_diffs" else "missing"
    client = _make_client(store)

    response = client.get(
        "/api/v1/operator/deployment-diff/plan-dp-002",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plan_id"] == "plan-dp-002"
    assert payload["previous_plan_id"] is None
    assert payload["first_deployment"] is True
    assert payload["changes"] == []
    assert payload["change_summary"]["total_changes"] == 0
    assert payload["allowedActions"]["canProceedToApproval"] is False
    assert payload["allowedActions"]["canEscalateDiff"] is True


def test_pkt007_deployment_diff_returns_unavailable_payload_in_honest_mode() -> None:
    store = create_in_memory_read_surface_ports()
    store.dataset_source = lambda dataset: "missing"
    client = _make_client(store)

    response = client.get(
        "/api/v1/operator/deployment-diff/plan-dp-001",
        headers={"Authorization": OPERATOR_TOKEN},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["plan_id"] == "plan-dp-001"
    assert payload["artifact_id"] is None
    assert payload["changes"] == []
    assert payload["allowedActions"]["canProceedToApproval"] is False
    assert payload["meta"]["surfaces"]["deployment_diff"]["status"] == "unavailable"
    assert payload["meta"]["surfaces"]["deployment_diff"]["source"] == "missing"
    assert payload["meta"]["surfaces"]["allowedActions"]["status"] == "unavailable"
