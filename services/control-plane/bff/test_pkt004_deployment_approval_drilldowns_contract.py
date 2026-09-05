from __future__ import annotations

import os
import sys
import tempfile

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.deployment.router import create_deployment_router
from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


OPERATOR_TOKEN = "Bearer op-2:operator"


def _page_slice(items, _page_token, _page_size):
    return items, None


def test_pkt004_deployment_approval_drilldowns_filters_follow_canonical_contract() -> None:
    with tempfile.TemporaryDirectory() as td:
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

        def _extract_identity(auth):
            return OperatorIdentity(operator_id="op-2", roles=["operator", "admin"])

        def _require_read_role(identity):
            pass

        app = FastAPI()

        dep_router = create_deployment_router(
            queries=store,
            extract_identity=_extract_identity,
            require_read_role=_require_read_role,
            require_operator_role=_require_read_role,
            bff_error=lambda status, code, msg, *a, **kw: HTTPException(status, {"code": str(code), "message": msg}),
            utc_now=lambda: "2026-05-01T00:00:00Z",
            page_slice=_page_slice,
            snapshot_meta=lambda _snapshot_at: {"snapshot_at": _snapshot_at},
            dataset_surface_status=lambda *_args, **_kwargs: {"status": "available"},
            composed_surface_status=lambda *_args, **_kwargs: {"status": "available"},
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

        gov_router = create_governance_router(
            read_surface=store,
            extract_identity=_extract_identity,
            require_read_role=_require_read_role,
            require_operator_role=_require_read_role,
            utc_now=lambda: "2026-05-01T00:00:00Z",
            read_surface_meta=lambda dataset, key, *, total=None, snapshot_at=None, **kwargs: {"total": total, "snapshot_at": snapshot_at, "surfaces": {}},
        )

        app.include_router(dep_router)
        app.include_router(gov_router)

        client = TestClient(app)

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
