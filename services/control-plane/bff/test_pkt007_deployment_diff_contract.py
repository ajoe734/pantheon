from __future__ import annotations

from typing import Any
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import (
    bff_error,
    extract_identity_stub,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.deployment.adapters import DeploymentReadSurfaceAdapter
from services.control_plane.bff.deployment.router import create_deployment_router
from services.control_plane.bff.personas.service import (
    _composed_surface_status,
    _dataset_surface_status,
    _snapshot_meta,
    utc_now,
)
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


def _make_client(store: Any) -> TestClient:
    router = create_deployment_router(
        queries=DeploymentReadSurfaceAdapter(store),
        commands=None,
        extract_identity=extract_identity_stub,
        require_read_role=require_read_role,
        require_operator_role=require_operator_role,
        bff_error=bff_error,
        utc_now=utc_now,
        page_slice=lambda items, token, size: (items, None),
        snapshot_meta=_snapshot_meta,
        dataset_surface_status=lambda dataset, **kw: _dataset_surface_status(dataset, read_store=store, **kw),
        composed_surface_status=_composed_surface_status,
        read_surface_meta=lambda *a, **kw: {},
        raise_if_read_surface_unavailable=lambda *a, **kw: None,
        aggregate_group_surface=lambda *a, **kw: {"status": "available"},
        split_csv_query=lambda val: val.split(",") if val else None,
        meta_staleness=lambda: None,
        stable_json_hash=lambda val: "hash",
        resolve_final_idempotency_key=lambda r, h: r or h or "key",
        reject_body_idempotency_key=lambda p: None,
        request_dry_run_requested=lambda *a, **kw: False,
        gov_bff_idempotency={},
        publish_event=lambda *a, **kw: "event-id",
        sse_buffers={},
        sse_subscribers={},
        gov_bff_action_command=lambda *a, **kw: {},
        deprecated_bff_path_response=lambda *a, **kw: None,
        sem_command_response=lambda *a, **kw: {},
        stream_generic_events=lambda *a, **kw: iter(()),
        surface_degradation_reason=lambda *a, **kw: None,
    )
    app = FastAPI()
    app.include_router(router)
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
