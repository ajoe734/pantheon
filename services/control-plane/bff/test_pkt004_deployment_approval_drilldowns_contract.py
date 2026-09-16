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
from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.personas.service import (
    _composed_surface_status,
    _dataset_surface_status,
    _snapshot_meta,
    utc_now,
)
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


OPERATOR_TOKEN = "Bearer op-2:operator"


def _read_surface_meta(
    dataset: str,
    surface_key: str,
    *,
    snapshot_at: str,
    total: int | None = None,
    surface: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    meta: dict[str, Any] = {"snapshot_at": snapshot_at, "surfaces": {surface_key: surface or {}}}
    if total is not None:
        meta["total"] = total
    return meta


def _make_client(store: Any) -> TestClient:
    deployment_router = create_deployment_router(
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
        read_surface_meta=_read_surface_meta,
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
    governance_router = create_governance_router(
        read_surface=store,
        extract_identity=extract_identity_stub,
        require_read_role=require_read_role,
        require_operator_role=require_operator_role,
        bff_error=bff_error,
        utc_now=utc_now,
    )
    app = FastAPI()
    app.include_router(deployment_router)
    app.include_router(governance_router)
    return TestClient(app)


def test_pkt004_deployment_approval_drilldowns_filters_follow_canonical_contract() -> None:
    store = create_in_memory_read_surface_ports()
    plan_records = [
        {
            "id": "plan-F-042",
            "plan_id": "plan-F-042",
            "status": "approved",
            "capital_pool_id": "pool-main",
            "approval_decision_id": "approval-042",
        }
    ]
    decision_records = [
        {
            "id": "approval-042",
            "decision_id": "approval-042",
            "outcome": "approved",
            "state": "decided",
        }
    ]
    store.list_deployment_plans = lambda **kwargs: [
        plan
        for plan in plan_records
        if (not kwargs.get("status") or plan["status"] == kwargs["status"])
        and (
            not kwargs.get("capital_pool_id")
            or plan["capital_pool_id"] == kwargs["capital_pool_id"]
        )
    ]
    store.get_deployment_plan = lambda plan_id: next(
        (plan for plan in plan_records if plan["plan_id"] == plan_id), None
    )
    store.list_approval_decisions = lambda **kwargs: [
        decision
        for decision in decision_records
        if (not kwargs.get("outcome") or decision["outcome"] == kwargs["outcome"])
        and (not kwargs.get("state") or decision["state"] == kwargs["state"])
    ]
    store.get_approval_decision = lambda decision_id: next(
        (decision for decision in decision_records if decision["id"] == decision_id), None
    )
    client = _make_client(store)

    headers = {"Authorization": OPERATOR_TOKEN}

    plans = client.get(
        "/api/v1/deployment-plans?status=approved&capital_pool_id=pool-main",
        headers=headers,
    )
    assert plans.status_code == 200, plans.text
    plan_payload = plans.json()
    assert plan_payload["meta"]["total"] == 1
    assert plan_payload["data"][0]["plan_id"] == "plan-F-042"
    assert plan_payload["data"][0]["status"] == "approved"
    assert plan_payload["data"][0]["capital_pool_id"] == "pool-main"

    no_plans = client.get(
        "/api/v1/deployment-plans?status=rejected&capital_pool_id=pool-main",
        headers=headers,
    )
    assert no_plans.status_code == 200, no_plans.text
    assert no_plans.json()["meta"]["total"] == 0

    decisions = client.get(
        "/api/v1/approval-decisions?outcome=approved&state=decided",
        headers=headers,
    )
    assert decisions.status_code == 200, decisions.text
    decision_payload = decisions.json()
    assert decision_payload["meta"]["total"] == 1
    assert decision_payload["data"][0]["outcome"] == "approved"
    assert decision_payload["data"][0]["state"] == "decided"

    no_decisions = client.get(
        "/api/v1/approval-decisions?outcome=approved&state=pending",
        headers=headers,
    )
    assert no_decisions.status_code == 200, no_decisions.text
    assert no_decisions.json()["meta"]["total"] == 0

    plan_detail = client.get(
        "/api/v1/deployment-plans/plan-F-042",
        headers=headers,
    )
    assert plan_detail.status_code == 200, plan_detail.text
    plan_detail_payload = plan_detail.json()
    assert plan_detail_payload["data"]["plan_id"] == "plan-F-042"
    assert plan_detail_payload["data"]["approval_decision"]["id"] == "approval-042"

    decision_detail = client.get(
        "/api/v1/approval-decisions/approval-042",
        headers=headers,
    )
    assert decision_detail.status_code == 200, decision_detail.text
    assert decision_detail.json()["data"]["id"] == "approval-042"
